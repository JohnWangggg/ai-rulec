"""Patch an existing sing-box / Hiddify config with AI groups and rule-sets.

Hiddify regenerates its config from the subscription, so this is a safe
"annotate a copy" step: we insert selectors, local rule-sets and route rules,
validate with `sing-box check`, then swap the file atomically with a backup.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Any

from . import platforms


def find_singbox() -> str | None:
    found = platforms.which_exe("sing-box")
    if found:
        return found
    for candidate in (
        "/opt/homebrew/bin/sing-box",
        "/usr/local/bin/sing-box",
        "/Applications/Hiddify.app/Contents/Resources/sing-box",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def infer_singbox_target(config: dict[str, Any]) -> str | None:
    outbounds = [item for item in config.get("outbounds") or [] if isinstance(item, dict)]
    selectors = [item for item in outbounds if item.get("type") == "selector"]
    preferred = ("select", "proxy", "节点选择", "选择", "auto")
    for want in preferred:
        for item in selectors:
            if str(item.get("tag", "")).lower() == want:
                return str(item["tag"])
    for item in outbounds:
        if item.get("type") == "direct":
            return str(item.get("tag"))
    return str(selectors[0].get("tag")) if selectors else None


def _is_ai_outbound(item: dict[str, Any]) -> bool:
    tag = str(item.get("tag", ""))
    return tag == "AI" or tag.startswith("AI-")


def _is_ai_rule_set(item: dict[str, Any]) -> bool:
    return str(item.get("tag", "")).startswith("ai-")


def _is_ai_route_rule(rule: dict[str, Any]) -> bool:
    outbound = str(rule.get("outbound", ""))
    if outbound == "AI" or outbound.startswith("AI-"):
        return True
    return any(str(tag).startswith("ai-") for tag in rule.get("rule_set") or [])


def patch_config(
    config: dict[str, Any],
    services: list[str],
    *,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    rules_dir: str = "AI-Rules",
    absolute_paths: bool = False,
    path_base: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    mapping = mapping or {}
    patched = copy.deepcopy(config)

    outbounds = [item for item in patched.get("outbounds") or [] if isinstance(item, dict)]
    known_tags = {str(item.get("tag")) for item in outbounds if item.get("tag")}
    chosen = target or infer_singbox_target(patched)
    if not chosen:
        raise ValueError("config has no outbound to use as target; pass --target")
    if chosen not in known_tags:
        raise ValueError(f"target outbound not found: {chosen}")

    kept = [item for item in outbounds if not _is_ai_outbound(item)]
    removed_outbounds = len(outbounds) - len(kept)

    new_outbounds: list[dict[str, Any]] = []
    for service in services:
        member = mapping.get(service, chosen)
        if member not in known_tags:
            raise ValueError(f"mapping for {service} points to unknown outbound: {member}")
        new_outbounds.append(
            {
                "type": "selector",
                "tag": f"AI-{service}",
                "outbounds": [member],
                "default": member,
            }
        )
    new_outbounds.append(
        {
            "type": "selector",
            "tag": "AI",
            "outbounds": [f"AI-{service}" for service in services] + [chosen],
            "default": f"AI-{services[0]}" if services else chosen,
        }
    )

    route = patched.setdefault("route", {})
    rule_sets = [item for item in route.get("rule_set") or [] if isinstance(item, dict)]
    kept_rule_sets = [item for item in rule_sets if not _is_ai_rule_set(item)]
    removed_rule_sets = len(rule_sets) - len(kept_rule_sets)
    for service in services:
        path = f"{rules_dir}/{service}.json"
        if absolute_paths:
            path = (
                os.path.abspath(os.path.join(path_base, f"{service}.json"))
                if path_base
                else os.path.abspath(path)
            )
        path = platforms.path_for_config(path)
        kept_rule_sets.append(
            {
                "type": "local",
                "tag": f"ai-{service}",
                "format": "source",
                "path": path,
            }
        )

    rules = [rule for rule in route.get("rules") or [] if isinstance(rule, dict)]
    kept_rules = [rule for rule in rules if not _is_ai_route_rule(rule)]
    removed_rules = len(rules) - len(kept_rules)
    new_rules = [
        {"rule_set": [f"ai-{service}"], "outbound": f"AI-{service}"}
        for service in services
    ]

    patched["outbounds"] = kept + new_outbounds
    route["rule_set"] = kept_rule_sets
    route["rules"] = new_rules + kept_rules
    route.setdefault("final", chosen)

    report = {
        "target": chosen,
        "target_source": "explicit" if target else "inferred",
        "outbounds_removed": removed_outbounds,
        "outbounds_added": len(new_outbounds),
        "rule_sets_removed": removed_rule_sets,
        "rule_sets_added": len(services),
        "rules_removed": removed_rules,
        "rules_added": len(new_rules),
        "services": list(services),
        "rules_dir": rules_dir,
    }
    return patched, report


def _run_check(config_path: str, cwd: str) -> dict[str, Any]:
    binary = find_singbox()
    if not binary:
        return {"status": "skipped", "reason": "sing-box not found"}
    proc = subprocess.run(
        [binary, "check", "-c", os.path.abspath(config_path)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "returncode": proc.returncode,
        "stderr": proc.stderr.strip()[-1000:],
    }


def _delete_field(config: dict[str, Any], path: str, field: str) -> bool:
    parts = [part for part in path.split(".") if part]
    if parts and parts[-1] == field:
        parts = parts[:-1]
    node: Any = config
    for part in parts:
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\[(\d+)\]", part)
        if match:
            key, index = match.group(1), int(match.group(2))
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
            if not isinstance(node, list) or not (0 <= index < len(node)):
                return False
            node = node[index]
        else:
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
    if isinstance(node, dict) and field in node:
        node.pop(field)
        return True
    return False


def _sanitize_once(config: dict[str, Any], message: str) -> list[str]:
    """Strip one fork-only construct so upstream sing-box can keep validating.

    Hiddify ships a sing-box fork with `balancer` outbounds and fields such as
    `tolerance`/`tls_fragment`/`store_warp_config`. We remove exactly what the
    decoder complains about, preserving tags and references, then retry.
    """
    changed: list[str] = []

    field_match = re.search(
        r"([A-Za-z0-9_.\[\]]+): json: unknown field \"([^\"]+)\"",
        message,
    )
    if field_match:
        path, field = field_match.group(1), field_match.group(2)
        if _delete_field(config, path, field):
            changed.append(f"{path}.{field}" if not path.endswith(field) else path)

    unknown_types = set(re.findall(r"unknown outbound type: ([A-Za-z0-9_-]+)", message))
    if unknown_types:
        for index, item in enumerate(config.get("outbounds") or []):
            if isinstance(item, dict) and str(item.get("type")) in unknown_types:
                changed.append(f"outbounds[{index}].type={item.get('type')}")
                config["outbounds"][index] = {"type": "direct", "tag": item.get("tag")}

    return changed


def _check(config_path: str, cwd: str) -> dict[str, Any]:
    result = _run_check(config_path, cwd)
    if result["status"] != "fail":
        return result

    with open(config_path, encoding="utf-8") as fh:
        config = json.load(fh)
    sanitized: list[str] = []
    sanitized_path = f"{config_path}.sanitized"
    try:
        for _ in range(40):
            changed = _sanitize_once(config, str(result.get("stderr", "")))
            if not changed:
                break
            sanitized.extend(changed)
            with open(sanitized_path, "w", encoding="utf-8") as fh:
                json.dump(config, fh, ensure_ascii=False, indent=2)
            result = _run_check(sanitized_path, cwd)
            if result["status"] != "fail":
                break
        if sanitized:
            result["sanitized"] = sanitized
        return result
    finally:
        if os.path.exists(sanitized_path):
            os.remove(sanitized_path)


def discover_configs() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for candidate in platforms.singbox_config_roots():
        path = str(platforms.expand(candidate))
        if os.path.isfile(path):
            found.append({"path": path, "kind": "sing-box", "size": os.path.getsize(path)})
    return found


def apply_singbox(
    config_path: str,
    services: list[str],
    *,
    lists_dir: str | None = None,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    rules_dir: str = "AI-Rules",
    absolute_paths: bool = False,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    config_path = os.path.abspath(config_path)
    if not os.path.exists(config_path):
        raise FileNotFoundError(config_path)
    config_dir = os.path.dirname(config_path)

    with open(config_path, encoding="utf-8") as fh:
        original = json.load(fh)
    if not isinstance(original, dict):
        raise ValueError(f"{config_path}: expected a JSON object")

    lists_ready = bool(lists_dir) and all(
        os.path.exists(os.path.join(lists_dir, f"{service}.json")) for service in services
    )
    validate_against_lists = dry_run and lists_ready
    patched, report = patch_config(
        original,
        services,
        target=target,
        mapping=mapping,
        rules_dir=rules_dir,
        absolute_paths=absolute_paths or validate_against_lists,
        path_base=lists_dir if validate_against_lists else None,
    )
    result: dict[str, Any] = {"ok": True, "dry_run": dry_run, **report}
    result["config"] = config_path
    result["original_check"] = _check(config_path, config_dir)

    if lists_dir:
        target_dir = os.path.join(config_dir, report["rules_dir"])
        copied = 0
        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
            for service in services:
                source = os.path.join(lists_dir, f"{service}.json")
                if not os.path.exists(source):
                    continue
                shutil.copyfile(source, os.path.join(target_dir, f"{service}.json"))
                copied += 1
        result["rules_copied"] = copied

    staging = os.path.join(config_dir, f".ai-rulec-preview-{os.getpid()}.json")
    try:
        with open(staging, "w", encoding="utf-8") as fh:
            json.dump(patched, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        if dry_run and not lists_ready and not all(
            os.path.exists(os.path.join(config_dir, rules_dir, f"{service}.json"))
            for service in services
        ):
            check = {"status": "skipped", "reason": "rule-set files not present for dry-run"}
        else:
            check = _check(staging, config_dir)
        result["check"] = check
        if check["status"] == "fail":
            result["ok"] = False
            result["error"] = "sing-box check rejected the patched config"
            return result
        if dry_run:
            return result

        if backup:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = f"{config_path}.bak.{stamp}"
            shutil.copy2(config_path, backup_path)
            result["backup"] = backup_path

        platforms.atomic_replace(staging, config_path)
        result["applied"] = True
        return result
    finally:
        if os.path.exists(staging):
            os.remove(staging)
