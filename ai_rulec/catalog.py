from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .models import Rule
from .normalize import BROAD_ASNS, classify_role, is_shared_apex, is_shared_domain
from .sources import DOMAIN_KINDS


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _alias_map(services: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, cfg in services.items():
        mapping[key.lower()] = key
        for alias in cfg.get("aliases", []):
            mapping[str(alias).lower()] = key
    return mapping


def _known_domains(services: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, cfg in services.items():
        for domain in cfg.get("known_domains", []):
            pairs.append((str(domain).lower().strip("."), key))
    return sorted(pairs, key=lambda item: len(item[0]), reverse=True)


def _process_map(services: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for key, cfg in services.items():
        for process in cfg.get("known_processes", []):
            mapping[str(process).lower()] = key
    return mapping


def _owner_from_domain(
    domain: str,
    aliases: dict[str, str],
    known: list[tuple[str, str]],
) -> str | None:
    for alias in sorted(aliases, key=len, reverse=True):
        # A bare brand ("meta", "chatgpt") must anchor at the first label:
        # matching it anywhere would claim unrelated TLDs such as "shop.meta".
        # Aliases with a dot ("github-copilot.io") are real suffixes.
        if "." in alias:
            if domain == alias or domain.endswith("." + alias):
                return aliases[alias]
        elif domain == alias or domain.startswith(alias + "."):
            return aliases[alias]
    for known_domain, key in known:
        if domain == known_domain or domain.endswith("." + known_domain):
            return key
    return None


def _service_from_hint(hint: str | None, aliases: dict[str, str]) -> str | None:
    if not hint:
        return None
    return aliases.get(hint.lower())


def _add_source(target: list[dict[str, Any]], rule: Rule) -> None:
    key = (rule.source, rule.path)
    if any((item["source"], item["path"]) == key for item in target):
        return
    target.append(
        {
            "source": rule.source,
            "path": rule.path,
            "lineno": rule.lineno,
            "hint": rule.service_hint,
            "set": rule.set_name,
        }
    )


def _is_stale(source: str, source_meta: dict[str, Any]) -> bool:
    files = source_meta.get(source, {}).get("files", {})
    return any(bool(item.get("stale")) for item in files.values())


def build_catalog(
    rules: list[Rule],
    services: dict[str, Any],
    source_meta: dict[str, Any],
    probes: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    aliases = _alias_map(services)
    known = _known_domains(services)
    process_map = _process_map(services)

    domains: dict[str, dict[str, Any]] = {}
    processes: list[dict[str, Any]] = []
    ip_asns: list[dict[str, Any]] = []
    ip_cidrs: list[dict[str, Any]] = []
    regex_rules: list[dict[str, Any]] = []

    def entry_for(domain: str) -> dict[str, Any]:
        return domains.setdefault(
            domain,
            {
                "domain": domain,
                "roles": Counter(),
                "services": {},
                "sources": [],
                "processes": [],
                "requires_mitm": False,
                "flags": set(),
            },
        )

    for rule in rules:
        if rule.kind in DOMAIN_KINDS:
            domain = rule.value
            entry = entry_for(domain)
            _add_source(entry["sources"], rule)
            role = classify_role(domain)
            entry["roles"][role] += 1
            if rule.requires_mitm:
                entry["requires_mitm"] = True

            hint_key = _service_from_hint(rule.service_hint, aliases)
            domain_owner = _owner_from_domain(domain, aliases, known)
            owner = hint_key or domain_owner
            if hint_key and domain_owner and hint_key != domain_owner:
                entry["flags"].add("hint_domain_mismatch")
            if owner:
                slot = entry["services"].setdefault(
                    owner, {"hints": set(), "sources": set(), "roles": Counter()}
                )
                if rule.service_hint:
                    slot["hints"].add(rule.service_hint)
                slot["sources"].add(rule.source)
                slot["roles"][role] += 1

        elif rule.kind == "process":
            hint_key = _service_from_hint(rule.service_hint, aliases)
            owner = hint_key or process_map.get(rule.value.lower())
            processes.append(
                {
                    "value": rule.value,
                    "service": owner,
                    "source": rule.source,
                    "path": rule.path,
                    "lineno": rule.lineno,
                }
            )
            if owner:
                for domain in services.get(owner, {}).get("known_domains", []):
                    target = domains.get(str(domain).lower())
                    if target is not None:
                        target["processes"].append(rule.value)

        elif rule.kind == "ip_asn":
            hint_key = _service_from_hint(rule.service_hint, aliases)
            owner = hint_key
            ip_asns.append(
                {
                    "value": rule.value,
                    "service": owner,
                    "source": rule.source,
                    "path": rule.path,
                    "lineno": rule.lineno,
                }
            )

        elif rule.kind == "url_regex":
            hint_key = _service_from_hint(rule.service_hint, aliases)
            regex_rules.append(
                {
                    "value": rule.value,
                    "service": hint_key,
                    "source": rule.source,
                    "path": rule.path,
                    "lineno": rule.lineno,
                    "requires_mitm": True,
                }
            )

        elif rule.kind == "ip_cidr":
            hint_key = _service_from_hint(rule.service_hint, aliases)
            owner = hint_key
            ip_cidrs.append(
                {
                    "value": rule.value,
                    "service": owner,
                    "source": rule.source,
                    "path": rule.path,
                    "lineno": rule.lineno,
                }
            )

    # Finalize domain entries: shared / multi-service / confidence / flags.
    for domain, entry in domains.items():
        services_for_domain = sorted(entry["services"])
        entry["services"] = {
            service: {
                "hints": sorted(slot["hints"]),
                "sources": sorted(slot["sources"]),
                "roles": dict(slot["roles"]),
            }
            for service, slot in sorted(entry["services"].items())
        }
        entry["roles"] = dict(entry["roles"])
        primary = _owner_from_domain(domain, aliases, known)
        if not primary and services_for_domain:
            def _score(service: str) -> tuple[int, int, int, str]:
                slot = entry["services"][service]
                role_bonus = 1 if ("api" in slot["roles"] or "web" in slot["roles"]) else 0
                return (len(slot["hints"]), len(slot["sources"]), role_bonus, service)
            primary = max(services_for_domain, key=_score)
        entry["primary_service"] = primary
        probe = (probes or {}).get(domain)
        entry["probe_status"] = probe.get("status") if probe else None
        entry["probe"] = (
            {
                "status": probe.get("status"),
                "checked_at": probe.get("checked_at"),
                "dns_ok": bool(probe.get("dns", {}).get("ok")),
                "tls_ok": bool(probe.get("tls", {}).get("ok")),
                "hostname_match": bool(probe.get("tls", {}).get("hostname_match")),
                "http_status": probe.get("http", {}).get("status"),
                "parked": bool(probe.get("http", {}).get("parked")),
            }
            if probe
            else None
        )
        if entry["probe_status"] == "dns_fail":
            entry["flags"].add("probe_dns_fail")
        elif entry["probe_status"] == "tls_fail":
            entry["flags"].add("probe_tls_fail")
        elif entry["probe_status"] == "tls_mismatch":
            entry["flags"].add("probe_tls_mismatch")
        elif entry["probe_status"] == "parked":
            entry["flags"].add("probe_parked")
        elif entry["probe_status"] == "probe_error":
            entry["flags"].add("probe_error")
        entry["sources"] = sorted(
            entry["sources"], key=lambda item: (item["source"], item["path"])
        )
        entry["processes"] = sorted(set(entry["processes"]))

        if is_shared_domain(domain) or entry["roles"].get("shared"):
            entry["flags"].add("shared")
        if is_shared_apex(domain):
            entry["flags"].add("shared_apex")
        if "shared" in entry["flags"]:
            first = domain.split(".")[0].lower()
            if not any(
                first == alias
                or first.startswith(alias + "-")
                or first.startswith(alias)
                for alias in aliases
            ):
                entry["flags"].add("shared_generic")
        if len(services_for_domain) > 1:
            entry["flags"].add("multi_service")
        if not services_for_domain:
            entry["flags"].add("unknown_service")
        if entry["requires_mitm"]:
            entry["flags"].add("mitm")
        if any(src.get("set") == "reject" for src in entry["sources"]):
            entry["flags"].add("reject_set")
        if any(_is_stale(src["source"], source_meta) for src in entry["sources"]):
            entry["flags"].add("stale_source")

        confidence = 0.5
        if len(services_for_domain) == 1:
            confidence += 0.15
        if any(src["source"] not in {"loyalsoldier"} for src in entry["sources"]):
            confidence += 0.1
        if entry["roles"].get("api") or entry["roles"].get("web"):
            confidence += 0.1
        if "shared" in entry["flags"]:
            confidence -= 0.2
        if "multi_service" in entry["flags"]:
            confidence -= 0.15
        if "unknown_service" in entry["flags"]:
            confidence -= 0.2
        entry["confidence"] = round(max(0.0, min(1.0, confidence)), 3)
        entry["flags"] = sorted(entry["flags"])

    # Service summaries.
    service_summary: dict[str, Any] = {}
    for key, cfg in services.items():
        service_domains = sorted(
            domain
            for domain, entry in domains.items()
            if key in entry["services"]
        )
        service_processes = sorted(
            {item["value"] for item in processes if item["service"] == key}
        )
        service_asns = sorted(
            {item["value"] for item in ip_asns if item["service"] == key}
        )
        service_cidrs = sorted(
            {item["value"] for item in ip_cidrs if item["service"] == key}
        )
        roles = Counter()
        quality = Counter()
        confidences: list[float] = []
        for domain in service_domains:
            entry = domains[domain]
            roles.update(entry["roles"])
            confidences.append(entry["confidence"])
            for flag in entry["flags"]:
                quality[flag] += 1
        probe_statuses = Counter()
        probe_issues: list[str] = []
        for domain in service_domains:
            status = domains[domain].get("probe_status")
            if status:
                probe_statuses[status] += 1
                if status != "ok":
                    probe_issues.append(domain)
        quality["broad_asn"] = len([asn for asn in service_asns if asn in BROAD_ASNS])
        score = min(1.0, len(service_domains) / 10.0) * 0.7
        score += 0.15 if service_processes else 0.0
        score += 0.15 if service_asns or service_cidrs else 0.0
        service_summary[key] = {
            "display_name": cfg.get("display_name", key),
            "domain_count": len(service_domains),
            "process_count": len(service_processes),
            "ip_asn_count": len(service_asns),
            "ip_cidr_count": len(service_cidrs),
            "roles": dict(roles),
            "sources": sorted(
                {
                    source
                    for domain in service_domains
                    for source in domains[domain].get("services", {}).get(key, {}).get("sources", [])
                }
            ),
            "quality": dict(quality),
            "probe": {
                "ok": probe_statuses.get("ok", 0),
                "issues": len(probe_issues),
                "by_status": dict(sorted(probe_statuses.items())),
                "issue_domains": probe_issues[:20],
            },
            "avg_confidence": round(sum(confidences) / len(confidences), 3)
            if confidences
            else 0.0,
            "heuristic_score": round(score, 3),
            "domains": service_domains,
            "processes": service_processes,
            "ip_asns": service_asns,
            "ip_cidrs": service_cidrs,
            "url_regex_count": len(
                [item for item in regex_rules if item["service"] == key]
            ),
        }

    multi_service = {
        domain: sorted(entry["services"])
        for domain, entry in sorted(domains.items())
        if len(entry["services"]) > 1
    }
    shared_domains = sorted(
        domain for domain, entry in domains.items() if "shared" in entry["flags"]
    )
    unknown_domains = sorted(
        domain for domain, entry in domains.items() if "unknown_service" in entry["flags"]
    )
    unknown_processes = sorted(
        {item["value"] for item in processes if not item["service"]}
    )
    unknown_asns = sorted({item["value"] for item in ip_asns if not item["service"]})

    conflicts = {
        "multi_service": multi_service,
        "shared_domains": shared_domains,
        "broad_asns": sorted(
            {
                item["value"]
                for item in ip_asns
                if item["value"] in BROAD_ASNS and item["service"]
            }
        ),
        "mitm_domains": sorted(
            domain for domain, entry in domains.items() if "mitm" in entry["flags"]
        ),
        "mitm_rules": sorted({item["value"] for item in regex_rules}),
        "reject_set_domains": sorted(
            domain for domain, entry in domains.items() if "reject_set" in entry["flags"]
        ),
        "hint_domain_mismatch": sorted(
            domain
            for domain, entry in domains.items()
            if "hint_domain_mismatch" in entry["flags"]
        ),
        "unknown": {
            "domains": unknown_domains,
            "processes": unknown_processes,
            "ip_asns": unknown_asns,
        },
    }

    probe_counts = Counter(
        entry.get("probe_status")
        for entry in domains.values()
        if entry.get("probe_status")
    )
    summary = {
        "services": len(services),
        "domains": len(domains),
        "processes": len(processes),
        "ip_asns": len(ip_asns),
        "ip_cidrs": len(ip_cidrs),
        "url_regex": len(regex_rules),
        "shared_domains": len(shared_domains),
        "multi_service_domains": len(multi_service),
        "unknown_domains": len(unknown_domains),
        "probe": {
            "total": sum(probe_counts.values()),
            "ok": probe_counts.get("ok", 0),
            "issues": sum(
                count for status, count in probe_counts.items() if status != "ok"
            ),
            "by_status": dict(sorted(probe_counts.items())),
        },
        "stale_sources": sorted(
            name
            for name, meta in source_meta.items()
            if any(bool(item.get("stale")) for item in meta.get("files", {}).values())
        ),
    }

    catalog = {
        "schema_version": 1,
        "generated_at": _now(),
        "source_meta": source_meta,
        "probe_summary": summary["probe"],
        "domains": dict(sorted(domains.items())),
        "processes": sorted(processes, key=lambda item: (item["value"], item["source"])),
        "ip_asns": sorted(ip_asns, key=lambda item: (item["value"], item["source"])),
        "ip_cidrs": sorted(ip_cidrs, key=lambda item: (item["value"], item["source"])),
        "url_regex": sorted(
            regex_rules, key=lambda item: (item["value"], item["source"])
        ),
    }
    coverage = {
        "schema_version": 1,
        "generated_at": catalog["generated_at"],
        "summary": summary,
        "services": service_summary,
        "conflicts": conflicts,
    }
    return catalog, coverage
