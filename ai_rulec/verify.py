from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import yaml

from . import platforms
from .formats import SURGE_PREFIXES
from .paths import mihomo_binary
from .surge import find_surge_cli

ALLOWED_PREFIXES = (
    "DOMAIN,",
    "DOMAIN-SUFFIX,",
    "DOMAIN-KEYWORD,",
    "PROCESS-NAME,",
    "IP-ASN,",
    "IP-CIDR,",
    "IP-CIDR6,",
    "URL-REGEX,",
)


def _load_yaml(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _lock_formats(outdir: str) -> list[str]:
    lock_path = os.path.join(outdir, "lock.json")
    try:
        with open(lock_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return ["mihomo"]
    formats = data.get("formats")
    if isinstance(formats, list) and formats:
        return [str(item) for item in formats]
    return ["mihomo"]


def _verify_singbox(outdir: str, errors: list[str], checks: dict[str, Any]) -> None:
    rules_dir = os.path.join(outdir, "singbox", "AI-Rules")
    config_path = os.path.join(outdir, "singbox", "config.json")
    if not os.path.isdir(rules_dir):
        errors.append("missing singbox/AI-Rules")
        return
    files = sorted(name for name in os.listdir(rules_dir) if name.endswith(".json"))
    checks["singbox_rule_sets"] = len(files)
    if not files:
        errors.append("no sing-box rule-sets emitted")
    for name in files:
        path = os.path.join(rules_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                body = "\n".join(
                    line
                    for line in fh.read().splitlines()
                    if not line.lstrip().startswith("//")
                )
            doc = json.loads(body)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"sing-box rule-set {name}: {exc}")
            continue
        if not isinstance(doc, dict) or doc.get("version") != 3:
            errors.append(f"sing-box rule-set {name}: unexpected version")
        for rule in doc.get("rules", []) if isinstance(doc, dict) else []:
            if not isinstance(rule, dict):
                errors.append(f"sing-box rule-set {name}: invalid rule entry")
                break
            if "ip_asn" in rule:
                errors.append(f"sing-box rule-set {name}: ip_asn is unsupported")
    if not os.path.exists(config_path):
        errors.append("missing singbox/config.json")
        return
    binary = platforms.which_exe("sing-box")
    if not binary:
        checks["singbox"] = {"status": "skipped", "reason": "sing-box not found"}
        return
    try:
        proc = subprocess.run(
            [
                binary,
                "check",
                "-D",
                os.path.abspath(os.path.join(outdir, "singbox")),
                "-c",
                os.path.abspath(config_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        checks["singbox"] = {
            "status": "pass" if proc.returncode == 0 else "fail",
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip()[-2000:],
        }
        if proc.returncode != 0:
            errors.append("sing-box check failed")
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks["singbox"] = {"status": "error", "reason": str(exc)}
        errors.append(f"sing-box check error: {exc}")


def _verify_surge(outdir: str, errors: list[str], checks: dict[str, Any]) -> None:
    rules_dir = os.path.join(outdir, "surge", "AI-Rules")
    profile_path = os.path.join(outdir, "surge", "profile.conf")
    if not os.path.isdir(rules_dir):
        errors.append("missing surge/AI-Rules")
    else:
        files = sorted(name for name in os.listdir(rules_dir) if name.endswith(".list"))
        checks["surge_rule_sets"] = len(files)
        if not files:
            errors.append("no Surge rule-set lists emitted")
        for name in files:
            path = os.path.join(rules_dir, name)
            with open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    text = line.strip()
                    if not text or text.startswith("#"):
                        continue
                    if text.startswith("FINAL") or "extended-matching" in text:
                        errors.append(f"surge list {name}:{lineno}: invalid {text!r}")
                        break
                    if not text.startswith(SURGE_PREFIXES):
                        errors.append(f"surge list {name}:{lineno}: invalid {text!r}")
                        break
    if not os.path.exists(profile_path):
        errors.append("missing surge/profile.conf")
        return
    binary = find_surge_cli()
    if not binary:
        checks["surge"] = {"status": "skipped", "reason": "surge-cli not found"}
        return
    try:
        proc = subprocess.run(
            [binary, "--check", profile_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        checks["surge"] = {
            "status": "pass" if proc.returncode == 0 else "fail",
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip()[-2000:],
        }
        if proc.returncode != 0:
            errors.append("surge-cli --check failed")
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks["surge"] = {"status": "error", "reason": str(exc)}
        errors.append(f"surge-cli --check error: {exc}")


def _verify_tower(outdir: str, errors: list[str], checks: dict[str, Any]) -> None:
    """Check the Tower scheme against the constraints its Clash reader enforces."""
    path = os.path.join(outdir, "tower", "ai-rules.yaml")
    if not os.path.exists(path):
        errors.append("missing tower/ai-rules.yaml")
        return
    try:
        document = _load_yaml(path)
    except yaml.YAMLError as exc:
        errors.append(f"tower/ai-rules.yaml: {exc}")
        return
    if not isinstance(document, dict):
        errors.append("tower/ai-rules.yaml is not a mapping")
        return

    providers = document.get("rule-providers") or {}
    groups = document.get("proxy-groups") or []
    rules = document.get("rules") or []
    checks["tower"] = {
        "providers": len(providers),
        "groups": len(groups),
        "rules": len(rules),
    }
    if not providers:
        errors.append("tower: no rule-providers")
    if not groups:
        errors.append("tower: no proxy-groups (Tower rejects a scheme without groups)")
    if not rules:
        errors.append("tower: no rules (Tower rejects a scheme without rules)")

    group_names = {g.get("name") for g in groups if isinstance(g, dict)}
    for name, provider in providers.items():
        if not isinstance(provider, dict):
            errors.append(f"tower: provider {name} is not a mapping")
            continue
        if provider.get("type") != "inline":
            errors.append(
                f"tower: provider {name} must be type inline "
                "(Tower cannot read type:file providers)"
            )
        for forbidden in ("url", "path", "header", "headers", "exclude-filter"):
            if forbidden in provider:
                errors.append(f"tower: provider {name} carries unsupported '{forbidden}'")
        payload = provider.get("payload") or []
        if not payload:
            errors.append(f"tower: provider {name} has an empty payload")
        for line in payload:
            if not isinstance(line, str) or not line.startswith(ALLOWED_PREFIXES):
                errors.append(f"tower: provider {name} has an unsupported line: {line!r}")

    for rule in rules:
        if not isinstance(rule, str):
            errors.append(f"tower: rule is not a string: {rule!r}")
            continue
        fields = [f.strip() for f in rule.split(",")]
        if fields[0].upper() == "RULE-SET":
            if len(fields) < 3:
                errors.append(f"tower: malformed rule: {rule}")
                continue
            if fields[1] not in providers:
                errors.append(f"tower: rule references unknown provider: {fields[1]}")
            if fields[2] not in group_names and fields[2].upper() not in (
                "DIRECT",
                "REJECT",
            ):
                errors.append(f"tower: rule targets unknown group: {fields[2]}")
        elif fields[0].upper() not in ("MATCH", "FINAL"):
            errors.append(f"tower: bare rule is not Tower-importable: {rule}")

    mihomo, hint = mihomo_binary()
    if not mihomo:
        checks["tower"]["mihomo"] = {"status": "skipped", "reason": hint}
        return
    try:
        proc = subprocess.run(
            [mihomo, "-t", "-f", os.path.abspath(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks["tower"]["mihomo"] = {"status": "error", "reason": str(exc)}
        errors.append(f"mihomo -t (tower) error: {exc}")
        return
    checks["tower"]["mihomo"] = {
        "status": "ok" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
    }
    if proc.returncode != 0:
        errors.append(f"mihomo -t rejected tower/ai-rules.yaml: {proc.stderr[-400:]}")


def _collect_warnings(
    coverage: dict[str, Any],
    probes: dict[str, Any] | None,
    warnings: list[str],
    checks: dict[str, Any],
) -> None:
    if isinstance(probes, dict):
        probe_summary = probes.get("summary", {})
        checks["probes"] = probe_summary
        issues = int(probe_summary.get("issues", 0) or 0)
        if issues:
            warnings.append(
                f"probe issues: {issues}/{probe_summary.get('total', 0)} domains"
            )
        for status in ("dns_fail", "tls_fail", "tls_mismatch", "parked"):
            count = int(probe_summary.get("by_status", {}).get(status, 0) or 0)
            if count:
                warnings.append(f"probe {status}: {count} domains")

    conflicts = coverage.get("conflicts", {})
    if conflicts.get("shared_domains"):
        warnings.append(
            f"shared domains present: {len(conflicts['shared_domains'])} "
            "(review before routing)"
        )
    if conflicts.get("broad_asns"):
        warnings.append(
            f"broad ASNs present: {', '.join(conflicts['broad_asns'])} "
            "(high false-positive risk)"
        )
    if conflicts.get("reject_set_domains"):
        warnings.append(
            f"domains also in reject set: {len(conflicts['reject_set_domains'])}"
        )
    if conflicts.get("mitm_rules"):
        warnings.append(
            f"MITM URL-REGEX rules present: {len(conflicts['mitm_rules'])} "
            "(not emitted; requires CA)"
        )
    stale = coverage.get("summary", {}).get("stale_sources", [])
    if stale:
        warnings.append(f"stale sources: {', '.join(stale)}")


def _mihomo_less_report(
    errors: list[str],
    warnings: list[str],
    checks: dict[str, Any],
    coverage: dict[str, Any],
    probes: dict[str, Any] | None,
) -> dict[str, Any]:
    _collect_warnings(coverage, probes, warnings, checks)
    return {
        "ok": not errors,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def verify_outputs(
    outdir: str,
    coverage: dict[str, Any],
    probes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    if probes is None:
        probe_path = os.path.join(outdir, "probes.json")
        if os.path.exists(probe_path):
            try:
                with open(probe_path, encoding="utf-8") as fh:
                    probes = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f"probes.json unreadable: {exc}")
                probes = None

    meta_path = os.path.join(outdir, "mihomo-meta.yaml")
    config_path = os.path.abspath(os.path.join(outdir, "mihomo-config.yaml"))
    formats = _lock_formats(outdir)
    checks["formats"] = formats
    if "sing-box" in formats:
        _verify_singbox(outdir, errors, checks)
    if "surge" in formats:
        _verify_surge(outdir, errors, checks)
    if "tower" in formats:
        _verify_tower(outdir, errors, checks)
    if "mihomo" not in formats:
        return _mihomo_less_report(errors, warnings, checks, coverage, probes)

    if not os.path.exists(meta_path):
        errors.append("missing mihomo-meta.yaml")
        meta = {}
    else:
        try:
            meta = _load_yaml(meta_path)
        except yaml.YAMLError as exc:
            errors.append(f"mihomo-meta.yaml: {exc}")
            meta = {}

    if not os.path.exists(config_path):
        errors.append("missing mihomo-config.yaml")
    else:
        try:
            _load_yaml(config_path)
        except yaml.YAMLError as exc:
            errors.append(f"mihomo-config.yaml: {exc}")

    providers = meta.get("rule-providers", {}) if isinstance(meta, dict) else {}
    checks["provider_count"] = len(providers)
    if not providers:
        errors.append("no rule-providers in mihomo-meta.yaml")

    for name, provider in providers.items():
        if not isinstance(provider, dict):
            errors.append(f"provider {name}: not a mapping")
            continue
        rel = str(provider.get("path", ""))
        path = os.path.normpath(os.path.join(outdir, rel))
        if not os.path.exists(path):
            errors.append(f"provider {name}: missing file {rel}")
            continue
        try:
            data = _load_yaml(path)
        except yaml.YAMLError as exc:
            errors.append(f"provider {name}: {exc}")
            continue
        payload = data.get("payload") if isinstance(data, dict) else data
        if not isinstance(payload, list):
            errors.append(f"provider {name}: payload is not a list")
            continue
        behavior = str(provider.get("behavior", "classical"))
        for line in payload:
            if not isinstance(line, str) or not line.startswith(ALLOWED_PREFIXES):
                errors.append(f"provider {name}: invalid rule {line!r}")
                continue
            if behavior == "domain" and not line.startswith(
                ("DOMAIN,", "DOMAIN-SUFFIX,", "DOMAIN-KEYWORD,")
            ):
                errors.append(
                    f"provider {name}: {behavior} behavior cannot contain {line!r}"
                )

    groups = meta.get("proxy-groups", []) if isinstance(meta, dict) else []
    group_names = {g.get("name") for g in groups if isinstance(g, dict)}

    rules = meta.get("rules", []) if isinstance(meta, dict) else []
    checks["rule_count"] = len(rules)
    for rule in rules:
        if not isinstance(rule, str):
            errors.append(f"invalid rule: {rule!r}")
            continue
        if rule.startswith("RULE-SET,"):
            parts = rule.split(",")
            if len(parts) < 3:
                errors.append(f"invalid RULE-SET: {rule}")
                continue
            provider_name = parts[1]
            if provider_name not in providers:
                errors.append(f"RULE-SET references unknown provider: {provider_name}")
            target_group = parts[2]
            if target_group not in group_names:
                errors.append(
                    f"RULE-SET {provider_name} references unknown group: {target_group}"
                )
        elif rule.startswith("MATCH"):
            continue
        else:
            errors.append(f"unexpected rule: {rule}")

    checks["proxy_groups"] = sorted(name for name in group_names if name)
    if "AI" not in group_names:
        errors.append("missing proxy-group AI")
    expected_groups = {
        f"AI-{name}" for name in coverage.get("services", {}) if isinstance(name, str)
    }
    missing_groups = sorted(expected_groups - group_names)
    if missing_groups:
        errors.append(f"missing proxy-groups: {', '.join(missing_groups)}")

    _collect_warnings(coverage, probes, warnings, checks)

    mihomo, mihomo_hint = mihomo_binary()
    if not mihomo:
        checks["mihomo"] = {"status": "skipped", "reason": mihomo_hint}
        warnings.append(mihomo_hint)
    else:
        try:
            proc = subprocess.run(
                [mihomo, "-t", "-f", config_path],
                cwd=outdir,
                capture_output=True,
                text=True,
                timeout=30,
            )
            checks["mihomo"] = {
                "status": "pass" if proc.returncode == 0 else "fail",
                "returncode": proc.returncode,
                "stdout": proc.stdout.strip()[-2000:],
                "stderr": proc.stderr.strip()[-2000:],
            }
            if proc.returncode != 0:
                errors.append("mihomo -t failed")
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks["mihomo"] = {"status": "error", "reason": str(exc)}
            errors.append(f"mihomo -t error: {exc}")

    return {
        "ok": not errors,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }
