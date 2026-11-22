"""Resolve bundled default configs, allowing a local override.

The package ships ``ai_rulec/data/{services,agents,sources}.yaml``. Those are the
canonical defaults: a wheel install has no repository root to read from. A user
who wants their own copy drops one at the documented local path and it wins.
"""
from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).with_name("data")

#: name -> (bundled file, local override path consulted first)
DEFAULTS: dict[str, tuple[str, str]] = {
    "services": ("services.yaml", "config/services.yaml"),
    "agents": ("agents.yaml", "config/agents.yaml"),
    "sources": ("sources.yaml", "sources.yaml"),
}


def bundled(name: str) -> Path:
    """Path to the packaged default for ``name``."""
    try:
        filename, _ = DEFAULTS[name]
    except KeyError:
        raise ValueError(f"unknown default config: {name!r}") from None
    return DATA_DIR / filename


def resolve(name: str, explicit: str | Path | None = None) -> str:
    """Return the config path to use.

    ``explicit`` wins when given. Otherwise a local override is used if it
    exists, and the bundled default is the fallback.
    """
    if explicit:
        return str(explicit)
    _, local = DEFAULTS[name]
    if Path(local).is_file():
        return local
    return str(bundled(name))
