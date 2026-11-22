from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import yaml

from .formats import (
    dump_singbox,
    singbox_document,
    singbox_header,
    singbox_route_fragment,
    surge_fragment,
    surge_list_text,
    surge_profile,
)
from .normalize import BROAD_ASNS

FORMATS = ("mihomo", "sing-box", "surge")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def _cidr_type(value: str) -> str:
    return "IP-CIDR6" if ":" in value else "IP-CIDR"


def _provider_lines(
    service: str,
    summary: dict[str, Any],
    behavior: str,
    asn_mode: str = "keep",
) -> list[str]:
    lines = [f"DOMAIN-SUFFIX,{domain}" for domain in summary.get("domains", [])]
    if behavior == "classical":
        lines.extend(
            f"PROCESS-NAME,{process}" for process in summary.get("processes", [])
        )
        if asn_mode != "drop":
            lines.extend(f"IP-ASN,{asn}" for asn in summary.get("ip_asns", []))
        lines.extend(
            f"{_cidr_type(cidr)},{cidr},no-resolve"
            for cidr in summary.get("ip_cidrs", [])
        )
    # Keep deterministic order: domains first, then process/IP, dedupe preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            unique.append(line)
    return unique


def _write_provider(
    path: str,
    service: str,
    summary: dict[str, Any],
    behavior: str,
    generated_at: str,
    note: str = "",
    asn_mode: str = "keep",
) -> None:
    lines = _provider_lines(service, summary, behavior, asn_mode)
    header = [
        f"# ai-rulec P0 — {service} ({behavior})",
        f"# generated: {generated_at}",
        f"# domains: {len(summary.get('domains', []))}; "
        f"processes: {len(summary.get('processes', []))}; "
        f"ip_asns: {len(summary.get('ip_asns', []))}; "
        f"ip_cidrs: {len(summary.get('ip_cidrs', []))}",
        "# source: see catalog.json source_meta; review flags before production use",
    ]
    if note:
        header.append(note)
    header.append("")
    body = _dump_yaml({"payload": lines})
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + body)


def _strict_summary(
    service: str,
    summary: dict[str, Any],
    catalog: dict[str, Any],
    strict: bool,
    probe_gate: str,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    if not strict:
        return summary, {"domains": [], "ip_asns": [], "probe_domains": []}
    gate_flags: set[str] = set()
    if probe_gate in ("dns", "both"):
        gate_flags.add("probe_dns_fail")
    if probe_gate in ("mismatch", "both"):
        gate_flags.add("probe_tls_mismatch")
    excluded_domains: list[str] = []
    probe_domains: list[str] = []
    domains: list[str] = []
    for domain in summary.get("domains", []):
        flags = set(catalog.get("domains", {}).get(domain, {}).get("flags", []))
        primary = catalog.get("domains", {}).get(domain, {}).get("primary_service")
        if flags & {"shared_generic", "unknown_service", "reject_set"}:
            excluded_domains.append(domain)
        elif primary and primary != service:
            excluded_domains.append(domain)
        elif gate_flags and flags & gate_flags:
            excluded_domains.append(domain)
            probe_domains.append(domain)
        else:
            domains.append(domain)
    excluded_asns = [a for a in summary.get("ip_asns", []) if a in BROAD_ASNS]
    asns = [a for a in summary.get("ip_asns", []) if a not in BROAD_ASNS]
    filtered = dict(summary)
    filtered["domains"] = domains
    filtered["ip_asns"] = asns
    return filtered, {
        "domains": excluded_domains,
        "ip_asns": excluded_asns,
        "probe_domains": probe_domains,
    }


def emit_all(
    catalog: dict[str, Any],
    coverage: dict[str, Any],
    services: dict[str, Any],
    outdir: str,
    strict: bool = True,
    probes: dict[str, Any] | None = None,
    probes_path: str | None = None,
    probe_gate: str = "both",
    asn_mode: str = "keep",
    formats: tuple[str, ...] = ("mihomo",),
) -> dict[str, Any]:
    if asn_mode not in ("keep", "drop"):
        raise ValueError("asn_mode must be 'keep' or 'drop'")
    unknown = [fmt for fmt in formats if fmt not in FORMATS]
    if unknown:
        raise ValueError(f"unknown formats: {', '.join(unknown)}")
    generated_at = catalog.get("generated_at") or datetime.now(timezone.utc).isoformat()
    providers_dir = os.path.join(outdir, "AI-Rules")
    singbox_dir = os.path.join(outdir, "singbox", "AI-Rules")
    surge_dir = os.path.join(outdir, "surge", "AI-Rules")
    for target in (
        providers_dir if "mihomo" in formats else None,
        singbox_dir if "sing-box" in formats else None,
        surge_dir if "surge" in formats else None,
    ):
        if target:
            os.makedirs(target, exist_ok=True)

    artifacts: dict[str, str] = {}
    provider_entries: dict[str, Any] = {}
    rule_lines: list[str] = []
    excluded: dict[str, dict[str, list[str]]] = {}
    service_names: list[str] = []

    for service in sorted(services):
        summary = coverage["services"].get(service)
        if not summary:
            continue
        summary, skipped = _strict_summary(
            service, summary, catalog, strict, probe_gate
        )
        if skipped["domains"] or skipped["ip_asns"] or skipped["probe_domains"]:
            excluded[service] = skipped
        note = (
            f"# strict: excluded {len(skipped['domains'])} domains "
            f"({len(skipped['probe_domains'])} probe-gated), "
            f"{len(skipped['ip_asns'])} broad ASNs"
            if strict
            else "# raw: includes shared/unknown/reject-set domains"
        )
        service_names.append(service)
        if "mihomo" in formats:
            classical_path = os.path.join(providers_dir, f"{service}.yaml")
            domain_path = os.path.join(providers_dir, f"{service}.domain.yaml")
            _write_provider(
                classical_path,
                service,
                summary,
                "classical",
                generated_at,
                note,
                asn_mode,
            )
            _write_provider(
                domain_path, service, summary, "domain", generated_at, note, asn_mode
            )
            artifacts[os.path.relpath(classical_path, outdir)] = _sha256(classical_path)
            artifacts[os.path.relpath(domain_path, outdir)] = _sha256(domain_path)

            provider_entries[service] = {
                "type": "file",
                "behavior": "classical",
                "format": "yaml",
                "path": f"./AI-Rules/{service}.yaml",
            }
            provider_entries[f"{service}-domain"] = {
                "type": "file",
                "behavior": "domain",
                "format": "yaml",
                "path": f"./AI-Rules/{service}.domain.yaml",
            }
            rule_lines.append(f"RULE-SET,{service},AI-{service}")

        if "sing-box" in formats:
            sb_path = os.path.join(singbox_dir, f"{service}.json")
            with open(sb_path, "w", encoding="utf-8") as fh:
                fh.write(singbox_header(service, summary, generated_at))
                fh.write(dump_singbox(singbox_document(service, summary, asn_mode)))
            artifacts[os.path.relpath(sb_path, outdir)] = _sha256(sb_path)

        if "surge" in formats:
            surge_path = os.path.join(surge_dir, f"{service}.list")
            with open(surge_path, "w", encoding="utf-8") as fh:
                fh.write(
                    surge_list_text(service, summary, generated_at, "classical", asn_mode)
                )
            artifacts[os.path.relpath(surge_path, outdir)] = _sha256(surge_path)

    if "sing-box" in formats:
        singbox_config: dict[str, Any] = {
            "log": {"level": "info"},
            "inbounds": [
                {
                    "type": "mixed",
                    "tag": "mixed-in",
                    "listen": "127.0.0.1",
                    "listen_port": 7890,
                }
            ],
            "outbounds": [{"type": "direct", "tag": "direct"}],
        }
        fragment = singbox_route_fragment(
            service_names, "direct", rules_dir="AI-Rules", final="direct"
        )
        singbox_config["outbounds"].extend(fragment["outbounds"])
        singbox_config["route"] = fragment["route"]
        sb_config_path = os.path.join(outdir, "singbox", "config.json")
        with open(sb_config_path, "w", encoding="utf-8") as fh:
            fh.write(dump_singbox(singbox_config))
        artifacts[os.path.relpath(sb_config_path, outdir)] = _sha256(sb_config_path)

    if "surge" in formats:
        surge_fragment_path = os.path.join(outdir, "surge", "fragment.conf")
        with open(surge_fragment_path, "w", encoding="utf-8") as fh:
            fh.write(surge_fragment(service_names, "Proxy"))
        artifacts[os.path.relpath(surge_fragment_path, outdir)] = _sha256(
            surge_fragment_path
        )
        surge_profile_path = os.path.join(outdir, "surge", "profile.conf")
        with open(surge_profile_path, "w", encoding="utf-8") as fh:
            fh.write(
                surge_profile(
                    service_names, "PROXY", generated_at=generated_at
                )
            )
        artifacts[os.path.relpath(surge_profile_path, outdir)] = _sha256(
            surge_profile_path
        )

    if "mihomo" in formats:
        service_groups = [
            {
                "name": f"AI-{service}",
                "type": "select",
                "proxies": ["DIRECT"],
            }
            for service in service_names
        ]
        ai_group = {
            "name": "AI",
            "type": "select",
            "proxies": [f"AI-{service}" for service in service_names] + ["DIRECT"],
        }
        proxy_groups = service_groups + [ai_group]

        meta = {
            "mode": "rule",
            "log-level": "info",
            "rule-providers": provider_entries,
            "proxy-groups": proxy_groups,
            "rules": rule_lines + ["MATCH,DIRECT"],
        }
        meta_path = os.path.join(outdir, "mihomo-meta.yaml")
        with open(meta_path, "w", encoding="utf-8") as fh:
            fh.write(
                "\n".join(
                    [
                        "# ai-rulec P1 — mihomo rule-provider fragment",
                        "# Merge rule-providers/proxy-groups/rules into your config, or use mihomo-config.yaml.",
                        f"# generated: {generated_at}",
                        "",
                    ]
                )
                + _dump_yaml(meta)
            )
        artifacts["mihomo-meta.yaml"] = _sha256(meta_path)

        config = {
            "mixed-port": 7890,
            "allow-lan": False,
            "mode": "rule",
            "log-level": "info",
            "proxy-groups": proxy_groups,
            "rule-providers": provider_entries,
            "rules": rule_lines + ["MATCH,DIRECT"],
        }
        config_path = os.path.join(outdir, "mihomo-config.yaml")
        with open(config_path, "w", encoding="utf-8") as fh:
            fh.write(
                "\n".join(
                    [
                        "# ai-rulec P1 — minimal mihomo config for local validation",
                        "# Replace DIRECT with your proxy/group before real use.",
                        f"# generated: {generated_at}",
                        "",
                    ]
                )
                + _dump_yaml(config)
            )
        artifacts["mihomo-config.yaml"] = _sha256(config_path)
    else:
        for stale in ("mihomo-meta.yaml", "mihomo-config.yaml"):
            stale_path = os.path.join(outdir, stale)
            if os.path.exists(stale_path):
                os.remove(stale_path)

    if probes_path and os.path.exists(probes_path):
        artifacts["probes.json"] = _sha256(probes_path)

    return _write_lock(
        outdir,
        generated_at=generated_at,
        strict=strict,
        probe_gate=probe_gate,
        asn_mode=asn_mode,
        formats=formats,
        excluded=excluded,
        probes=probes,
        catalog=catalog,
        artifacts=artifacts,
    )


def _write_lock(
    outdir: str,
    *,
    generated_at: str,
    strict: bool,
    probe_gate: str,
    asn_mode: str,
    formats: tuple[str, ...],
    excluded: dict[str, dict[str, list[str]]],
    probes: dict[str, Any] | None,
    catalog: dict[str, Any],
    artifacts: dict[str, str],
) -> dict[str, Any]:
    lock = {
        "schema_version": 1,
        "generated_at": generated_at,
        "strict": strict,
        "probe_gate": probe_gate,
        "asn_mode": asn_mode,
        "formats": list(formats),
        "excluded": excluded,
        "probes": probes.get("summary") if probes else None,
        "sources": {
            name: {
                "commit": source.get("commit"),
                "files": {
                    path: {
                        "sha256": item.get("sha256"),
                        "last_update": item.get("last_update"),
                        "date_source": item.get("date_source"),
                        "stale": item.get("stale"),
                    }
                    for path, item in source.get("files", {}).items()
                },
            }
            for name, source in catalog.get("source_meta", {}).items()
        },
        "artifacts": artifacts,
    }
    lock_path = os.path.join(outdir, "lock.json")
    with open(lock_path, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return lock
