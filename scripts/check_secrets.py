#!/usr/bin/env python3
"""Scan tracked files (and optionally git history) for obvious secrets.

This is a guardrail, not a full secret scanner. It deliberately uses narrow
patterns to keep false positives low; review every hit manually.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |PGP )?PRIVATE KEY-----"),
    "openai-key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "github-token": re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}"),
    "aws-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "google-key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "proxy-uri": re.compile(r"\b(?:vmess|vless|trojan|ss|hysteria2?)://"),
    "credential-assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|secret|password|passwd|access[_-]?token|auth[_-]?token)"
        r"\b\s*[:=]\s*['\"]?[^\s'\"#]{8,}"
    ),
    "absolute-home": re.compile(r"/(?:Users|home)/[^/\s]+"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}


def _mask(value: str, keep: int = 6) -> str:
    return value[:keep] + "…" if len(value) > keep else "…"


def scan_text(text: str, where: str) -> list[str]:
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, rx in PATTERNS.items():
            match = rx.search(line)
            if match:
                hits.append(f"{where}:{lineno}: {name}: {_mask(match.group(0))}")
    return hits


def scan_files(refs: list[str] | None = None) -> list[str]:
    if refs is None:
        raw = subprocess.check_output(["git", "ls-files", "-z"])
        refs = [p.decode() for p in raw.split(b"\0") if p]
    hits: list[str] = []
    for ref in refs:
        path = Path(ref)
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        hits.extend(scan_text(text, ref))
    return hits


def scan_history() -> list[str]:
    diff = subprocess.check_output(
        ["git", "log", "-p", "--all", "--no-ext-diff", "--format="],
        text=True,
        errors="replace",
    )
    hits: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            hits.extend(scan_text(line[1:], "git-history"))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="also scan added lines in git history")
    parser.add_argument("--all", action="store_true", help="scan all files, not only tracked files")
    args = parser.parse_args()

    if args.all:
        refs = [str(p) for p in Path(".").rglob("*") if p.is_file() and ".git" not in p.parts]
        hits = scan_files(refs)
    else:
        hits = scan_files()
    if args.history:
        hits += scan_history()

    for hit in hits:
        print(hit)
    print(f"{len(hits)} potential secret(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
