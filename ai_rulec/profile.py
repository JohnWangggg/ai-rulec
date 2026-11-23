from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import platforms
from .merge import infer_target, merge_profile
from .paths import mihomo_binary as _mihomo_binary


# Candidate proxy-client data directories for this OS. Computed from the shared
# platform layer so Windows/Linux get their own guesses; entries stay
# ``~``-relative and are validated by discovery at runtime.
DEFAULT_ROOTS = platforms.client_roots()

# Ordered most-specific first: "clash-verge" must win over the generic mihomo
# "clash" component check below.
CLIENT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("flclash", ("flclash", "com-follow-clash", "io-github-flclash")),
    ("clash-verge", ("clash-verge", "clashverge")),
    ("stash", ("stash",)),
    ("clashx", ("clashx", "clash-x")),
    ("mihomo", ("mihomo", "clash-meta", "clashmeta")),
)


def _path_component_hint(value: str) -> str:
    """Lowercase a path component and collapse separators/punctuation to '-'."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def detect_client(path: str | Path) -> str:
    """Guess the client owning *path* from normalized path components."""
    parts = [_path_component_hint(part) for part in Path(path).expanduser().parts]
    for client, hints in CLIENT_HINTS:
        if any(hint in part for part in parts for hint in hints):
            return client
    # Legacy mihomo/Clash core directories: ~/.config/clash, ~/.config/clash.meta
    if "clash" in parts:
        return "mihomo"
    return "unknown"


def load_yaml(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    return data


def _candidate_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in {".yaml", ".yml"} else []
    if not root.is_dir():
        return []

    candidates: list[Path] = []
    profiles = root / "profiles"
    if profiles.is_dir():
        candidates.extend(sorted(profiles.glob("*.yaml")))
        candidates.extend(sorted(profiles.glob("*.yml")))
    for name in ("config.yaml", "config.yml"):
        path = root / name
        if path.is_file():
            candidates.append(path)
    candidates.extend(sorted(root.glob("*.yaml")))
    candidates.extend(sorted(root.glob("*.yml")))

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def inspect_profile(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    data = load_yaml(path)
    groups = [
        group for group in (data.get("proxy-groups") or []) if isinstance(group, dict)
    ]
    providers = data.get("rule-providers") or {}
    rules = data.get("rules") or []
    ai_groups = [
        str(group.get("name"))
        for group in groups
        if str(group.get("name", "")).startswith("AI-")
        or str(group.get("name", "")) == "AI"
    ]
    managed = any(
        isinstance(provider, dict) and "AI-Rules" in str(provider.get("path", ""))
        for provider in providers.values()
    )
    stat = path.stat()
    return {
        "path": str(path),
        "name": path.stem,
        "client": detect_client(path),
        "kind": "profile" if path.parent.name == "profiles" else "config",
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "mtime_ts": stat.st_mtime,
        "proxies": len(data.get("proxies") or []),
        "proxy_groups": len(groups),
        "providers": len(providers),
        "rules": len(rules),
        "ai_groups": ai_groups,
        "managed": managed,
        "target_hint": infer_target(data),
    }


def _flclash_active_id() -> str | None:
    """Read the profile id FlClash currently has selected.

    FlClash keeps it in ``flutter.config`` (a JSON string) inside the macOS
    preferences plist. Windows/Linux builds store the same key elsewhere, so a
    missing file just means "unknown" and the caller falls back.
    """
    plist = platforms.home() / "Library" / "Preferences" / "com.follow.clash.plist"
    try:
        import json
        import plistlib

        payload = plistlib.loads(plist.read_bytes()).get("flutter.config")
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        if not isinstance(payload, str):
            return None
        value = json.loads(payload).get("currentProfileId")
    except (OSError, ValueError, TypeError):
        # Missing, unreadable (sandboxed, permission-restricted) or malformed
        # preferences all mean the same thing: "we cannot know", never a crash.
        return None
    return str(value) if value else None


def _active_hints() -> list[str]:
    """Profile ids the client currently has selected, best-effort."""
    try:
        active = _flclash_active_id()
    except Exception:  # noqa: BLE001 - discovery must never abort on preferences
        return []
    return [active] if active else []


def pick_profile(
    profiles: list[dict[str, Any]], active_hints: list[str] | None = None
) -> dict[str, Any] | None:
    """Pick the profile a user means by "the current one".

    Order: the client's selected profile, then a managed profile that already
    carries AI groups, then the newest non-``config.yaml`` file. Sorting by
    mtime alone picks whatever subscription the client downloaded last, which is
    usually *not* the active one, so this must not be the primary key.
    """
    if not profiles:
        return None
    hints = _active_hints() if active_hints is None else active_hints
    for hint in hints:
        for item in profiles:
            if str(item.get("name")) == hint or Path(str(item.get("path"))).stem == hint:
                return item
    managed = [item for item in profiles if item.get("managed")]
    if managed:
        return max(managed, key=lambda item: item.get("mtime_ts", 0.0))
    return profiles[0]


def discover_profiles(
    roots: list[str] | None = None, extra: list[str] | None = None
) -> dict[str, Any]:
    roots = roots or DEFAULT_ROOTS
    candidates: list[Path] = []
    for root in [*roots, *(extra or [])]:
        candidates.extend(_candidate_files(Path(os.path.expanduser(root))))

    profiles: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            info = inspect_profile(path)
        except Exception as exc:  # noqa: BLE001 - discovery must not abort on one file
            errors.append({"path": str(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        if info["proxies"] == 0 and info["proxy_groups"] == 0 and info["rules"] == 0:
            continue
        profiles.append(info)

    profiles.sort(
        key=lambda item: (item["kind"] == "config", -item["mtime_ts"])
    )
    hints = set(_active_hints())
    for item in profiles:
        item["active"] = str(item.get("name")) in hints
    return {"profiles": profiles, "errors": errors}


def _mihomo_home(profile_file: Path) -> Path:
    for candidate in (profile_file.parent, *profile_file.parent.parents):
        if (candidate / "GEOIP.metadb").is_file() or (candidate / "GEOIP.dat").is_file():
            return candidate
    for candidate in (profile_file.parent, *profile_file.parent.parents):
        if (candidate / "config.yaml").is_file() or (candidate / "config.yml").is_file():
            return candidate
    return profile_file.parent


def _link_geo_assets(home: Path, data_dir: Path) -> list[str]:
    groups = {
        "GEOIP.dat": ("GEOIP.dat", "geoip.dat"),
        "GEOSITE.dat": ("GEOSITE.dat", "geosite.dat"),
        "GEOIP.metadb": ("GEOIP.metadb", "geoip.metadb"),
        "ASN.mmdb": ("ASN.mmdb", "asn.mmdb"),
    }
    linked: list[str] = []
    for source_name, aliases in groups.items():
        source = home / source_name
        if not source.is_file():
            continue
        for alias in aliases:
            target = data_dir / alias
            if target.exists():
                continue
            platforms.link_or_copy(source, target)
        linked.append(source_name)
    return linked


def _backup_path(path: Path, suffix: str) -> Path:
    candidate = path.with_name(path.name + suffix)
    index = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}{suffix}.{index}")
        index += 1
    return candidate


def _copy_rules(source: Path, target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for pattern in ("*.yaml", "*.yml"):
        for path in sorted(source.glob(pattern)):
            destination = target / path.name
            if path.resolve() != destination.resolve():
                shutil.copy2(path, destination)
            count += 1
    return count


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_hash(path: Path) -> str | None:
    """Hash provider rules while ignoring comments and blank lines.

    Generated headers carry a build timestamp, so a raw file hash differs on
    every rebuild even when the rule set is unchanged. FlClash's cache is only
    stale when the payload itself changed.
    """
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith(b"#"):
                continue
            digest.update(stripped)
            digest.update(b"\n")
    return digest.hexdigest()


def _rules_file_for_provider(name: str, rules_dir: Path) -> Path | None:
    """Map a generated provider name to its AI-Rules payload file.

    Names are matched case-sensitively on purpose: FlClash profiles may carry
    their own uppercase providers such as ``OpenAI``; those are not ours and
    must not be overwritten by a lowercase ``openai`` payload.
    """
    base = name.strip()
    candidates: list[str] = []
    if base.endswith("-domain"):
        candidates.append(f"{base[:-7]}.domain.yaml")
    else:
        candidates.append(f"{base}.yaml")
    existing = {path.name for path in rules_dir.glob("*.yaml")}
    existing.update(path.name for path in rules_dir.glob("*.yml"))
    for candidate in candidates:
        if candidate in existing:
            return rules_dir / candidate
    return None


def _runtime_config(profile_file: Path) -> Path | None:
    if profile_file.name.startswith(("config.yaml", "config.yml")):
        return profile_file if profile_file.is_file() else None
    for base in (profile_file.parent, *profile_file.parent.parents):
        for name in ("config.yaml", "config.yml"):
            candidate = base / name
            if candidate.is_file():
                return candidate
    return None


def _resolve_provider_path(raw: str, home: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = home / path
    return path


def inspect_provider_cache(
    profile_path: str | Path, rules_dir: str | Path | None = None
) -> dict[str, Any]:
    """Check that FlClash's hashed provider cache matches AI-Rules payloads."""
    profile_file = Path(profile_path).expanduser().resolve()
    config_file = _runtime_config(profile_file)
    if config_file is None:
        return {
            "ok": False,
            "profile": str(profile_file),
            "config": None,
            "error": "runtime config.yaml not found",
            "providers": [],
            "summary": {},
        }

    home = config_file.parent
    rules_path = (
        Path(rules_dir).expanduser().resolve()
        if rules_dir
        else home / "AI-Rules"
    )
    rules_exists = rules_path.is_dir()
    data = load_yaml(config_file)
    configured = data.get("rule-providers") or {}
    entries: list[dict[str, Any]] = []

    for name, provider in configured.items():
        if not isinstance(provider, dict):
            continue
        expected = _rules_file_for_provider(str(name), rules_path)
        if expected is None:
            continue
        raw_path = provider.get("path")
        expected_hash = _payload_hash(expected)
        if not raw_path:
            entries.append(
                {
                    "name": str(name),
                    "target": None,
                    "expected": str(expected),
                    "expected_hash": expected_hash,
                    "actual_hash": None,
                    "status": "no_path",
                }
            )
            continue
        target = _resolve_provider_path(str(raw_path), home)
        actual_hash = _payload_hash(target)
        if not target.exists():
            status = "missing"
        elif not target.is_file():
            status = "not_file"
        elif actual_hash != expected_hash:
            status = "mismatch"
        else:
            status = "ok"
        entries.append(
            {
                "name": str(name),
                "target": str(target),
                "expected": str(expected),
                "expected_hash": expected_hash,
                "actual_hash": actual_hash,
                "size": target.stat().st_size if target.is_file() else None,
                "status": status,
            }
        )

    by_status: dict[str, int] = {}
    for entry in entries:
        by_status[entry["status"]] = by_status.get(entry["status"], 0) + 1
    issues = [
        entry for entry in entries if entry["status"] not in {"ok"}
    ]
    return {
        "ok": rules_exists and bool(entries) and not issues,
        "profile": str(profile_file),
        "config": str(config_file),
        "home": str(home),
        "rules_dir": str(rules_path),
        "rules_dir_exists": rules_exists,
        "providers": entries,
        "summary": {
            "managed": len(entries),
            "ok": by_status.get("ok", 0),
            "missing": by_status.get("missing", 0),
            "mismatch": by_status.get("mismatch", 0),
            "other": len(entries)
            - by_status.get("ok", 0)
            - by_status.get("missing", 0)
            - by_status.get("mismatch", 0),
        },
    }


def repair_provider_cache(
    profile_path: str | Path,
    *,
    rules_dir: str | Path | None = None,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    """Copy AI-Rules payloads back into the paths referenced by config.yaml."""
    before = inspect_provider_cache(profile_path, rules_dir=rules_dir)
    repaired: list[dict[str, Any]] = []
    errors: list[str] = []

    for entry in before.get("providers", []):
        if entry["status"] not in {"missing", "mismatch"}:
            continue
        source = Path(entry["expected"])
        target = Path(entry["target"]) if entry.get("target") else None
        if target is None:
            errors.append(f"{entry['name']}: config entry has no path")
            continue
        if not source.is_file():
            errors.append(f"{entry['name']}: expected payload missing: {source}")
            continue

        action = {
            "name": entry["name"],
            "target": str(target),
            "source": str(source),
            "status_before": entry["status"],
        }
        if dry_run:
            action["status_after"] = "would_fix"
            repaired.append(action)
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and backup:
                backup_path = _backup_path(
                    target, f".bak.{time.strftime('%Y%m%d-%H%M%S')}"
                )
                shutil.copy2(target, backup_path)
                action["backup"] = str(backup_path)
            fd, temp_name = tempfile.mkstemp(
                prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
            )
            temp_path = Path(temp_name)
            try:
                os.close(fd)
                shutil.copyfile(source, temp_path)
                shutil.copystat(source, temp_path)
                platforms.atomic_replace(temp_path, target)
            finally:
                if temp_path.exists():
                    temp_path.unlink()
            action["status_after"] = "fixed"
            repaired.append(action)
        except OSError as exc:
            errors.append(f"{entry['name']}: {type(exc).__name__}: {exc}")

    if dry_run:
        after = before
        fixable = not errors and all(
            entry["status"] == "ok"
            or (entry.get("target") and Path(entry["expected"]).is_file())
            for entry in before.get("providers", [])
        )
        ok = bool(before.get("rules_dir_exists")) and bool(before.get("providers")) and fixable
    else:
        after = inspect_provider_cache(profile_path, rules_dir=rules_dir)
        ok = bool(after.get("ok")) and not errors

    return {
        "ok": ok,
        "profile": before.get("profile"),
        "config": before.get("config"),
        "rules_dir": before.get("rules_dir"),
        "before": before.get("summary", {}),
        "after": after.get("summary", {}),
        "providers": after.get("providers", []),
        "repaired": repaired,
        "errors": errors,
        "dry_run": dry_run,
    }


def doctor_profile(
    profile_path: str | Path,
    *,
    rules_dir: str | Path | None = None,
    fix: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Diagnose (and optionally repair) the FlClash provider cache."""
    profile_file = Path(profile_path).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    try:
        inspection = inspect_provider_cache(profile_file, rules_dir=rules_dir)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        inspection = {
            "ok": False,
            "config": None,
            "home": None,
            "rules_dir": None,
            "rules_dir_exists": False,
            "providers": [],
            "summary": {},
            "error": f"{type(exc).__name__}: {exc}",
        }
        errors.append(f"provider cache: {inspection['error']}")

    if not inspection.get("config"):
        errors.append("runtime config.yaml not found")
    if not inspection.get("rules_dir_exists"):
        errors.append(f"AI-Rules directory not found: {inspection.get('rules_dir')}")
    if not inspection.get("providers"):
        errors.append("config.yaml references no AI rule-providers")
    if not inspection.get("ok") and not fix:
        summary = inspection.get("summary", {})
        errors.append(
            "provider cache incomplete: "
            f"{summary.get('missing', 0)} missing, "
            f"{summary.get('mismatch', 0)} mismatched"
        )

    try:
        profile_info = inspect_profile(profile_file)
    except Exception as exc:  # noqa: BLE001 - doctor reports, never aborts
        profile_info = None
        errors.append(f"profile: {type(exc).__name__}: {exc}")
    else:
        if not profile_info["ai_groups"]:
            warnings.append("profile has no AI-* proxy groups")
        if not profile_info["managed"]:
            warnings.append("profile has no AI-Rules provider paths")

    report: dict[str, Any] = {
        "profile": str(profile_file),
        "home": inspection.get("home"),
        "config": inspection.get("config"),
        "rules_dir": inspection.get("rules_dir"),
        "cache": inspection,
        "profile_info": profile_info,
        "errors": errors,
        "warnings": warnings,
        "fix": fix,
        "dry_run": dry_run,
    }

    if fix:
        try:
            repair = repair_provider_cache(
                profile_file, rules_dir=rules_dir, dry_run=dry_run, backup=True
            )
        except (OSError, ValueError, yaml.YAMLError) as exc:
            repair = {
                "ok": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
        report["repair"] = repair
        if repair["ok"]:
            try:
                report["cache"] = inspect_provider_cache(
                    profile_file, rules_dir=rules_dir
                )
            except (OSError, ValueError, yaml.YAMLError) as exc:
                errors.append(f"cache recheck: {type(exc).__name__}: {exc}")
        else:
            errors.extend(repair.get("errors") or ["provider cache repair failed"])
        report["errors"] = errors
        report["ok"] = not errors
    else:
        report["ok"] = not errors

    return report


def apply_profile(
    profile_path: str,
    meta_path: str,
    *,
    out_path: str | None = None,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    rules_dir: str | None = None,
    replace_existing: bool = True,
    preserve_existing_proxies: bool = True,
    rules_position: str = "top",
    backup: bool = True,
    copy_rules: bool = True,
    repair_cache: bool = True,
    verify_mihomo: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    profile_file = Path(profile_path).expanduser().resolve()
    meta_file = Path(meta_path).expanduser().resolve()
    if not profile_file.is_file():
        raise ValueError(f"profile not found: {profile_file}")
    if not meta_file.is_file():
        raise ValueError(f"meta config not found: {meta_file}")

    base = load_yaml(profile_file)
    meta = load_yaml(meta_file)
    home_dir = _mihomo_home(profile_file)
    destination = Path(out_path).expanduser().resolve() if out_path else profile_file
    out_dir = destination.parent

    source_rules = (
        Path(rules_dir).expanduser().resolve()
        if rules_dir
        else meta_file.parent / "AI-Rules"
    )
    # FlClash runs mihomo with the data directory as its home, so relative
    # provider paths in an in-place profile must resolve there, not next to
    # profiles/<id>.yaml.
    provider_base = out_dir if out_path else home_dir
    target_rules = provider_base / "AI-Rules"
    meta_providers = meta.get("rule-providers") or {}
    expected_rules = sorted(
        Path(str(provider.get("path", ""))).name
        for provider in meta_providers.values()
        if isinstance(provider, dict) and provider.get("path")
    )
    if copy_rules:
        if not source_rules.is_dir():
            raise ValueError(f"AI-Rules directory not found: {source_rules}")
        missing = [
            name for name in expected_rules if not (source_rules / name).is_file()
        ]
        if missing:
            raise ValueError(
                "provider files missing from AI-Rules: " + ", ".join(missing)
            )

    merged, report = merge_profile(
        base,
        meta,
        target=target,
        mapping=mapping,
        providers_dir=platforms.path_for_config(target_rules),
        output_dir=str(provider_base),
        replace_existing=replace_existing,
        preserve_existing_proxies=preserve_existing_proxies,
        rules_position=rules_position,
    )
    report.update(
        {
            "profile": str(profile_file),
            "output": str(destination),
            "home": str(home_dir),
            "rules_dir": str(target_rules),
            "dry_run": dry_run,
        }
    )
    if dry_run:
        report["ok"] = True
        return report

    mihomo, mihomo_hint = _mihomo_binary(verify_mihomo)
    # Validate before touching the client: a rejected merge must leave the
    # profile, the provider files and the cache exactly as they were.
    report["mihomo"] = _validate_merged(
        merged, home_dir=home_dir, mihomo=mihomo, hint=mihomo_hint
    )
    if report["mihomo"].get("warning"):
        report["warnings"] = [*(report.get("warnings") or []), report["mihomo"]["warning"]]

    out_dir.mkdir(parents=True, exist_ok=True)
    backups: dict[str, str] = {}
    if backup and destination.exists():
        backup_file = _backup_path(destination, f".bak.{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(destination, backup_file)
        backups["profile"] = str(backup_file)
    if copy_rules and backup and target_rules.exists():
        backup_rules = _backup_path(
            target_rules, f".bak.{time.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copytree(target_rules, backup_rules)
        backups["rules"] = str(backup_rules)

    copied = 0
    if copy_rules:
        copied = _copy_rules(source_rules, target_rules)
        missing = [name for name in expected_rules if not (target_rules / name).is_file()]
        if missing:
            raise ValueError(
                "provider files missing after copy: " + ", ".join(missing)
            )
        report["rules_copied"] = copied
    report["backup"] = backups

    if repair_cache and not out_path:
        # Repair before the swap, never after: the runtime config still names
        # the old hashed paths, and a cache we cannot write must abort the
        # apply instead of leaving a profile whose providers fail to load.
        report["cache"] = repair_provider_cache(
            profile_file, rules_dir=target_rules, backup=True
        )
        if report["cache"].get("ok") is False and report["cache"].get("errors"):
            raise ValueError(
                "provider cache repair failed: "
                + "; ".join(str(item) for item in report["cache"]["errors"])
            )
    elif out_path:
        report["cache"] = {
            "ok": None,
            "skipped": "out_path set; cache repair only runs for in-place apply",
        }

    fd, temp_name = tempfile.mkstemp(
        prefix=destination.stem + ".", suffix=".tmp.yaml", dir=str(out_dir)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("# merged by ai-rulec\n")
            yaml.safe_dump(
                merged,
                fh,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        platforms.atomic_replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    report["ok"] = True
    return report


def _backup_files(path: Path) -> list[Path]:
    return sorted(
        (p for p in path.parent.glob(path.name + ".bak.*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def _backup_dirs(path: Path) -> list[Path]:
    return sorted(
        (p for p in path.parent.glob(path.name + ".bak.*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def list_backups(profile_path: str | Path) -> dict[str, list[str]]:
    profile_file = Path(profile_path).expanduser().resolve()
    home_dir = _mihomo_home(profile_file)
    return {
        "profile": [str(path) for path in _backup_files(profile_file)],
        "rules": [str(path) for path in _backup_dirs(home_dir / "AI-Rules")],
    }


def _validate_merged(
    merged: dict[str, Any],
    *,
    home_dir: Path,
    mihomo: str | None,
    hint: str | None,
) -> dict[str, Any]:
    """Validate a merged config in a throwaway data directory.

    The real profile is never written first: a rejected merge leaves the
    client's files untouched. ``merged`` is only rendered to disk inside the
    temporary directory, so validation-only keys (``geodata-mode``) can never
    leak into what the client loads.
    """
    if not mihomo:
        return {"ok": None, "warning": f"runtime validation skipped: {hint}"}
    has_dat = (home_dir / "GEOIP.dat").is_file()
    validation = dict(merged)
    if has_dat:
        validation["geodata-mode"] = True
    with tempfile.TemporaryDirectory(prefix="ai-rulec-mihomo-") as work:
        data_dir = Path(work)
        config_path = data_dir / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                validation,
                fh,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        linked = _link_geo_assets(home_dir, data_dir)
        proc = subprocess.run(
            [mihomo, "-t", "-d", str(data_dir), "-f", str(config_path)],
            capture_output=True,
            text=True,
            timeout=45,
        )
    output = (proc.stdout + proc.stderr).strip()
    report = {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "geodata_mode": "dat" if has_dat else "config",
        "geo_assets": linked,
        "output": output[-2000:],
    }
    if proc.returncode != 0:
        raise ValueError(f"mihomo validation failed: {output[-1000:]}")
    return report


def _validate_config_file(source: Path, home_dir: Path, mihomo: str) -> dict[str, Any]:
    has_dat = (home_dir / "GEOIP.dat").is_file()
    data = load_yaml(source)
    validation = dict(data)
    if has_dat:
        validation["geodata-mode"] = True
    with tempfile.TemporaryDirectory(prefix="ai-rulec-mihomo-") as work:
        data_dir = Path(work)
        config_path = data_dir / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(
                validation,
                fh,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
            )
        linked = _link_geo_assets(home_dir, data_dir)
        proc = subprocess.run(
            [mihomo, "-t", "-d", str(data_dir), "-f", str(config_path)],
            capture_output=True,
            text=True,
            timeout=45,
        )
    output = (proc.stdout + proc.stderr).strip()
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "geodata_mode": "dat" if has_dat else "config",
        "geo_assets": linked,
        "output": output[-2000:],
    }


def restore_profile(
    profile_path: str | Path,
    *,
    backup_path: str | Path | None = None,
    rules_backup: str | Path | None = None,
    restore_rules: bool = True,
    verify_mihomo: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    profile_file = Path(profile_path).expanduser().resolve()
    home_dir = _mihomo_home(profile_file)
    profile_backups = _backup_files(profile_file)
    chosen = (
        Path(backup_path).expanduser().resolve()
        if backup_path
        else (profile_backups[0] if profile_backups else None)
    )
    if chosen is None or not chosen.is_file():
        raise ValueError(f"no profile backup found for {profile_file}")

    rules_candidates = _backup_dirs(home_dir / "AI-Rules")
    chosen_rules = (
        Path(rules_backup).expanduser().resolve()
        if rules_backup
        else (rules_candidates[0] if rules_candidates else None)
    )
    if chosen_rules is not None and not chosen_rules.is_dir():
        raise ValueError(f"rules backup not found: {chosen_rules}")

    report: dict[str, Any] = {
        "profile": str(profile_file),
        "restored_from": str(chosen),
        "rules_restored_from": str(chosen_rules) if chosen_rules else None,
        "dry_run": dry_run,
    }

    mihomo, mihomo_hint = _mihomo_binary(verify_mihomo)
    if mihomo:
        validation = _validate_config_file(chosen, home_dir, mihomo)
        report["mihomo"] = validation
        if not validation["ok"]:
            raise ValueError(
                f"mihomo validation failed: {validation['output'][-1000:]}"
            )
    else:
        report["mihomo"] = {
            "ok": None,
            "warning": f"runtime validation skipped: {mihomo_hint}",
        }

    if dry_run:
        report["ok"] = True
        return report

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backups: dict[str, str] = {}
    if profile_file.exists():
        current = _backup_path(profile_file, f".pre-rollback.{timestamp}")
        shutil.copy2(profile_file, current)
        backups["profile"] = str(current)
    rules_dir = home_dir / "AI-Rules"
    if restore_rules and rules_dir.is_dir():
        current_rules = _backup_path(rules_dir, f".pre-rollback.{timestamp}")
        shutil.copytree(rules_dir, current_rules)
        backups["rules"] = str(current_rules)
    report["backup"] = backups

    fd, temp_name = tempfile.mkstemp(
        prefix=profile_file.stem + ".", suffix=".rollback.yaml", dir=str(profile_file.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(chosen.read_bytes())
        platforms.atomic_replace(temp_path, profile_file)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    if restore_rules and chosen_rules is not None:
        report["rules_copied"] = _copy_rules(chosen_rules, rules_dir)

    report["ok"] = True
    return report
