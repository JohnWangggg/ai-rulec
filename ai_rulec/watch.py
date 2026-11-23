"""Watch FlClash's runtime config and repair the AI provider cache.

FlClash owns ``config.yaml``: on start/profile switch it rewrites the file from
the selected profile, hashing rule-provider paths into
``profiles/providers/<profile>/rules/<sha>``.  A crash or cache cleanup can
leave that file pointing at files that no longer exist.  This module keeps the
cache in sync and, when FlClash has dropped the AI providers entirely,
re-applies the managed profile so the next reload is correct.

It is deliberately a polling loop with stdlib only: no fsevents dependency and
no attempt to click the FlClash UI.  Starting FlClash remains a human action;
the reconciler only makes the files correct and reports that a restart is due.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator

import yaml

from . import defaults
from . import platforms
from .health import check_agents as _check_agents
from .health import load_agents as _load_agents
from .profile import (
    _runtime_config,
    _sha256,
    apply_profile,
    discover_profiles,
    doctor_profile,
    inspect_profile,
    pick_profile,
    repair_provider_cache,
)

DEFAULT_INTERVAL = 2.0
DEFAULT_STATE = ".ai-rulec-watch.json"
SKIPPED_ACTIONS = {"skipped", "cache_ok", "waiting_for_config"}
STALE_FLOOR = 30.0
# After this many waiting cycles the client clearly never reloaded config.yaml,
# so escalate from "waiting" to "restart the client".
WAITING_LIMIT = 3


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _load_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        platforms.atomic_replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


@contextlib.contextmanager
def _lock(home: Path) -> Iterator[bool]:
    lock = platforms.FileLock(home / ".ai-rulec-watch.lock")
    if not lock.acquire():
        yield False
        return
    try:
        yield True
    finally:
        lock.release()


def _port_from_config(config: Path | None) -> int | None:
    if config is None:
        return None
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("mixed-port", "port"):
        value = data.get(key)
        if isinstance(value, int) and 0 < value < 65536:
            return value
    return None


def _port_open(port: int | None, timeout: float = 0.35) -> bool | None:
    if port is None:
        return None
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _state_file(profile_file: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    config = _runtime_config(profile_file)
    home = config.parent if config else profile_file.parent
    return home / DEFAULT_STATE


def _parse_time(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return time.mktime(time.strptime(value, fmt))
        except ValueError:
            continue
    return None


def stale_after(interval: Any) -> float:
    """Seconds without a cycle after which the watcher counts as stale."""
    try:
        value = float(interval)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL
    if value <= 0:
        value = DEFAULT_INTERVAL
    return max(STALE_FLOOR, 3.0 * value)


def _record_seen(result: dict[str, Any], interval: float) -> dict[str, Any]:
    """Stamp liveness on every cycle, including skipped/unchanged ones.

    ``last_run`` keeps meaning "last action attempted", so a quiet watcher
    (skipping every cycle) still shows a moving ``last_seen`` without
    pretending to have acted.
    """
    state_path = result.get("state")
    if not state_path or result.get("dry_run"):
        return result
    state_file = Path(str(state_path))
    state = _load_state(state_file)
    seen = _now()
    state["last_seen"] = seen
    try:
        state["interval"] = float(interval)
    except (TypeError, ValueError):
        state["interval"] = DEFAULT_INTERVAL
    if result.get("action") not in SKIPPED_ACTIONS:
        state["last_run"] = seen
    try:
        _save_state(state_file, state)
    except OSError as exc:
        result.setdefault("warnings", []).append(
            f"state: {type(exc).__name__}: {exc}"
        )
    return result


# One background sweep per state file.  A sweep costs tens of seconds
# (seven agents x several endpoints x retries), far longer than the poll
# interval; running it inline froze the reconciler and made ``last_seen``
# look stale while the process was in fact healthy.
_HEALTH_JOBS: dict[str, dict[str, Any]] = {}


def _health_from_report(
    report: dict[str, Any], proxy_url: str | None
) -> dict[str, Any]:
    items = report.get("agents") or []
    return {
        "ok": all(item["status"] != "down" for item in items),
        "proxy": proxy_url or "direct",
        "summary": report.get("summary") or {},
        "down_agents": [i["name"] for i in items if i["status"] == "down"],
        "degraded_agents": [i["name"] for i in items if i["status"] == "degraded"],
        "agents": [
            {
                "name": i["name"],
                "status": i["status"],
                "reason": i.get("reason"),
                "checks": i.get("summary"),
            }
            for i in items
        ],
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _store_health(
    result: dict[str, Any],
    state: dict[str, Any],
    health: dict[str, Any],
    *,
    dry_run: bool,
    state_file: Path,
) -> dict[str, Any]:
    result["health"] = health
    if health.get("down_agents"):
        result["warnings"].append(
            "agents down: " + ", ".join(health["down_agents"])
        )
    if not dry_run:
        state["last_health"] = {k: v for k, v in health.items() if k != "checked_at"}
        state["last_health_at"] = health["checked_at"]
        try:
            _save_state(state_file, state)
        except OSError as exc:
            result["warnings"].append(f"health state: {type(exc).__name__}: {exc}")
    return health


def _agent_health_block(
    result: dict[str, Any],
    state: dict[str, Any],
    *,
    agents_path: str | None,
    agent_names: list[str] | None,
    enabled: bool,
    interval: float,
    timeout: float,
    dry_run: bool,
    state_file: Path,
) -> dict[str, Any] | None:
    """Refresh agent endpoint/CLI health on a slower cadence, off-loop."""
    if not enabled:
        return None
    key = str(state_file)
    job = _HEALTH_JOBS.get(key)
    if job is not None and job.get("done"):
        _HEALTH_JOBS.pop(key, None)
        if job.get("error"):
            result["health"] = {"error": job["error"]}
            result["warnings"].append(f"agent health skipped: {job['error']}")
            return result["health"]
        health = _health_from_report(job.get("report") or {}, job.get("proxy"))
        return _store_health(
            result, state, health, dry_run=dry_run, state_file=state_file
        )

    now = time.time()
    last = _parse_time(state.get("last_health_at"))
    cached = state.get("last_health")
    if (
        last is not None
        and interval > 0
        and now - last < interval
        and isinstance(cached, dict)
    ):
        result["health"] = {**cached, "cached": True}
        return result["health"]
    if job is not None:
        result["health"] = (
            {**cached, "pending": True} if isinstance(cached, dict) else {"pending": True}
        )
        return result["health"]

    path = defaults.resolve("agents", agents_path)
    port = result.get("port")
    proxy_url = f"http://127.0.0.1:{port}" if port else None
    if proxy_url and _port_open(port) is False:
        # The client is closed.  Sampling every agent over the direct route
        # would burn a timeout each and report failures that describe the
        # route, not the agents, so wait for the proxy to come back.
        health = {"ok": None, "skipped": f"{proxy_url} is not listening"}
        result["health"] = health
        result["warnings"].append(f"agent health skipped: {health['skipped']}")
        return health

    job = {"done": False, "report": None, "error": None, "proxy": proxy_url}

    def run() -> None:
        try:
            agents = _load_agents(path)
            job["report"] = _check_agents(
                agents,
                agent_names or list(agents),
                proxy_url=proxy_url,
                timeout=timeout,
            )
        except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
            job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            job["done"] = True

    _HEALTH_JOBS[key] = job
    threading.Thread(target=run, name="ai-rulec-health", daemon=True).start()
    result["health"] = (
        {**cached, "pending": True} if isinstance(cached, dict) else {"pending": True}
    )
    return result["health"]


def reconcile_once(
    profile_path: str,
    *,
    meta_path: str | None = None,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    rules_dir: str | None = None,
    mihomo_path: str | None = None,
    fix_cache: bool = True,
    reapply: bool = True,
    dry_run: bool = False,
    state_path: str | None = None,
    settle: float = 0.35,
    agents_path: str | None = None,
    agent_names: list[str] | None = None,
    check_agent_health: bool = False,
    agents_interval: float = 300.0,
    agents_timeout: float = 6.0,
    interval: float = DEFAULT_INTERVAL,
) -> dict[str, Any]:
    """Check one profile/config pair and record liveness for this cycle."""
    result = _reconcile_once(
        profile_path,
        meta_path=meta_path,
        target=target,
        mapping=mapping,
        rules_dir=rules_dir,
        mihomo_path=mihomo_path,
        fix_cache=fix_cache,
        reapply=reapply,
        dry_run=dry_run,
        state_path=state_path,
        settle=settle,
        agents_path=agents_path,
        agent_names=agent_names,
        check_agent_health=check_agent_health,
        agents_interval=agents_interval,
        agents_timeout=agents_timeout,
    )
    return _record_seen(result, interval)


def _reconcile_once(
    profile_path: str,
    *,
    meta_path: str | None = None,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    rules_dir: str | None = None,
    mihomo_path: str | None = None,
    fix_cache: bool = True,
    reapply: bool = True,
    dry_run: bool = False,
    state_path: str | None = None,
    settle: float = 0.35,
    agents_path: str | None = None,
    agent_names: list[str] | None = None,
    check_agent_health: bool = False,
    agents_interval: float = 300.0,
    agents_timeout: float = 6.0,
) -> dict[str, Any]:
    """Check one profile/config pair and repair what is safely repairable."""
    profile_file = Path(profile_path).expanduser().resolve()
    if not profile_file.is_file():
        raise ValueError(f"profile not found: {profile_file}")

    state_file = _state_file(profile_file, state_path)
    state = _load_state(state_file)
    info = inspect_profile(profile_file)
    config = _runtime_config(profile_file)
    config_hash = _sha256(config) if config else None
    profile_hash = _sha256(profile_file)

    result: dict[str, Any] = {
        "ok": True,
        "action": "none",
        "profile": str(profile_file),
        "state": str(state_file),
        "config": str(config) if config else None,
        "home": str(config.parent) if config else str(profile_file.parent),
        "target": target,
        "dry_run": dry_run,
        "profile_info": info,
        "profile_hash": profile_hash,
        "config_hash": config_hash,
        "port": _port_from_config(config),
        "port_open": _port_open(_port_from_config(config)),
        "restart_required": False,
        "warnings": [],
        "errors": [],
    }

    health = _agent_health_block(
        result,
        state,
        agents_path=agents_path,
        agent_names=agent_names,
        enabled=check_agent_health,
        interval=agents_interval,
        timeout=agents_timeout,
        dry_run=dry_run,
        state_file=state_file,
    )

    if not info.get("managed"):
        # FlClash can rewrite the selected profile from its subscription,
        # dropping the AI groups we added.  Treat that as a re-apply target,
        # not as a profile we should ignore.
        if not reapply:
            result["action"] = "skipped"
            result["reason"] = "profile has no AI-* groups (reapply off)"
            return result
        if not meta_path:
            result["action"] = "config_missing_ai"
            result["ok"] = False
            result["errors"].append(
                "profile has no AI-* groups and --meta is missing"
            )
            return result

    if config and settle > 0 and not dry_run:
        time.sleep(settle)
        settled_hash = _sha256(config)
        if settled_hash != config_hash:
            result["action"] = "unstable"
            result["config_hash"] = settled_hash
            result["warnings"].append("config.yaml is being rewritten; retrying next cycle")
            return result

    unchanged = (
        info.get("managed") is True
        and state.get("profile_hash") == profile_hash
        and state.get("config_hash") == config_hash
        and state.get("last_ok") is True
        and state.get("last_action")
        not in {"reapplied", "waiting_for_config", "config_missing_ai"}
    )
    if unchanged:
        result["action"] = "skipped"
        result["reason"] = "no change since last successful reconcile"
        return result

    if rules_dir:
        rules = Path(rules_dir).expanduser().resolve()
    elif meta_path:
        rules = Path(meta_path).expanduser().resolve().parent / "AI-Rules"
    else:
        rules = (config.parent / "AI-Rules") if config else None

    doctor = doctor_profile(profile_file, rules_dir=str(rules) if rules else None)
    result["cache"] = doctor.get("cache")
    providers = (doctor.get("cache") or {}).get("providers") or []

    if providers and info.get("managed"):
        cache_ok = bool((doctor.get("cache") or {}).get("ok"))
        if cache_ok:
            result["action"] = "cache_ok"
        elif fix_cache:
            if dry_run:
                result["action"] = "would_repair"
                result["warnings"].append("cache has missing/mismatched providers")
            else:
                repaired = repair_provider_cache(
                    profile_file,
                    rules_dir=str(rules) if rules else None,
                    dry_run=False,
                    backup=True,
                )
                result["repair"] = repaired
                result["action"] = "cache_repaired" if repaired.get("ok") else "cache_failed"
                result["ok"] = bool(repaired.get("ok"))
                if not result["ok"]:
                    result["errors"].extend(
                        repaired.get("errors") or ["provider cache repair failed"]
                    )
        else:
            result["action"] = "cache_issue"
            result["ok"] = False
            result["errors"].append("provider cache incomplete; --fix-cache is off")
    elif reapply:
        if not meta_path:
            result["action"] = "config_missing_ai"
            result["ok"] = False
            result["errors"].append("config has no AI providers and --meta is missing")
        elif state.get("applied_profile_hash") == profile_hash:
            result["action"] = "waiting_for_config"
            cycles = int(state.get("waiting_cycles") or 0) + 1
            state["waiting_cycles"] = cycles
            result["waiting_cycles"] = cycles
            if cycles >= WAITING_LIMIT:
                result["warnings"].append(
                    f"config has no AI providers after {cycles} cycles: the client "
                    "applied profile on disk but never reloaded it; restart the "
                    "client (FlClash) to activate the AI rules"
                )
            else:
                result["warnings"].append(
                    "config has no AI providers; profile was already reapplied, "
                    "waiting for the client to regenerate config.yaml"
                )
        elif dry_run:
            result["action"] = "would_reapply"
        else:
            applied = apply_profile(
                str(profile_file),
                meta_path,
                target=target,
                mapping=mapping,
                rules_dir=str(rules) if rules else None,
                replace_existing=True,
                preserve_existing_proxies=True,
                rules_position="top",
                backup=True,
                copy_rules=True,
                repair_cache=True,
                verify_mihomo=mihomo_path,
                dry_run=False,
            )
            result["apply"] = applied
            result["action"] = "reapplied"
            result["ok"] = not (
                (applied.get("mihomo") or {}).get("ok") is False
            )
            if not result["ok"]:
                result["errors"].append("mihomo validation failed after reapply")
    else:
        result["action"] = "config_missing_ai"
        result["ok"] = False
        result["errors"].append(
            "config has no AI providers; pass --reapply (and --meta) to repair"
        )

    if result["action"] in {
        "cache_repaired",
        "reapplied",
        "would_repair",
        "would_reapply",
        "config_missing_ai",
        "waiting_for_config",
    }:
        result["restart_required"] = True
    if result["restart_required"] and result["port_open"] is False:
        result["restart_reason"] = "proxy port is closed"
    elif result["restart_required"]:
        result["restart_reason"] = "profile or provider cache changed; reload FlClash"

    if (
        not dry_run
        and result["ok"]
        and result["action"] not in {"cache_failed", "unstable"}
    ):
        new_profile_hash = _sha256(profile_file) or profile_hash
        new_config_hash = _sha256(config) if config else None
        state.update(
            {
                "profile": str(profile_file),
                "config": str(config) if config else None,
                "profile_hash": new_profile_hash,
                "config_hash": new_config_hash,
                "last_action": result["action"],
                "last_ok": result["ok"],
            }
        )
        if result["action"] == "reapplied":
            state["applied_profile_hash"] = new_profile_hash
        if result["action"] != "waiting_for_config":
            state.pop("waiting_cycles", None)
        try:
            _save_state(state_file, state)
        except OSError as exc:
            result["warnings"].append(f"state: {type(exc).__name__}: {exc}")
    return result


def _active_profile() -> Path | None:
    """Resolve the client's selected profile the same way ``--latest`` does."""
    picked = pick_profile(discover_profiles().get("profiles") or [])
    return Path(str(picked["path"])).resolve() if picked else None


def watch(
    profile_path: str | None,
    *,
    follow_active: bool = False,
    meta_path: str | None = None,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    rules_dir: str | None = None,
    mihomo_path: str | None = None,
    fix_cache: bool = True,
    reapply: bool = True,
    dry_run: bool = False,
    state_path: str | None = None,
    settle: float = 0.35,
    interval: float = DEFAULT_INTERVAL,
    once: bool = False,
    json_output: bool = True,
    agents_path: str | None = None,
    agent_names: list[str] | None = None,
    check_agent_health: bool = False,
    agents_interval: float = 300.0,
    agents_timeout: float = 6.0,
) -> dict[str, Any] | None:
    """Reconcile once, or poll forever.  Returns the first result when once.

    ``follow_active`` re-resolves the client's selected profile every cycle, so
    a profile switch in FlClash moves the watcher instead of leaving it pinned
    to the subscription that happened to be active at launch.
    """
    profile_file = (
        Path(profile_path).expanduser().resolve() if profile_path else None
    )
    if profile_file is None:
        if not follow_active:
            raise ValueError("watch: pass a profile path or --latest")
        profile_file = _active_profile()
    if profile_file is None:
        raise ValueError("watch: no profiles discovered")
    home = _state_file(profile_file, state_path).parent
    last_printed: tuple[str, bool, bool] | None = None

    while True:
        if follow_active:
            current = _active_profile()
            if current is not None and current != profile_file:
                profile_file = current
                home = _state_file(profile_file, state_path).parent
        with _lock(home) as acquired:
            if not acquired:
                result: dict[str, Any] = {
                    "ok": True,
                    "action": "locked",
                    "profile": str(profile_file),
                    "reason": "another ai-rulec watch cycle is running",
                }
            else:
                try:
                    result = reconcile_once(
                        str(profile_file),
                        meta_path=meta_path,
                        target=target,
                        mapping=mapping,
                        rules_dir=rules_dir,
                        mihomo_path=mihomo_path,
                        fix_cache=fix_cache,
                        reapply=reapply,
                        dry_run=dry_run,
                        state_path=state_path,
                        settle=settle,
                        agents_path=agents_path,
                        agent_names=agent_names,
                        check_agent_health=check_agent_health,
                        agents_interval=agents_interval,
                        agents_timeout=agents_timeout,
                        interval=interval,
                    )
                except (OSError, ValueError, yaml.YAMLError) as exc:
                    result = {
                        "ok": False,
                        "action": "error",
                        "profile": str(profile_file),
                        "state": str(_state_file(profile_file, state_path)),
                        "dry_run": dry_run,
                        "errors": [f"{type(exc).__name__}: {exc}"],
                    }
                    _record_seen(result, interval)
        if once:
            return result
        marker = (
            result.get("action", "error"),
            bool(result.get("ok")),
            int(result.get("waiting_cycles") or 0) >= WAITING_LIMIT,
        )
        if marker != last_printed:
            if json_output:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print(_format_result(result), flush=True)
            last_printed = marker
        time.sleep(max(0.2, interval))


def _format_result(result: dict[str, Any]) -> str:
    cache = result.get("cache") or {}
    summary = cache.get("summary") or {}
    bits = [
        time.strftime("%H:%M:%S"),
        result.get("action", "?"),
        f"ok={result.get('ok')}",
    ]
    if summary:
        bits.append(
            f"cache={summary.get('ok', 0)}/{summary.get('managed', 0)}"
        )
    health = result.get("health") or {}
    health_summary = health.get("summary") or {}
    if health_summary:
        bits.append(
            "agents={}/{}/{}".format(
                health_summary.get("ok", 0),
                health_summary.get("degraded", 0),
                health_summary.get("down", 0),
            )
        )
        if health.get("down_agents"):
            bits.append("down=" + ",".join(health["down_agents"][:3]))
    elif health.get("skipped"):
        bits.append("agents=skipped")
    elif health.get("error"):
        bits.append("agents=error")
    if result.get("restart_required"):
        bits.append("restart=required")
    if result.get("errors"):
        bits.append("errors=" + "; ".join(result["errors"][:2]))
    return " ".join(bits)
