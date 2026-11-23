"""Locate build artifacts and external binaries without relying on CWD.

Two problems this solves:

* ``apply --latest`` with the default ``--meta out/mihomo-meta.yaml`` only worked
  when the shell happened to sit in the directory where ``build`` ran. The last
  successful build is now recorded under the user state dir, so readers can find
  it from anywhere.
* ``mihomo`` is optional but silently skipped, which made a missing verifier look
  like a passing verification. Callers get a hint they can act on instead.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import platforms

STATE_ENV = "AI_RULEC_STATE"
LAST_BUILD = "last-build.json"
MIHOMO_HINT = (
    "install it (macOS: brew install mihomo; other: "
    "https://github.com/MetaCubeX/mihomo/releases) or pass --mihomo PATH"
)


def state_dir() -> Path:
    override = os.environ.get(STATE_ENV)
    if override:
        return Path(override).expanduser()
    return platforms.state_home() / "ai-rulec"


def record_build(out: str | Path) -> Path:
    """Remember where the last successful build wrote its artifacts."""
    path = state_dir() / LAST_BUILD
    resolved = Path(out).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"out": str(resolved)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return resolved


def last_build() -> Path | None:
    path = state_dir() / LAST_BUILD
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    out = data.get("out")
    if not isinstance(out, str):
        return None
    candidate = Path(out).expanduser()
    return candidate if candidate.is_dir() else None


def resolve_out(explicit: str | Path | None = None) -> Path:
    """Build directory: explicit wins, then ./out, then the last recorded build."""
    if explicit:
        return Path(explicit).expanduser()
    local = Path.cwd() / "out"
    if local.is_dir():
        return local
    recorded = last_build()
    if recorded is not None:
        return recorded
    return local


def resolve_meta(explicit: str | Path | None = None) -> Path:
    """mihomo meta file path: explicit wins, else <out>/mihomo-meta.yaml."""
    if explicit:
        return Path(explicit).expanduser()
    return resolve_out() / "mihomo-meta.yaml"


def mihomo_binary(explicit: str | Path | None = None) -> tuple[str | None, str | None]:
    """Return ``(path, hint)``; ``path`` is None when no verifier is available."""
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate), None
        return None, f"{candidate} is not an executable file; {MIHOMO_HINT}"
    found = shutil.which("mihomo")
    if found:
        return found, None
    return None, f"mihomo not found; {MIHOMO_HINT}"
