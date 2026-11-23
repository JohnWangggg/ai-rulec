from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

from . import defaults
from .health import check_agents, load_agents, proxy_skip
from .profile import (
    _runtime_config,
    inspect_profile,
    inspect_provider_cache,
    list_backups,
)
from .watch import (
    DEFAULT_INTERVAL,
    _load_state,
    _parse_time,
    _port_from_config,
    _port_open,
    _state_file,
    stale_after,
)

DEFAULT_AGENTS = str(defaults.bundled("agents"))


def profile_status(
    profile_path: str | Path,
    *,
    state_path: str | None = None,
    agents_path: str | None = None,
    agent_names: list[str] | None = None,
    proxy_url: str | None = None,
    check_agents_run: bool = False,
    timeout: float = 6.0,
    workers: int = 6,
) -> dict[str, Any]:
    """Read-only health summary for one FlClash/mihomo profile."""
    profile_file = Path(profile_path).expanduser().resolve()
    if not profile_file.is_file():
        raise ValueError(f"profile not found: {profile_file}")

    info = inspect_profile(profile_file)
    config = _runtime_config(profile_file)
    cache = inspect_provider_cache(profile_file)
    state_file = _state_file(profile_file, state_path)
    state = _load_state(state_file) if state_file.is_file() else {}
    port = _port_from_config(config)
    port_open = _port_open(port)
    backups = list_backups(profile_file)

    interval = state.get("interval")
    try:
        watch_interval = float(interval)
    except (TypeError, ValueError):
        watch_interval = DEFAULT_INTERVAL
    if watch_interval <= 0:
        watch_interval = DEFAULT_INTERVAL
    seen_ts = _parse_time(state.get("last_seen"))
    if seen_ts is None:
        # Older state files only know last_run; still better than nothing.
        seen_ts = _parse_time(state.get("last_run"))
    watch_age = None if seen_ts is None else max(0.0, time.time() - seen_ts)
    watch_stale = bool(
        watch_age is not None and watch_age > stale_after(watch_interval)
    )

    rules_dir = Path(cache.get("rules_dir") or "")
    rules_files = (
        len(list(rules_dir.glob("*.yaml"))) + len(list(rules_dir.glob("*.yml")))
        if cache.get("rules_dir")
        else 0
    )

    warnings: list[str] = []
    errors: list[str] = []
    if not info.get("managed"):
        warnings.append("profile has no AI-Rules provider paths")
    if not info.get("ai_groups"):
        warnings.append("profile has no AI-* proxy groups")
    if not cache.get("ok"):
        summary = cache.get("summary") or {}
        if info.get("managed") and not summary.get("managed"):
            warnings.append(
                "runtime config has no AI providers; "
                "the client has not reloaded the profile yet"
            )
        else:
            warnings.append(
                "provider cache incomplete: "
                f"{summary.get('missing', 0)} missing, "
                f"{summary.get('mismatch', 0)} mismatch, "
                f"{summary.get('other', 0)} other"
            )
    if port_open is False:
        warnings.append(f"proxy port {port} is not listening")
    if not state:
        warnings.append("no watch state yet")
    elif state.get("last_ok") is False:
        warnings.append(
            f"last watch action failed: {state.get('last_action', 'unknown')}"
        )
    if watch_stale:
        warnings.append(
            f"watch is stale: no cycle for {int(watch_age or 0)}s "
            f"(limit {int(stale_after(watch_interval))}s)"
        )
    if cache.get("error"):
        errors.append(str(cache["error"]))
    if config is None:
        errors.append("runtime config.yaml not found")

    health: dict[str, Any] | None = None
    if check_agents_run:
        skip = proxy_skip(proxy_url)
        if skip:
            health = {"ok": None, "skipped": skip, "proxy": proxy_url}
            warnings.append(f"agent health skipped: {skip}")
        else:
            health = _agent_health(
                agents_path,
                agent_names,
                proxy_url=proxy_url,
                timeout=timeout,
                workers=workers,
            )
        summary = health.get("summary") or {}
        if health.get("error"):
            warnings.append(f"agent health: {health['error']}")
        elif summary.get("down"):
            warnings.append(
                f"agents down: {summary['down']} "
                f"({', '.join(health.get('down_agents') or [])})"
            )
        if not health.get("skipped") and summary.get("degraded"):
            warnings.append(
                f"agents degraded: {summary['degraded']} "
                f"({', '.join(health.get('degraded_agents') or [])})"
            )

    healthy = bool(
        info.get("managed")
        and info.get("ai_groups")
        and cache.get("ok")
        and port_open is not False
    )
    return {
        "ok": healthy and not errors,
        "profile": str(profile_file),
        "profile_info": info,
        "config": str(config) if config else None,
        "port": port,
        "port_open": port_open,
        "rules_dir": cache.get("rules_dir"),
        "rules_files": rules_files,
        "cache": cache,
        "watch": {
            "state": str(state_file),
            "exists": state_file.is_file(),
            "last_run": state.get("last_run"),
            "last_seen": state.get("last_seen"),
            "interval": watch_interval,
            "age_seconds": None if watch_age is None else int(watch_age),
            "stale": watch_stale,
            "last_action": state.get("last_action"),
            "last_ok": state.get("last_ok"),
        },
        "backups": {
            "profile": len(backups.get("profile", [])),
            "rules": len(backups.get("rules", [])),
        },
        "health": health,
        "warnings": warnings,
        "errors": errors,
    }


def _agent_health(
    agents_path: str | None,
    names: list[str] | None,
    *,
    proxy_url: str | None = None,
    timeout: float = 6.0,
    workers: int = 6,
) -> dict[str, Any]:
    """Run agent checks without letting a broken config take status down."""
    path = defaults.resolve("agents", agents_path)
    if not Path(path).is_file():
        return {"error": f"agents config not found: {path}"}
    try:
        agents = load_agents(path)
        report = check_agents(
            agents,
            names or list(agents),
            proxy_url=proxy_url,
            timeout=timeout,
            workers=workers,
        )
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    items = report.get("agents") or []
    report["down_agents"] = [item["name"] for item in items if item["status"] == "down"]
    report["degraded_agents"] = [
        item["name"] for item in items if item["status"] == "degraded"
    ]
    report["ok"] = all(item["status"] != "down" for item in items)
    return report
