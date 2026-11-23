from __future__ import annotations

import hashlib
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .models import Rule
from .normalize import matches_any, normalize_domain

DOMAIN_KINDS = {"domain", "domain_suffix"}
KNOWN_RULE_TYPES = {
    "DOMAIN": "domain",
    "DOMAIN-SUFFIX": "domain_suffix",
    "DOMAIN-KEYWORD": "domain_keyword",
    "PROCESS-NAME": "process",
    "IP-CIDR": "ip_cidr",
    "IP-CIDR6": "ip_cidr",
    "IP-ASN": "ip_asn",
    "URL-REGEX": "url_regex",
}


def filter_redistributable(
    sources_cfg: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Drop sources whose data cannot be redistributed.

    A source is only safe to publish when its config marks
    ``redistributable: true``. Everything else stays local: the licence
    assessment lists GPL-2.0-only, unlicensed and unconfirmed repos.
    """
    kept = []
    dropped = []
    for source in sources_cfg.get("sources") or []:
        if source.get("redistributable") is True:
            kept.append(source)
        else:
            dropped.append(str(source.get("name")))
    if not kept:
        raise ValueError(
            "no redistributable sources configured; mark a source with "
            "`redistributable: true` in sources.yaml"
        )
    return {**sources_cfg, "sources": kept}, dropped


def _clean(raw: str) -> str | None:
    s = raw.strip()
    if not s or s.startswith("#") or s == "payload:":
        return None
    if s.startswith("- "):
        s = s[2:].strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    if not s.upper().startswith("URL-REGEX"):
        s = s.split("#", 1)[0].strip()
    return s or None


def parse_rule_string(
    raw: str,
    source: str,
    path: str,
    lineno: int,
    service_hint: str | None,
    set_name: str | None,
    domain_filter: set[str] | None = None,
    via_include: str | None = None,
) -> Rule | None:
    body = _clean(raw)
    if not body or body.lower().startswith("include:"):
        return None

    kind: str | None = None
    value: str | None = None
    requires_mitm = False

    if "," in body:
        parts = [p.strip() for p in body.split(",")]
        rtype = parts[0].upper()
        if rtype not in KNOWN_RULE_TYPES:
            return None
        kind = KNOWN_RULE_TYPES[rtype]
        if rtype == "URL-REGEX":
            value = ",".join(parts[1:]).strip()
            requires_mitm = True
        else:
            value = parts[1].strip() if len(parts) > 1 else ""
        if rtype == "IP-ASN":
            value = value.upper().removeprefix("AS").strip()
    else:
        lower = body.lower()
        if lower.startswith("+."):
            kind, value = "domain_suffix", body[2:]
        elif lower.startswith("full:"):
            kind, value = "domain", body[5:]
        elif lower.startswith("domain:"):
            kind, value = "domain_suffix", body[7:]
        elif lower.startswith("keyword:"):
            kind, value = "domain_keyword", body[8:]
        elif lower.startswith("regexp:"):
            kind, value, requires_mitm = "url_regex", body[7:], True
        else:
            kind, value = "domain_suffix", body

    if kind in DOMAIN_KINDS:
        value = normalize_domain(value)
        if not value:
            return None
    elif kind == "domain_keyword":
        value = value.lower().strip()
    elif kind == "process":
        value = value.strip()
    elif kind == "ip_asn":
        if not value.isdigit():
            return None
    elif kind == "ip_cidr":
        value = value.strip()
        if not value:
            return None
    elif kind == "url_regex":
        value = value.strip()
        if not value:
            return None

    if domain_filter is not None:
        if kind not in DOMAIN_KINDS:
            return None
        assert value is not None
        if not matches_any(value, domain_filter):
            return None

    return Rule(
        source=source,
        path=path,
        lineno=lineno,
        kind=kind,
        value=value,
        service_hint=service_hint,
        set_name=set_name,
        requires_mitm=requires_mitm,
        raw=raw.strip(),
        meta={"via_include": via_include} if via_include else {},
    )


def parse_lines(
    path: str,
    source: str,
    service_hint: str | None,
    set_name: str | None,
    domain_filter: set[str] | None = None,
) -> list[Rule]:
    rules: list[Rule] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            rule = parse_rule_string(
                raw, source, path, lineno, service_hint, set_name, domain_filter
            )
            if rule:
                rules.append(rule)
    return rules


def parse_yaml_payload(
    path: str,
    source: str,
    service_hint: str | None,
    set_name: str | None,
    domain_filter: set[str] | None = None,
) -> list[Rule]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return parse_lines(path, source, service_hint, set_name, domain_filter)

    items = data.get("payload") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    rules: list[Rule] = []
    for lineno, item in enumerate(items, 1):
        if not isinstance(item, str):
            continue
        rule = parse_rule_string(
            item, source, path, lineno, service_hint, set_name, domain_filter
        )
        if rule:
            rules.append(rule)
    return rules


def parse_v2fly(
    path: str,
    source: str,
    service_hint: str | None,
    set_name: str | None,
    domain_filter: set[str] | None = None,
    via_include: str | None = None,
    seen: set[str] | None = None,
) -> list[Rule]:
    seen = seen or set()
    real = os.path.realpath(path)
    if real in seen:
        return []
    seen.add(real)

    rules: list[Rule] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            body = _clean(raw)
            if not body:
                continue
            if body.lower().startswith("include:"):
                include = body.split(":", 1)[1].strip()
                include_path = os.path.join(os.path.dirname(path), include)
                if os.path.exists(include_path):
                    rules.extend(
                        parse_v2fly(
                            include_path,
                            source,
                            service_hint,
                            set_name,
                            domain_filter,
                            via_include=include,
                            seen=seen,
                        )
                    )
                continue
            rule = parse_rule_string(
                raw, source, path, lineno, service_hint, set_name, domain_filter
            )
            if rule:
                rules.append(rule)
    return rules


def parse_file(
    path: str,
    source: str,
    source_type: str,
    service_hint: str | None,
    set_name: str | None,
    domain_filter: set[str] | None = None,
) -> list[Rule]:
    if not os.path.exists(path):
        return []
    if source_type == "v2fly":
        return parse_v2fly(path, source, service_hint, set_name, domain_filter)
    if source_type == "clash_yaml":
        return parse_yaml_payload(path, source, service_hint, set_name, domain_filter)
    if source_type == "loyalsoldier":
        return parse_lines(path, source, service_hint, set_name, domain_filter)
    if source_type == "mixed":
        if path.endswith((".yaml", ".yml")):
            return parse_yaml_payload(path, source, service_hint, set_name, domain_filter)
        return parse_lines(path, source, service_hint, set_name, domain_filter)
    return parse_lines(path, source, service_hint, set_name, domain_filter)


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _source_root(source_cfg: dict[str, Any], global_root: str) -> str:
    root = str(source_cfg.get("root", ""))
    if os.path.isabs(root):
        return root
    return os.path.join(global_root, root)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit(root: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", root, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


def _detect_date(path: str, root: str) -> tuple[str | None, str]:
    try:
        head = Path(path).read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        head = ""
    m = re.search(
        r"#\s*(?:UPDATED|UPDATE|LAST\s+UPDATE)\s*:\s*(\d{4}-\d{2}-\d{2})",
        head,
        re.I,
    )
    if m:
        return m.group(1), "header"

    try:
        rel = os.path.relpath(path, root)
        out = subprocess.run(
            ["git", "-C", root, "log", "-1", "--format=%cI", "--", rel],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            dt = datetime.fromisoformat(out.stdout.strip().replace("Z", "+00:00"))
            return dt.date().isoformat(), "git"
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass

    try:
        return date.fromtimestamp(os.path.getmtime(path)).isoformat(), "mtime"
    except OSError:
        return None, "unknown"


def _file_meta(path: str, root: str, max_age_days: int) -> dict[str, Any]:
    last_update, date_source = _detect_date(path, root)
    age_days = None
    if last_update:
        try:
            age_days = (date.today() - date.fromisoformat(last_update)).days
        except ValueError:
            pass
    return {
        "path": path,
        "sha256": _sha256(path),
        "last_update": last_update,
        "date_source": date_source,
        "age_days": age_days,
        "stale": bool(age_days is not None and age_days > max_age_days),
    }


def build_source_meta(
    entries: list[dict[str, Any]],
    extra_paths: list[tuple[str, str]],
    max_age_days: int,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    root_by_source: dict[str, str] = {}
    source_by_name: dict[str, dict[str, Any]] = {}
    for entry in entries:
        src = entry["src"]
        name = src["name"]
        root = entry["root"]
        root_by_source[name] = root
        source_by_name[name] = src
        item = meta.setdefault(
            name,
            {
                "root": root,
                "commit": _git_commit(root),
                "license": src.get("license"),
                "redistributable": bool(src.get("redistributable")),
                "files": {},
            },
        )
        if entry["path"] not in item["files"]:
            item["files"][entry["path"]] = _file_meta(entry["path"], root, max_age_days)

    for source_name, path in extra_paths:
        root = root_by_source.get(source_name)
        if not root or not os.path.exists(path):
            continue
        src = source_by_name.get(source_name, {})
        item = meta.setdefault(
            source_name,
            {
                "root": root,
                "commit": _git_commit(root),
                "license": src.get("license"),
                "redistributable": bool(src.get("redistributable")),
                "files": {},
            },
        )
        if path not in item["files"]:
            item["files"][path] = _file_meta(path, root, max_age_days)
    return meta


def load_rules(
    sources_cfg: dict[str, Any],
    services: dict[str, Any],
    max_age_days: int,
) -> tuple[list[Rule], dict[str, Any]]:
    global_root = os.environ.get("SOURCE_ROOT") or str(
        sources_cfg.get("default_root") or ".sources"
    )

    entries: list[dict[str, Any]] = []
    for src in sources_cfg.get("sources", []):
        root = _source_root(src, global_root)
        source_type = str(src.get("type", "surge"))
        for key, files in (src.get("files") or {}).items():
            hint = None if str(key).startswith("_") else str(key)
            set_name = str(key) if source_type == "loyalsoldier" else None
            aggregate = hint is None or source_type == "loyalsoldier"
            for rel in _as_list(files):
                entries.append(
                    {
                        "src": src,
                        "root": root,
                        "path": os.path.join(root, rel),
                        "type": source_type,
                        "hint": hint,
                        "set_name": set_name,
                        "aggregate": aggregate,
                    }
                )

    if entries and not any(os.path.exists(entry["path"]) for entry in entries):
        missing = ", ".join(os.path.relpath(e["path"], global_root) for e in entries[:3])
        raise FileNotFoundError(
            f"no rule sources found under {global_root!r} (e.g. {missing}); "
            "run scripts/fetch_sources.sh or set SOURCE_ROOT"
        )

    rules: list[Rule] = []
    for entry in entries:
        if entry["aggregate"]:
            continue
        rules.extend(
            parse_file(
                entry["path"],
                entry["src"]["name"],
                entry["type"],
                entry["hint"],
                entry["set_name"],
            )
        )

    seeds: set[str] = set()
    for cfg in services.values():
        seeds.update(str(d) for d in cfg.get("known_domains", []))
    for rule in rules:
        if rule.kind in DOMAIN_KINDS:
            seeds.add(rule.value)

    for entry in entries:
        if not entry["aggregate"]:
            continue
        rules.extend(
            parse_file(
                entry["path"],
                entry["src"]["name"],
                entry["type"],
                entry["hint"],
                entry["set_name"],
                domain_filter=seeds,
            )
        )

    configured = {entry["path"] for entry in entries}
    extra_paths: list[tuple[str, str]] = []
    seen_extra: set[tuple[str, str]] = set()
    for rule in rules:
        key = (rule.source, rule.path)
        if rule.path not in configured and key not in seen_extra:
            seen_extra.add(key)
            extra_paths.append(key)

    source_meta = build_source_meta(entries, extra_paths, max_age_days)
    return rules, source_meta
