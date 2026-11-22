from __future__ import annotations

import copy
import os
from typing import Any


BUILTIN_TARGETS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"}
AUTO_HINTS = ["自动选择", "Auto", "auto", "url-test", "自动", "自动切换"]
SELECT_HINTS = [
    "手动选择",
    "PROXY",
    "Proxy",
    "proxy",
    "节点选择",
    "Select",
    "GLOBAL",
    "Global",
]


def infer_target(data: dict[str, Any]) -> str | None:
    """Pick the profile's main egress group: url-test first, then selectors."""
    groups = [
        group
        for group in (data.get("proxy-groups") or [])
        if isinstance(group, dict) and group.get("name")
    ]
    names = [str(group["name"]) for group in groups]
    auto = {
        str(group["name"])
        for group in groups
        if str(group.get("type", "")).lower()
        in {"url-test", "fallback", "load-balance"}
    }
    for hint in AUTO_HINTS:
        if hint in names and hint in auto:
            return hint
    for hint in SELECT_HINTS:
        if hint in names:
            return hint
    if auto:
        return sorted(auto)[0]
    for name in names:
        lower = name.lower()
        if any(keyword in lower for keyword in ("select", "proxy", "选择", "节点")):
            return name
    return None


def _names(items: Any) -> set[str]:
    names: set[str] = set()
    for item in items or []:
        if isinstance(item, dict) and item.get("name"):
            names.add(str(item["name"]))
    return names


def _target_exists(target: str, proxies: set[str], groups: set[str]) -> bool:
    return target in BUILTIN_TARGETS or target in proxies or target in groups


def parse_target_map(items: list[str] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in items or []:
        for entry in item.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if "=" not in entry:
                raise ValueError(f"--map expects service=target, got {entry!r}")
            service, target = (part.strip() for part in entry.split("=", 1))
            if not service or not target:
                raise ValueError(f"--map expects service=target, got {entry!r}")
            key = service.lower()
            if key in mapping:
                raise ValueError(f"duplicate --map for service {service!r}")
            mapping[key] = target
    return mapping


def _is_generated_rule(rule: str) -> bool:
    parts = [part.strip() for part in rule.split(",")]
    return (
        len(parts) == 3
        and parts[0] == "RULE-SET"
        and parts[2] == f"AI-{parts[1]}"
    )


def merge_profile(
    base: dict[str, Any],
    meta: dict[str, Any],
    *,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    providers_dir: str | None = None,
    output_dir: str | None = None,
    replace_existing: bool = False,
    preserve_existing_proxies: bool = True,
    rules_position: str = "before-match",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if rules_position not in {"before-match", "top"}:
        raise ValueError("rules_position must be 'before-match' or 'top'")
    mapping = {key.lower(): value for key, value in (mapping or {}).items()}
    merged = copy.deepcopy(base)
    base_proxies = _names(merged.get("proxies"))
    base_groups = _names(merged.get("proxy-groups"))
    target_source = "explicit"
    if target is None:
        target = infer_target(merged) or "DIRECT"
        target_source = "inferred" if target != "DIRECT" else "fallback"
    if not _target_exists(target, base_proxies, base_groups):
        raise ValueError(
            f"target {target!r} is not a builtin, proxy, or proxy-group in the base config"
        )
    for service, service_target in mapping.items():
        if not _target_exists(service_target, base_proxies, base_groups):
            raise ValueError(
                f"mapping for {service!r} points to unknown target {service_target!r}"
            )
    for target_name in {target, *mapping.values()}:
        if target_name == "AI" or target_name.startswith("AI-"):
            raise ValueError(
                f"target {target_name!r} is reserved for ai-rulec groups"
            )

    meta_providers = meta.get("rule-providers") or {}
    if not isinstance(meta_providers, dict):
        raise ValueError("meta rule-providers must be a mapping")
    providers: dict[str, Any] = {}
    for name, provider in meta_providers.items():
        if not isinstance(provider, dict):
            raise ValueError(f"meta provider {name!r} must be a mapping")
        item = copy.deepcopy(provider)
        path = item.get("path")
        if path and not os.path.isabs(str(path)):
            if not providers_dir:
                raise ValueError(
                    f"provider {name!r} has a relative path; pass providers_dir"
                )
            target_path = os.path.join(
                providers_dir, os.path.basename(str(path))
            )
            target_path = os.path.relpath(
                target_path, output_dir or providers_dir
            )
            target_path = target_path.replace(os.sep, "/")
            if not target_path.startswith(".") and not os.path.isabs(target_path):
                target_path = "./" + target_path
            item["path"] = target_path
        providers[str(name)] = item

    base_providers = merged.get("rule-providers") or {}
    if not isinstance(base_providers, dict):
        raise ValueError("base rule-providers must be a mapping")
    collisions = sorted(set(providers) & set(base_providers))
    if collisions and not replace_existing:
        raise ValueError(f"rule-provider name collision: {', '.join(collisions)}")
    for name in collisions:
        old = base_providers[name]
        old_path = str(old.get("path", "")) if isinstance(old, dict) else ""
        if "AI-Rules" not in old_path:
            raise ValueError(
                f"provider {name!r} exists and is not ai-rulec-generated; "
                "refusing to replace it"
            )

    meta_groups = meta.get("proxy-groups") or []
    if not isinstance(meta_groups, list):
        raise ValueError("meta proxy-groups must be a list")
    group_entries = [
        group
        for group in (merged.get("proxy-groups") or [])
        if isinstance(group, dict)
    ]
    existing_groups = {
        str(group.get("name")): group
        for group in group_entries
        if group.get("name")
    }

    service_groups: list[dict[str, Any]] = []
    service_names: list[str] = []
    replaced_groups: list[str] = []
    for group in meta_groups:
        if not isinstance(group, dict):
            raise ValueError("meta proxy-group entries must be mappings")
        name = str(group.get("name", ""))
        if not name.startswith("AI-"):
            continue
        service = name[3:]
        service_names.append(service)
        existing = existing_groups.get(name)
        if existing is not None:
            if not replace_existing:
                raise ValueError(f"proxy-group name collision: {name}")
            if existing.get("type") != "select":
                raise ValueError(
                    f"proxy-group {name!r} exists but is not a select group"
                )
            replaced_groups.append(name)
            old_proxies = [
                str(proxy)
                for proxy in (existing.get("proxies") or [])
                if isinstance(proxy, str)
            ]
            if service not in mapping and preserve_existing_proxies:
                if old_proxies in (["DIRECT"], []) and target != "DIRECT":
                    # Older ai-rulec runs defaulted to DIRECT; without this
                    # migration a re-apply would keep AI traffic on the
                    # blocked direct route forever.
                    proxies = [target, "DIRECT"]
                else:
                    proxies = old_proxies or [target, "DIRECT"]
            else:
                proxies = [mapping.get(service, target)]
                if "DIRECT" not in proxies:
                    proxies.append("DIRECT")
        else:
            proxies = [mapping.get(service, target)]
            if "DIRECT" not in proxies:
                proxies.append("DIRECT")
        service_groups.append(
            {"name": name, "type": "select", "proxies": proxies}
        )

    if not service_groups:
        raise ValueError("meta config has no AI-<service> proxy-groups")
    unknown_mappings = sorted(set(mapping) - set(service_names))
    if unknown_mappings:
        raise ValueError(
            f"mapping targets unknown services: {', '.join(unknown_mappings)}"
        )

    parent = {
        "name": "AI",
        "type": "select",
        "proxies": [str(group["name"]) for group in service_groups] + ["DIRECT"],
    }
    existing_parent = existing_groups.get("AI")
    if existing_parent is not None:
        if not replace_existing:
            raise ValueError("proxy-group name collision: AI")
        if existing_parent.get("type") != "select":
            raise ValueError("proxy-group 'AI' exists but is not a select group")
        replaced_groups.append("AI")

    meta_rules = [
        str(rule)
        for rule in (meta.get("rules") or [])
        if not str(rule).upper().startswith("MATCH,")
    ]
    base_rules = [str(rule) for rule in (merged.get("rules") or [])]
    removed_rules: list[str] = []
    if replace_existing:
        removed_rules = [
            rule for rule in base_rules if _is_generated_rule(rule)
        ]
        base_rules = [
            rule for rule in base_rules if not _is_generated_rule(rule)
        ]
    match_index = next(
        (
            index
            for index, rule in enumerate(base_rules)
            if rule.upper().startswith("MATCH,")
        ),
        None,
    )
    if match_index is None:
        base_rules.append("MATCH,DIRECT")
        match_index = len(base_rules) - 1

    if rules_position == "top":
        combined = meta_rules + base_rules
    else:
        combined = base_rules[:match_index] + meta_rules + base_rules[match_index:]
    rules: list[str] = []
    seen: set[str] = set()
    for rule in combined:
        if rule not in seen:
            seen.add(rule)
            rules.append(rule)

    new_groups = {str(group["name"]): group for group in service_groups}
    new_groups["AI"] = parent
    existing_group_names = set(existing_groups)
    merged_groups: list[Any] = []
    for group in merged.get("proxy-groups") or []:
        if not isinstance(group, dict):
            merged_groups.append(group)
            continue
        name = str(group.get("name", ""))
        merged_groups.append(new_groups.get(name, group))
    for group in service_groups + [parent]:
        if str(group["name"]) not in existing_group_names:
            merged_groups.append(group)

    merged.setdefault("mode", "rule")
    merged["rule-providers"] = {**base_providers, **providers}
    merged["proxy-groups"] = merged_groups
    merged["rules"] = rules

    provider_replaced = len(collisions)
    report = {
        "target": target,
        "target_source": target_source,
        "mapping": mapping,
        "providers_added": len(providers) - provider_replaced,
        "providers_replaced": provider_replaced,
        "groups_added": len(
            [
                group
                for group in service_groups + [parent]
                if str(group["name"]) not in existing_group_names
            ]
        ),
        "groups_replaced": len(set(replaced_groups)),
        "rules_added": len(meta_rules),
        "rules_removed": len(removed_rules),
        "rules_total": len(rules),
        "rules_position": rules_position,
        "services": service_names,
    }
    return merged, report
