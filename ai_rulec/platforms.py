"""Platform adaptation: directories, client config roots, atomic writes, links.

Everything that differs between macOS, Linux and Windows lives here so the rest
of the package can stay path-agnostic. Scripts under ``scripts/`` stay thin:
they only check prerequisites and call ``ai-rulec service``.

Design rules:

* Candidate client directories are *guesses* per OS and are validated by
  ``discover`` at runtime; an unverified guess must never be authoritative.
* Writes to a live client config go through :func:`atomic_replace`, which
  retries on ``PermissionError`` because Windows keeps the file locked while
  the client holds it open.
* Provider rule files are only read by the proxy core, so a copy is as good as
  a symlink; :func:`link_or_copy` prefers a link and falls back to a copy.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

WINDOWS = sys.platform.startswith("win")
MACOS = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")


def os_family() -> str:
    """One of ``macos``, ``linux``, ``windows`` or ``<sys.platform>``."""
    if MACOS:
        return "macos"
    if WINDOWS:
        return "windows"
    if LINUX:
        return "linux"
    return sys.platform


def home() -> Path:
    return Path(os.path.expanduser("~"))


def _env_dir(var: str, *fallback: str) -> Path | None:
    for name in (var, *fallback):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser()
    return None


def config_home() -> Path:
    """Where applications keep user config: XDG on Unix, APPDATA on Windows."""
    if WINDOWS:
        base = _env_dir("APPDATA")
        return base if base is not None else home() / "AppData" / "Roaming"
    base = _env_dir("XDG_CONFIG_HOME")
    return base if base is not None else home() / ".config"


def data_home() -> Path:
    """Where applications keep bulk data: LOCALAPPDATA on Windows."""
    if WINDOWS:
        base = _env_dir("LOCALAPPDATA")
        return base if base is not None else home() / "AppData" / "Local"
    if MACOS:
        return home() / "Library" / "Application Support"
    base = _env_dir("XDG_DATA_HOME")
    return base if base is not None else home() / ".local" / "share"


def state_home() -> Path:
    """Small mutable state (build records, watch state, events)."""
    if WINDOWS:
        base = _env_dir("LOCALAPPDATA")
        return base if base is not None else home() / "AppData" / "Local"
    base = _env_dir("XDG_STATE_HOME")
    return base if base is not None else home() / ".local" / "state"


def log_dir() -> Path:
    if WINDOWS:
        base = _env_dir("LOCALAPPDATA")
        return (base if base is not None else home() / "AppData" / "Local") / "ai-rulec" / "logs"
    if MACOS:
        return home() / "Library" / "Logs"
    return state_home() / "ai-rulec" / "log"


def client_roots() -> list[str]:
    """Candidate proxy-client data directories for this OS, most likely first.

    Entries are ``~``-relative strings so configs and docs stay readable; use
    :func:`expand` before touching them.
    """
    if MACOS:
        support = "~/Library/Application Support"
        return [
            f"{support}/com.follow.clash",
            f"{support}/FlClash",
            f"{support}/io.github.clash-verge-rev.clash-verge-rev",
            f"{support}/clash-verge",
            f"{support}/Stash",
            f"{support}/ClashX",
            f"{support}/mihomo",
            "~/.config/clash",
            "~/.config/mihomo",
            "~/.config/flclash",
            "~/.config/clash-nyanpasu",
            "~/.local/share/io.github.flclash",
        ]
    if WINDOWS:
        roaming = os.environ.get("APPDATA") or "~/AppData/Roaming"
        local = os.environ.get("LOCALAPPDATA") or "~/AppData/Local"
        return [
            f"{roaming}/com.follow.clash",
            f"{local}/com.follow.clash",
            f"{roaming}/FlClash",
            f"{roaming}/io.github.clash-verge-rev.clash-verge-rev",
            f"{roaming}/clash-verge",
            f"{local}/io.github.clash-verge-rev.clash-verge-rev",
            f"{roaming}/Stash",
            f"{roaming}/mihomo",
            "~/.config/clash",
            "~/.config/mihomo",
        ]
    return [
        "~/.config/clash",
        "~/.config/mihomo",
        "~/.config/flclash",
        "~/.config/io.github.clash-verge-rev.clash-verge-rev",
        "~/.config/clash-verge",
        "~/.local/share/io.github.clash-verge-rev.clash-verge-rev",
        "~/.local/share/io.github.flclash",
        "~/.config/clash-nyanpasu",
        "~/.config/stash",
    ]


def singbox_config_roots() -> list[str]:
    """Candidate sing-box / Hiddify config files for this OS."""
    if MACOS:
        support = "~/Library/Application Support"
        return [
            f"{support}/app.hiddify.com/data/current-config.json",
            f"{support}/com.hiddify.hiddify/data/current-config.json",
            f"{support}/sing-box/config.json",
            "~/.config/sing-box/config.json",
        ]
    if WINDOWS:
        roaming = os.environ.get("APPDATA") or "~/AppData/Roaming"
        local = os.environ.get("LOCALAPPDATA") or "~/AppData/Local"
        return [
            f"{roaming}/app.hiddify.com/data/current-config.json",
            f"{local}/app.hiddify.com/data/current-config.json",
            f"{roaming}/sing-box/config.json",
            f"{local}/sing-box/config.json",
            "~/.config/sing-box/config.json",
        ]
    return [
        "~/.config/sing-box/config.json",
        "~/.local/share/sing-box/config.json",
        "~/.config/hiddify/config.json",
        "~/.config/app.hiddify.com/data/current-config.json",
    ]


def expand(path: str | Path) -> Path:
    return Path(os.path.expanduser(str(path)))


def which_exe(name: str) -> str | None:
    """``shutil.which`` that also tries the ``.exe`` suffix on Windows."""
    found = shutil.which(name)
    if found:
        return found
    if WINDOWS and not name.lower().endswith(".exe"):
        return shutil.which(name + ".exe")
    return None


def atomic_replace(src: str | Path, dst: str | Path, *, retries: int = 5, delay: float = 0.15) -> None:
    """``os.replace`` that survives a Windows file lock on *dst*.

    Windows refuses to replace a file another process has open, so retry with a
    short backoff. The error is re-raised once retries run out; callers should
    then tell the user to close the client.
    """
    src_path, dst_path = Path(src), Path(dst)
    last: PermissionError | None = None
    for attempt in range(max(1, retries)):
        try:
            os.replace(src_path, dst_path)
            return
        except PermissionError as exc:  # Windows sharing violation
            last = exc
            if attempt + 1 < retries:
                time.sleep(delay * (attempt + 1))
    assert last is not None
    raise PermissionError(
        f"cannot replace {dst_path}: {last.strerror or last} "
        "(close the client that holds this file open and retry)"
    ) from last


def link_or_copy(src: str | Path, dst: str | Path) -> str:
    """Create *dst* as a symlink to *src*; fall back to a copy.

    Returns ``"symlink"`` or ``"copy"``. Windows without Developer Mode or admin
    rights cannot create symlinks, and the proxy core only reads these files.
    """
    src_path, dst_path = Path(src), Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(src_path, dst_path)
        return "symlink"
    except (OSError, NotImplementedError):
        shutil.copy2(src_path, dst_path)
        return "copy"


def python_executable() -> str:
    """Interpreter to embed in a service definition (always absolute)."""
    return str(Path(sys.executable).resolve())


def path_for_config(value: str | Path) -> str:
    """Render a path for a client config: forward slashes, no ``~``.

    mihomo/sing-box accept ``/`` on every OS, and a config written with ``/``
    stays readable when copied between machines.
    """
    return str(Path(value).as_posix())
