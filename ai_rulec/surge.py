"""Patch an existing Surge profile with the AI service groups and rules.

Surge has no per-provider file drop-in: groups and rules must live in the
profile itself. This module edits a profile in place-safe fashion — parse the
sections, replace the generated block, validate with `surge-cli --check`, then
swap the file atomically and keep a backup.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from typing import Any

from . import platforms
from .formats import infer_surge_target


SURGE_CLI_CANDIDATES = (
    "surge-cli",
    "/Applications/Surge.app/Contents/Applications/surge-cli",
)

SURGE_ROOTS = (
    "~/Library/Application Support/Surge/Profiles",
    "~/Library/Mobile Documents/iCloud~com~nssurge~inc/Documents/Profiles",
)


def discover_surge_profiles(roots: list[str] | None = None) -> list[dict[str, Any]]:
    """List Surge profiles, newest first."""
    found: list[dict[str, Any]] = []
    for root in roots or SURGE_ROOTS:
        base = os.path.expanduser(root)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not name.endswith(".conf"):
                continue
            path = os.path.join(base, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            select_groups = [
                line.split("=", 1)[0].strip()
                for line in text.splitlines()
                if line.strip().lower().split("=")[0].strip()
                and "=" in line
                and line.split("=", 1)[1].strip().startswith("select")
            ]
            found.append(
                {
                    "path": path,
                    "name": name,
                    "bytes": os.path.getsize(path),
                    "has_ai": any(
                        line.strip().startswith(("AI =", "AI-"))
                        for line in text.splitlines()
                    ),
                    "select_groups": select_groups,
                    "sections": [
                        line.strip()
                        for line in text.splitlines()
                        if line.strip().startswith("[")
                    ],
                }
            )
    found.sort(
        key=lambda item: (bool(item["select_groups"]), os.path.getmtime(item["path"])),
        reverse=True,
    )
    return found


def find_surge_cli() -> str | None:
    # Surge is Apple-only, so never probe the app bundle elsewhere; a
    # ``surge-cli`` on PATH is still honoured. Absolute candidates are POSIX
    # paths, so test for "/" rather than os.path.sep (which is "\\" on Windows
    # and would classify every app-bundle path as a bare command name).
    candidates = (
        SURGE_CLI_CANDIDATES
        if platforms.MACOS
        else tuple(item for item in SURGE_CLI_CANDIDATES if "/" not in item)
    )
    for candidate in candidates:
        if "/" in candidate:
            if os.path.exists(candidate):
                return candidate
        else:
            found = platforms.which_exe(candidate)
            if found:
                return found
    return None


def split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """Split a Surge profile into (section header or None, lines) chunks."""
    chunks: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            chunks.append((stripped, []))
        else:
            chunks[-1][1].append(line)
    return chunks


def join_sections(chunks: list[tuple[str | None, list[str]]]) -> str:
    out: list[str] = []
    for header, lines in chunks:
        if header:
            out.append(header)
        out.extend(lines)
    return "\n".join(out).rstrip("\n") + "\n"


def _section(chunks: list[tuple[str | None, list[str]]], name: str) -> list[str] | None:
    for header, lines in chunks:
        if header and header.lower() == name.lower():
            return lines
    return None


def _is_ai_line(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("#") or not stripped:
        return False
    head = stripped.split("=", 1)[0].strip()
    return head == "AI" or head.startswith("AI-")


def _is_ai_rule(line: str) -> bool:
    parts = [part.strip() for part in line.strip().split(",")]
    if len(parts) < 3 or parts[0].upper() != "RULE-SET":
        return False
    return parts[2] == "AI" or parts[2].startswith("AI-")


def _ai_groups(services: list[str], target: str, mapping: dict[str, str]) -> list[str]:
    groups = [
        f"AI-{service} = select, {mapping.get(service, target)}, DIRECT"
        for service in services
    ]
    groups.append(
        "AI = select, " + ", ".join(f"AI-{service}" for service in services) + ", DIRECT"
    )
    return groups


def _ai_rules(services: list[str], prefix: str) -> list[str]:
    return [f"RULE-SET,{prefix}/{service}.list,AI-{service}" for service in services]


def patch_profile(
    text: str,
    services: list[str],
    *,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    rules_prefix: str = "AI-Rules",
    rules_position: str = "top",
) -> tuple[str, dict[str, Any]]:
    mapping = mapping or {}
    chunks = split_sections(text)

    group_lines = _section(chunks, "[Proxy Group]")
    if group_lines is None:
        raise ValueError("profile has no [Proxy Group] section")

    chosen = target or infer_surge_target(text)
    if not chosen:
        raise ValueError(
            "cannot infer a Surge policy; pass --target or add a select group"
        )

    existing_groups = [line for line in group_lines if _is_ai_line(line)]
    kept_groups = [line for line in group_lines if not _is_ai_line(line)]
    new_groups = _ai_groups(services, chosen, mapping)

    for header, lines in chunks:
        if header and header.lower() == "[proxy group]":
            lines[:] = kept_groups + new_groups

    rule_lines = _section(chunks, "[Rule]")
    if rule_lines is None:
        raise ValueError("profile has no [Rule] section")

    kept_rules = [line for line in rule_lines if not _is_ai_rule(line)]
    new_rules = _ai_rules(services, rules_prefix)
    if rules_position == "before-final":
        index = len(kept_rules)
        for position, line in enumerate(kept_rules):
            if line.strip().upper().startswith("FINAL"):
                index = position
                break
        merged = kept_rules[:index] + new_rules + kept_rules[index:]
    else:
        merged = new_rules + kept_rules

    for header, lines in chunks:
        if header and header.lower() == "[rule]":
            lines[:] = merged

    report = {
        "target": chosen,
        "groups_removed": len(existing_groups),
        "groups_added": len(new_groups),
        "rules_added": len(new_rules),
        "rules_position": rules_position,
        "services": list(services),
        "rules_prefix": rules_prefix,
    }
    return join_sections(chunks), report


def _validate(cli: str, profile_path: str) -> dict[str, Any]:
    proc = subprocess.run(
        [cli, "--check", profile_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return {
        "status": "pass" if proc.returncode == 0 else "fail",
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip()[-1000:],
        "stderr": proc.stderr.strip()[-1000:],
    }


def apply_surge(
    profile_path: str,
    services: list[str],
    *,
    lists_dir: str | None = None,
    target: str | None = None,
    mapping: dict[str, str] | None = None,
    rules_position: str = "top",
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, Any]:
    """Copy rule lists next to the profile and patch it, validating first."""
    profile_path = os.path.abspath(profile_path)
    if not os.path.exists(profile_path):
        raise FileNotFoundError(profile_path)
    profile_dir = os.path.dirname(profile_path)
    rules_dir = os.path.join(profile_dir, "AI-Rules")

    with open(profile_path, encoding="utf-8") as fh:
        original = fh.read()

    patched, report = patch_profile(
        original,
        services,
        target=target,
        mapping=mapping,
        rules_position=rules_position,
    )
    report["profile"] = profile_path
    report["rules_dir"] = rules_dir

    result: dict[str, Any] = {"ok": True, "dry_run": dry_run, **report}

    cli = find_surge_cli()
    if cli is None:
        result["ok"] = False
        result["error"] = "surge-cli not found; install Surge or run on macOS"
        return result

    staging = os.path.join(profile_dir, f".ai-rulec-preview-{os.getpid()}.conf")
    try:
        # Validate the untouched profile first: profiles that pull nodes from a
        # remote `$include` cannot be checked outside Surge, and patching a
        # profile we cannot verify would be a blind write.
        original_check = _validate(cli, profile_path)
        result["original_check"] = original_check
        if original_check["status"] != "pass":
            result["ok"] = False
            result["error"] = (
                "original profile already fails surge-cli --check "
                f"({original_check['stdout'] or original_check['stderr'] or 'no output'}); "
                "resolve its include/subscription first, then re-run"
            )
            return result

        with open(staging, "w", encoding="utf-8") as fh:
            fh.write(patched)
        check = _validate(cli, staging)
        result["check"] = check
        if check["status"] != "pass":
            result["ok"] = False
            result["error"] = "surge-cli --check rejected the patched profile"
            return result

        if dry_run:
            return result

        if lists_dir:
            os.makedirs(rules_dir, exist_ok=True)
            copied = 0
            for service in services:
                source = os.path.join(lists_dir, f"{service}.list")
                if not os.path.exists(source):
                    continue
                shutil.copyfile(source, os.path.join(rules_dir, f"{service}.list"))
                copied += 1
            result["lists_copied"] = copied

        if backup:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = f"{profile_path}.bak.{stamp}"
            shutil.copy2(profile_path, backup_path)
            result["backup"] = backup_path

        temp_path = f"{profile_path}.ai-rulec.tmp"
        with open(temp_path, "w", encoding="utf-8") as fh:
            fh.write(patched)
        platforms.atomic_replace(temp_path, profile_path)
        result["applied"] = True
        return result
    finally:
        if os.path.exists(staging):
            os.remove(staging)
