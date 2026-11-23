"""Keep a sing-box/Hiddify config patched across subscription rewrites.

Hiddify regenerates `current-config.json` from the subscription. This watcher
detects that rewrite (our `AI-*` outbounds disappear), re-applies the patch,
and records a small state file so an unchanged config is a no-op.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime
from typing import Any

from .platforms import FileLock
from .singbox import apply_singbox, discover_configs

from . import platforms

DEFAULT_INTERVAL = 5.0


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_file(config_path: str, state_path: str | None) -> str:
    if state_path:
        return os.path.abspath(os.path.expanduser(state_path))
    return os.path.join(os.path.dirname(config_path), ".ai-rulec-singbox-watch.json")


def _load_state(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path: str, state: dict[str, Any]) -> None:
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    platforms.atomic_replace(tmp, path)


def _has_ai(config: dict[str, Any], services: list[str]) -> bool:
    outbounds = config.get("outbounds") or []
    tags = {str(item.get("tag")) for item in outbounds if isinstance(item, dict)}
    if "AI" not in tags or not all(f"AI-{service}" in tags for service in services):
        return False
    route = config.get("route") or {}
    rule_tags = {
        str(item.get("tag"))
        for item in route.get("rule_set") or []
        if isinstance(item, dict)
    }
    return all(f"ai-{service}" in rule_tags for service in services)


def reconcile(
    config_path: str,
    services: list[str],
    *,
    lists_dir: str | None = None,
    rules_dir: str = "AI-Rules",
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    backup: bool = True,
    dry_run: bool = False,
    force: bool = False,
    state_path: str | None = None,
) -> dict[str, Any]:
    config_path = os.path.abspath(os.path.expanduser(config_path))
    if not os.path.exists(config_path):
        return {
            "ok": False,
            "action": "config_missing",
            "config": config_path,
            "errors": [f"config not found: {config_path}"],
        }

    state_file = _state_file(config_path, state_path)
    state = _load_state(state_file)
    config_hash = _sha256(config_path)

    try:
        with open(config_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "action": "config_invalid",
            "config": config_path,
            "errors": [str(exc)],
        }

    result: dict[str, Any] = {
        "ok": True,
        "config": config_path,
        "config_hash": config_hash,
        "services": len(services),
        "dry_run": dry_run,
        "action": "skipped",
    }

    if _has_ai(config, services) and not force:
        result["action"] = "unchanged"
        return result
    if _has_ai(config, services) and force:
        result["action"] = "reapplied"
    else:
        result["action"] = "would_apply" if dry_run else "applied"

    try:
        report = apply_singbox(
            config_path,
            services,
            lists_dir=lists_dir,
            rules_dir=rules_dir,
            target=target,
            mapping=mapping,
            dry_run=dry_run,
            backup=backup,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result.update({"ok": False, "action": "apply_failed", "errors": [str(exc)]})
        return result

    result["apply"] = {
        key: report.get(key)
        for key in (
            "ok",
            "target",
            "target_source",
            "outbounds_added",
            "rule_sets_added",
            "rules_added",
            "rules_copied",
            "backup",
            "check",
        )
    }
    result["ok"] = bool(report.get("ok"))
    if not result["ok"]:
        result["action"] = "apply_failed"
        result["errors"] = [report.get("error", "apply failed")]
        return result

    if not dry_run:
        result["config_hash"] = _sha256(config_path)
        state.update(
            {
                "config": config_path,
                "config_hash": result["config_hash"],
                "last_action": result["action"],
                "last_ok": True,
                "last_run": _now(),
            }
        )
        try:
            _save_state(state_file, state)
        except OSError as exc:
            result["warnings"] = [f"could not write state: {exc}"]
    return result


def watch(
    config_path: str | None,
    services: list[str],
    *,
    lists_dir: str | None = None,
    rules_dir: str = "AI-Rules",
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    interval: float = DEFAULT_INTERVAL,
    once: bool = False,
    dry_run: bool = False,
    backup: bool = True,
    force: bool = False,
    state_path: str | None = None,
    json_output: bool = True,
) -> int:
    path = config_path
    if not path:
        found = discover_configs()
        if not found:
            print("singbox-watch: no sing-box/Hiddify config found")
            return 1
        path = found[0]["path"]

    lock_name = hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:12]
    lock_path = os.path.join(tempfile.gettempdir(), f"ai-rulec-singbox-watch-{lock_name}.lock")
    lock = FileLock(lock_path)
    if not lock.acquire():
        print("singbox-watch: another watcher is running")
        return 1

    try:
        while True:
            result = reconcile(
                path,
                services,
                lists_dir=lists_dir,
                rules_dir=rules_dir,
                target=target,
                mapping=mapping,
                backup=backup,
                dry_run=dry_run,
                force=force,
                state_path=state_path,
            )
            if json_output:
                print(json.dumps(result, ensure_ascii=False))
            else:
                stamp = datetime.now().strftime("%H:%M:%S")
                check = (result.get("apply") or {}).get("check") or {}
                print(
                    f"{stamp} {result.get('action')} ok={result.get('ok')} "
                    f"services={result.get('services')} check={check.get('status', '-')}"
                )
            if once:
                return 0 if result.get("ok") else 1
            time.sleep(max(1.0, interval))
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()
