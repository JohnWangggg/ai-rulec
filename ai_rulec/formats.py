"""Alternative client formats: sing-box rule-sets and Surge rule lists.

The catalog stays the single source of truth; every writer here consumes the
same strict-filtered service summary that the mihomo emitter uses, so all
clients get identical coverage.
"""

from __future__ import annotations

import json
from typing import Any

SINGBOX_VERSION = 3

# Surge rejects anything outside this set inside a rule set file.
SURGE_PREFIXES = (
    "DOMAIN,",
    "DOMAIN-SUFFIX,",
    "DOMAIN-KEYWORD,",
    "PROCESS-NAME,",
    "IP-ASN,",
    "IP-CIDR,",
    "IP-CIDR6,",
    "USER-AGENT,",
)


def _cidr_type(value: str) -> str:
    return "IP-CIDR6" if ":" in value else "IP-CIDR"


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _dedupe(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


def singbox_rules(summary: dict[str, Any], asn_mode: str = "keep") -> list[dict[str, Any]]:
    """Build sing-box rule objects.

    sing-box has no ASN matcher, so ASNs can only be kept when the caller
    expands them into CIDRs; here they are dropped and reported by the emitter.
    """
    rules: list[dict[str, Any]] = []
    domains = list(summary.get("domains", []))
    if domains:
        rules.append({"domain_suffix": domains})
    processes = list(summary.get("processes", []))
    if processes:
        rules.append({"process_name": processes})
    cidrs = list(summary.get("ip_cidrs", []))
    if cidrs:
        rules.append({"ip_cidr": cidrs})
    return rules


def singbox_document(
    service: str, summary: dict[str, Any], asn_mode: str = "keep"
) -> dict[str, Any]:
    return {"version": SINGBOX_VERSION, "rules": singbox_rules(summary, asn_mode)}


def singbox_header(service: str, summary: dict[str, Any], generated_at: str) -> str:
    dropped = len(summary.get("ip_asns", []))
    note = f"; ip_asns dropped: {dropped} (sing-box has no ASN matcher)" if dropped else ""
    return (
        f"// ai-rulec — {service} sing-box rule-set\n"
        f"// generated: {generated_at}{note}\n"
        f"// domains: {len(summary.get('domains', []))}; "
        f"processes: {len(summary.get('processes', []))}; "
        f"cidrs: {len(summary.get('ip_cidrs', []))}\n"
    )


def surge_lines(
    summary: dict[str, Any], behavior: str = "classical", asn_mode: str = "keep"
) -> list[str]:
    lines = [f"DOMAIN-SUFFIX,{domain}" for domain in summary.get("domains", [])]
    if behavior == "classical":
        lines.extend(f"PROCESS-NAME,{name}" for name in summary.get("processes", []))
        if asn_mode != "drop":
            lines.extend(f"IP-ASN,{asn}" for asn in summary.get("ip_asns", []))
        lines.extend(
            f"{_cidr_type(cidr)},{cidr},no-resolve"
            for cidr in summary.get("ip_cidrs", [])
        )
    return _dedupe(lines)


def surge_list_text(
    service: str,
    summary: dict[str, Any],
    generated_at: str,
    behavior: str = "classical",
    asn_mode: str = "keep",
) -> str:
    lines = surge_lines(summary, behavior, asn_mode)
    header = [
        f"# NAME: ai-rulec {service}",
        "# AUTHOR: ai-rulec",
        f"# UPDATED: {generated_at}",
        f"# TOTAL: {len(lines)}",
        "",
    ]
    return "\n".join(header + lines) + "\n"


def surge_fragment(
    services: list[str],
    target: str,
    rules_prefix: str = "AI-Rules",
    mapping: dict[str, str] | None = None,
) -> str:
    """Return the [Proxy Group] + [Rule] lines a Surge profile needs.

    Surge modules cannot define policies, so these lines must be pasted into
    the profile itself (or applied by the `ai-rulec surge` subcommand).
    """
    mapping = mapping or {}
    groups = [
        f"AI-{service} = select, {mapping.get(service, target)}, DIRECT"
        for service in services
    ]
    groups.append(
        "AI = select, " + ", ".join(f"AI-{s}" for s in services) + ", DIRECT"
    )
    rules = [
        f"RULE-SET,{rules_prefix}/{service}.list,AI-{service}" for service in services
    ]
    return "\n".join(
        [
            "# ai-rulec — paste into your Surge profile",
            "# Provider lists live next to the profile: <profile dir>/AI-Rules/*.list",
            "[Proxy Group]",
            *groups,
            "",
            "[Rule]",
            *rules,
            "",
        ]
    )


def surge_profile(
    services: list[str],
    target: str = "PROXY",
    proxy_name: str = "PROXY",
    mapping: dict[str, str] | None = None,
    rules_prefix: str = "AI-Rules",
    generated_at: str = "",
) -> str:
    """A complete, verifiable Surge profile.

    `surge-cli --check` only accepts whole profiles, so the shipped artifact is
    a runnable profile whose only real policy is a DIRECT placeholder; replace
    [Proxy] with your own nodes and keep the AI group/rule block.
    """
    mapping = mapping or {}
    groups = [
        f"AI-{service} = select, {mapping.get(service, target)}, DIRECT"
        for service in services
    ]
    groups.append(
        "AI = select, " + ", ".join(f"AI-{service}" for service in services) + ", DIRECT"
    )
    rules = [
        f"RULE-SET,{rules_prefix}/{service}.list,AI-{service}" for service in services
    ]
    return "\n".join(
        [
            "# ai-rulec — Surge profile with AI services split per provider",
            f"# generated: {generated_at}",
            "# Replace the placeholder [Proxy] entry with your own policies.",
            "",
            "[General]",
            "loglevel = notify",
            "dns-server = system",
            "",
            "[Proxy]",
            f"{proxy_name} = direct",
            "",
            "[Proxy Group]",
            *groups,
            "",
            "[Rule]",
            *rules,
            f"FINAL,{target}",
            "",
        ]
    )


def singbox_route_fragment(
    services: list[str],
    target: str,
    mapping: dict[str, str] | None = None,
    rules_dir: str = "AI-Rules",
    format: str = "source",
    final: str | None = "AI",
) -> dict[str, Any]:
    mapping = mapping or {}
    outbounds = [
        {
            "type": "selector",
            "tag": f"AI-{service}",
            "outbounds": _unique([mapping.get(service, target), "direct"]),
        }
        for service in services
    ]
    outbounds.append(
        {
            "type": "selector",
            "tag": "AI",
            "outbounds": [f"AI-{s}" for s in services] + ["direct"],
        }
    )
    rule_set = [
        {
            "type": "local",
            "tag": service,
            "format": format,
            "path": f"./{rules_dir}/{service}.json",
        }
        for service in services
    ]
    rules = [
        {"rule_set": [service], "outbound": f"AI-{service}"} for service in services
    ]
    route: dict[str, Any] = {"rule_set": rule_set, "rules": rules}
    if final:
        route["final"] = final
    return {"outbounds": outbounds, "route": route}


def tower_document(
    services: list[str],
    summaries: dict[str, dict[str, Any]],
    target: str = "DIRECT",
    mapping: dict[str, str] | None = None,
    asn_mode: str = "keep",
    include_all_proxies: bool = True,
) -> dict[str, Any]:
    """A self-contained Clash document Tower can import as a rule scheme.

    Tower's Clash reader rejects ``type: file`` providers (the sandbox cannot
    read loose files), so every rule set ships inline under ``payload``. Groups
    carry ``include-all-proxies`` so a rule-only import binds Tower's own node
    pool instead of falling back to DIRECT only.
    """
    mapping = mapping or {}
    groups: list[dict[str, Any]] = []
    providers: dict[str, dict[str, Any]] = {}
    rules: list[str] = []
    emitted: list[str] = []
    for service in services:
        summary = summaries.get(service)
        if not summary:
            continue
        payload = surge_lines(summary, "classical", asn_mode)
        if not payload:
            continue
        emitted.append(service)
        providers[service] = {
            "type": "inline",
            "behavior": "classical",
            "payload": payload,
        }
        group: dict[str, Any] = {
            "name": f"AI-{service}",
            "type": "select",
            "proxies": _unique([mapping.get(service, target)]),
        }
        if include_all_proxies:
            group["include-all-proxies"] = True
        groups.append(group)
        rules.append(f"RULE-SET,{service},AI-{service}")
    if not groups:
        raise ValueError("tower format needs at least one service with rules")
    groups.append(
        {
            "name": "AI",
            "type": "select",
            "proxies": [f"AI-{service}" for service in emitted] + ["DIRECT"],
        }
    )
    return {
        "mixed-port": 7890,
        "mode": "rule",
        "log-level": "info",
        "proxy-groups": groups,
        "rule-providers": providers,
        "rules": rules + ["MATCH,AI"],
    }


def infer_surge_target(text: str) -> str | None:
    """Pick an existing Surge policy that comfortably routes traffic.

    Preference: a select group named Proxy/PROXY/Proxy2, else AIGC, else the
    first select group that is not an AI group.
    """
    section: list[str] | None = None
    groups: list[tuple[str, list[str]]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section != "proxy group" or not line or line.startswith("#"):
            continue
        name, _, rest = line.partition("=")
        parts = [p.strip() for p in rest.split(",")]
        if not parts or parts[0] != "select":
            continue
        groups.append((name.strip(), parts[1:]))
    preferred = ("proxy", "proxy2", "aigc", "节点选择")
    for want in preferred:
        for name, _ in groups:
            if name.lower() == want:
                return name
    for name, members in groups:
        if name.startswith("AI-") or name == "AI":
            continue
        if "DIRECT" in members:
            return name
    return groups[0][0] if groups else None


def dump_singbox(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
