"""Install ``ai-rulec watch`` as a per-user background service.

One definition, three backends:

===========  ================================================  ==============================
OS           Unit                                              Control
===========  ================================================  ==============================
macOS        ``~/Library/LaunchAgents/com.ai-rulec.watch.plist``  ``launchctl`` bootout/bootstrap
Linux        ``~/.config/systemd/user/ai-rulec-watch.service``  ``systemctl --user``
Windows      Task Scheduler task ``ai-rulec-watch``            ``schtasks``
===========  ================================================  ==============================

Every backend runs the same command line::

    <python> -m ai_rulec watch --latest --meta <meta> --interval <n> \
        --health --agents-interval <n> --json

Commands are always executed as argument lists (never a shell string) so paths
with spaces or CJK characters survive.  ``dry_run=True`` renders the exact plan
and returns it without writing a unit file or touching the service manager.

Nothing here is authoritative about the machine's service state: ``status``
reports what the service manager says, and a user unit on Linux dies with the
session unless lingering is enabled (``install`` only warns about that).
"""
from __future__ import annotations

import contextlib
import getpass
import os
import plistlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import paths, platforms

MACOS_LABEL = "com.ai-rulec.watch"
LINUX_UNIT = "ai-rulec-watch.service"
WINDOWS_TASK = "ai-rulec-watch"
SERVICE_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
DEFAULT_INTERVAL = 5.0
DEFAULT_AGENTS_INTERVAL = 300.0

_BACKENDS = {"macos": "launchd", "linux": "systemd", "windows": "schtasks"}


class ServiceError(RuntimeError):
    """A backend command could not be started; the text is user-facing."""


# --------------------------------------------------------------------------- #
# Plan: the command line and the place its output goes
# --------------------------------------------------------------------------- #


def _project_dir() -> Path:
    """Directory that owns the ``ai_rulec`` package (a checkout, or site-packages)."""
    return Path(__file__).resolve().parents[1]


def _default_meta() -> Path:
    """Meta file of the last build, or ``./out`` when nothing was recorded."""
    return paths.resolve_meta()


def _work_dir(meta_path: Path) -> Path:
    """Directory the service runs in.

    A checkout keeps ``config/`` and an importable ``ai_rulec`` at its root, so
    prefer that whenever the meta file sits in its ``out/``. A wheel install
    has no checkout: its meta file is the one directory we know exists.
    """
    parent = meta_path.parent
    root = parent.parent
    if parent.name == "out" and root.is_dir():
        for marker in ("pyproject.toml", "config", ".sources"):
            if (root / marker).exists():
                return root
    return parent if parent.is_dir() else _project_dir()


def _watch_argv(
    meta: str | Path,
    interval: float,
    agents_interval: float,
    python: str,
) -> list[str]:
    return [
        python,
        "-m",
        "ai_rulec",
        "watch",
        "--latest",
        "--meta",
        str(meta),
        "--interval",
        f"{float(interval):g}",
        "--health",
        "--agents-interval",
        f"{float(agents_interval):g}",
        "--json",
    ]


def _macos_plist() -> Path:
    return platforms.home() / "Library" / "LaunchAgents" / f"{MACOS_LABEL}.plist"


def _linux_unit() -> Path:
    return platforms.config_home() / "systemd" / "user" / LINUX_UNIT


def _log_paths() -> tuple[Path, Path]:
    base = platforms.log_dir()
    return base / "ai-rulec-watch.log", base / "ai-rulec-watch.err.log"


def _uid() -> int:
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if getuid is not None else 0


def _launchd_target() -> str:
    return f"gui/{_uid()}/{MACOS_LABEL}"


def _linger_file() -> Path:
    """Marker systemd creates for ``loginctl enable-linger <user>``."""
    return Path("/var/lib/systemd/linger") / getpass.getuser()


def _linger_enabled() -> bool:
    try:
        return _linger_file().exists()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Report helpers
# --------------------------------------------------------------------------- #


def _exists(path: Path) -> bool:
    """`Path.is_file()` raises under a strict sandbox; absence is enough."""
    try:
        return path.is_file()
    except OSError:
        return False


def _report(action: str) -> dict[str, Any]:
    family = platforms.os_family()
    return {
        "ok": True,
        "action": action,
        "platform": family,
        "backend": _BACKENDS.get(family, "unsupported"),
        "errors": [],
        "warnings": [],
        "notes": [],
    }


def _fail(report: dict[str, Any], message: str) -> None:
    report["ok"] = False
    report["errors"].append(message)
    report["message"] = message


def _detail(proc: subprocess.CompletedProcess[str]) -> str:
    """Last useful line of a failed command, for a report rather than a traceback."""
    text = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    if not text:
        return f"exit code {proc.returncode}"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[:3]) if lines else f"exit code {proc.returncode}"


def _run(
    argv: list[str], *, timeout: float = 120.0, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
    except FileNotFoundError as exc:
        raise ServiceError(
            f"command not found: {argv[0]}; install it or use a supported OS"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ServiceError(
            f"timed out after {timeout:g}s: {subprocess.list2cmdline(argv)}"
        ) from exc
    except OSError as exc:
        raise ServiceError(f"cannot run {argv[0]}: {exc}") from exc


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        platforms.atomic_replace(temp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(temp)


def _tail(path: Path, limit: int = 15) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-limit:]


ABSENT_MARKERS = (
    "cannot find",
    "not find",
    "no such file",
    "not loaded",
    "does not exist",
    "不能找到",
    "找不到",
    "不存在",
)

# ``Get-ScheduledTask`` reports the state as an enum, so the number is the same
# in every UI language; ``schtasks /FO LIST`` localises the label itself.
TASK_STATE_RUNNING = 4


def _looks_absent(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ABSENT_MARKERS)


def _windows_task_state() -> int | None:
    """State of the scheduled task as an enum value, or None when unknown."""
    pwsh = platforms.which_exe("powershell") or platforms.which_exe("pwsh")
    if pwsh is None:
        return None
    script = (
        "$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue; "
        "if ($null -eq $t) { '' } else { [int]$t.State }" % WINDOWS_TASK
    )
    try:
        proc = _run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script])
    except ServiceError:
        return None
    text = (proc.stdout or "").strip()
    return int(text) if text.isdigit() else None


def _task_running_from_text(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ("running", "正在运行", "正在執行")
    )


# --------------------------------------------------------------------------- #
# Install
# --------------------------------------------------------------------------- #


def install(
    *,
    meta: str | None = None,
    interval: float = DEFAULT_INTERVAL,
    agents_interval: float = DEFAULT_AGENTS_INTERVAL,
    python: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Write the platform unit and start it.  Returns a report, never raises."""
    report = _report("install")
    report["dry_run"] = dry_run
    backend = report["backend"]
    if backend == "unsupported":
        _fail(
            report,
            f"no supported service backend for platform {report['platform']!r}; "
            "run `ai-rulec watch` in a terminal instead",
        )
        return report

    plan = _plan(
        meta=meta,
        interval=interval,
        agents_interval=agents_interval,
        python=python,
        report=report,
    )
    if plan is None:
        return report
    report.update(
        {
            "meta": str(plan["meta"]),
            "work_dir": str(plan["work_dir"]),
            "working_dir": str(plan["work_dir"]),
            "argv": plan["argv"],
            "python": plan["argv"][0],
            "command": subprocess.list2cmdline(plan["argv"]),
        }
    )

    if backend == "launchd":
        _install_launchd(report, plan, dry_run)
    elif backend == "systemd":
        _install_systemd(report, plan, dry_run)
    else:
        _install_schtasks(report, plan, dry_run)
    return report


def _plan(
    *,
    meta: str | None,
    interval: float,
    agents_interval: float,
    python: str | None,
    report: dict[str, Any],
    probe_pyyaml: bool = True,
    require_meta: bool = True,
) -> dict[str, Any] | None:
    """Validate the interpreter and meta file, then build the argv.

    ``require_meta=False`` is for ``describe``: it must be able to explain what
    would be installed before the first build has produced anything.
    """
    exe = platforms.python_executable()
    if python:
        candidate = platforms.expand(python)
        if not (candidate.is_file() and os.access(candidate, os.X_OK)):
            _fail(
                report,
                f"python interpreter not found or not executable: {candidate}; "
                "pass --python /absolute/path/to/python",
            )
            return None
        # abspath, not resolve: a venv's python is a symlink to the base
        # interpreter, and resolving it would silently drop sys.prefix.
        exe = os.path.abspath(candidate)

    meta_path = platforms.expand(meta) if meta else _default_meta()
    if not meta_path.is_file():
        if require_meta:
            _fail(
                report,
                f"meta file not found: {meta_path} "
                "(run: ai-rulec build, or pass --meta PATH)",
            )
            return None
        # describe() may run before the first build: keep an absolute path so
        # the report is stable, but do not resolve() a file that is not there.
        meta_path = Path(os.path.abspath(meta_path))
    else:
        meta_path = meta_path.resolve()

    if probe_pyyaml:
        yaml_ok, yaml_detail = _check_pyyaml(exe)
        if not yaml_ok:
            _fail(
                report,
                f"{exe} cannot import PyYAML ({yaml_detail}); "
                "pass --python with an interpreter that has PyYAML installed",
            )
            return None

    return {
        "meta": meta_path,
        "work_dir": _work_dir(meta_path),
        "argv": _watch_argv(meta_path, interval, agents_interval, exe),
        "interval": float(interval),
        "agents_interval": float(agents_interval),
    }


def _check_pyyaml(exe: str) -> tuple[bool, str]:
    """The service runs ``ai_rulec``, which imports yaml at startup."""
    try:
        proc = _run([exe, "-c", "import yaml"], timeout=30.0)
    except ServiceError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, _detail(proc)
    return True, ""


def _macos_payload(argv: list[str], work_dir: Path) -> dict[str, Any]:
    out_log, err_log = _log_paths()
    return {
        "Label": MACOS_LABEL,
        "ProgramArguments": argv,
        "WorkingDirectory": str(work_dir),
        "EnvironmentVariables": {"PATH": SERVICE_PATH, "PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(out_log),
        "StandardErrorPath": str(err_log),
    }


def _install_launchd(report: dict[str, Any], plan: dict[str, Any], dry_run: bool) -> None:
    plist = _macos_plist()
    out_log, err_log = _log_paths()
    payload = _macos_payload(plan["argv"], plan["work_dir"])
    commands = [
        ["launchctl", "bootout", _launchd_target()],
        ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)],
        ["launchctl", "kickstart", "-k", _launchd_target()],
    ]
    report.update(
        {
            "unit": str(plist),
            "unit_kind": "plist",
            "unit_exists": _exists(plist),
            "logs": {"out": str(out_log), "err": str(err_log)},
            "commands": commands,
        }
    )
    if dry_run:
        report["message"] = f"dry run: would write {plist} and load {MACOS_LABEL}"
        return

    try:
        out_log.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(plist, plistlib.dumps(payload, fmt=plistlib.FMT_XML))
        with contextlib.suppress(ServiceError):
            _run(commands[0])  # an old copy may not be loaded; that is fine
        proc = _run(commands[1])
        if proc.returncode != 0:
            _fail(
                report,
                f"launchctl bootstrap failed: {_detail(proc)}; "
                f"the plist is at {plist}, retry `launchctl bootstrap gui/{_uid()} {plist}`",
            )
            return
        with contextlib.suppress(ServiceError):
            _run(commands[2])  # kickstart is best-effort; bootstrap already started it
    except ServiceError as exc:
        _fail(report, str(exc))
        return
    except OSError as exc:
        _fail(report, f"cannot write {plist}: {exc}")
        return

    report["unit_exists"] = _exists(plist)
    report["message"] = f"{MACOS_LABEL} installed; logs: {out_log}"


def _systemd_arg(arg: str) -> str:
    """Quote one ExecStart word only when systemd would otherwise split it."""
    escaped = arg.replace("%", "%%")
    if not any(ch.isspace() or ch in "\"'\\;$" for ch in arg):
        return escaped
    escaped = escaped.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _systemd_unit(argv: list[str], out_log: Path, err_log: Path, work_dir: Path) -> str:
    exec_start = " ".join(_systemd_arg(item) for item in argv)
    return "\n".join(
        [
            "[Unit]",
            "Description=ai-rulec watch: keep the AI provider cache in sync",
            "After=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={_systemd_arg(str(work_dir))}",
            f"ExecStart={exec_start}",
            "Restart=always",
            "RestartSec=10",
            "Environment=PYTHONUNBUFFERED=1",
            f"Environment=PATH={SERVICE_PATH}",
            f"StandardOutput=append:{_systemd_arg(str(out_log))}",
            f"StandardError=append:{_systemd_arg(str(err_log))}",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _install_systemd(report: dict[str, Any], plan: dict[str, Any], dry_run: bool) -> None:
    unit = _linux_unit()
    out_log, err_log = _log_paths()
    commands = [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", LINUX_UNIT],
        ["systemctl", "--user", "restart", LINUX_UNIT],
    ]
    report.update(
        {
            "unit": str(unit),
            "unit_kind": "unit",
            "unit_exists": _exists(unit),
            "logs": {"out": str(out_log), "err": str(err_log)},
            "commands": commands,
        }
    )
    if not _linger_enabled():
        user = getpass.getuser()
        report["warnings"].append(
            f"lingering is disabled for {user}: this user unit stops when the last "
            f"session ends. Enable it once with: sudo loginctl enable-linger {user}"
        )
    if dry_run:
        report["message"] = f"dry run: would write {unit} and enable {LINUX_UNIT}"
        return

    try:
        out_log.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(
            unit,
            _systemd_unit(plan["argv"], out_log, err_log, plan["work_dir"]).encode("utf-8"),
        )
        for argv in commands:
            proc = _run(argv)
            if proc.returncode != 0:
                _fail(report, f"{subprocess.list2cmdline(argv)} failed: {_detail(proc)}")
                return
    except ServiceError as exc:
        _fail(
            report,
            f"{exc}; a systemd user session needs a login session "
            f"(or `sudo loginctl enable-linger {getpass.getuser()}`)",
        )
        return
    except OSError as exc:
        _fail(report, f"cannot write {unit}: {exc}")
        return

    report["unit_exists"] = _exists(unit)
    report["message"] = f"{LINUX_UNIT} installed; logs: {out_log}"


def _install_schtasks(report: dict[str, Any], plan: dict[str, Any], dry_run: bool) -> None:
    command = subprocess.list2cmdline(plan["argv"])
    argv = [
        "schtasks",
        "/Create",
        "/F",
        "/SC",
        "ONLOGON",
        "/TN",
        WINDOWS_TASK,
        "/TR",
        command,
    ]
    report.update(
        {
            "unit": WINDOWS_TASK,
            "unit_kind": "task",
            "commands": [argv],
        }
    )
    report["notes"].append(
        "the task runs at this user's logon; /SC ONSTART (boot, before logon) needs "
        "an administrator shell and is not attempted"
    )
    report["warnings"].append(
        "Task Scheduler discards stdout/stderr: the --json stream is not persisted; "
        "use `ai-rulec service status` for live state"
    )
    if dry_run:
        report["message"] = f"dry run: would register the {WINDOWS_TASK} task"
        return

    try:
        proc = _run(argv)
    except ServiceError as exc:
        _fail(report, str(exc))
        return
    if proc.returncode != 0:
        _fail(report, f"schtasks /Create failed: {_detail(proc)}")
        return
    report["message"] = f"task {WINDOWS_TASK} registered; run `ai-rulec service status`"


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


def status() -> dict[str, Any]:
    """Ask the platform's service manager whether the unit exists and runs."""
    report = _report("status")
    backend = report["backend"]
    report["installed"] = False
    report["running"] = False
    report["detail"] = ""

    try:
        if backend == "launchd":
            _status_launchd(report)
        elif backend == "systemd":
            _status_systemd(report)
        elif backend == "schtasks":
            _status_schtasks(report)
        else:
            _fail(
                report,
                f"no supported service backend for platform {report['platform']!r}",
            )
    except ServiceError as exc:
        _fail(report, str(exc))
    return report


def _status_launchd(report: dict[str, Any]) -> None:
    plist = _macos_plist()
    out_log, err_log = _log_paths()
    report.update(
        {
            "unit": str(plist),
            "unit_kind": "plist",
            "unit_exists": _exists(plist),
            "installed": _exists(plist),
            "logs": {"out": str(out_log), "err": str(err_log)},
        }
    )
    if platforms.which_exe("launchctl") is None:
        _fail(report, "launchctl not found; this is not a macOS service manager")
        return
    proc = _run(["launchctl", "print", _launchd_target()])
    report["running"] = proc.returncode == 0
    if proc.returncode == 0:
        report["detail"] = "\n".join((proc.stdout or "").splitlines()[:8])
    else:
        report["detail"] = f"{MACOS_LABEL} is not loaded"
    report["log_tail"] = {
        "err": _tail(err_log),
        "out": _tail(out_log),
    }


def _status_systemd(report: dict[str, Any]) -> None:
    unit = _linux_unit()
    out_log, err_log = _log_paths()
    report.update(
        {
            "unit": str(unit),
            "unit_kind": "unit",
            "unit_exists": _exists(unit),
            "installed": _exists(unit),
            "logs": {"out": str(out_log), "err": str(err_log)},
        }
    )
    if platforms.which_exe("systemctl") is None:
        _fail(report, "systemctl not found; this is not a systemd machine")
        return
    active = _run(["systemctl", "--user", "is-active", LINUX_UNIT])
    enabled = _run(["systemctl", "--user", "is-enabled", LINUX_UNIT])
    report["running"] = active.returncode == 0
    report["enabled"] = enabled.returncode == 0
    report["detail"] = (
        f"active={(active.stdout or '').strip() or 'unknown'} "
        f"enabled={(enabled.stdout or '').strip() or 'unknown'}"
    )
    report["log_tail"] = {
        "err": _tail(err_log),
        "out": _tail(out_log),
    }


def _status_schtasks(report: dict[str, Any]) -> None:
    report.update({"unit": WINDOWS_TASK, "unit_kind": "task"})
    if platforms.which_exe("schtasks") is None:
        _fail(report, "schtasks not found; this is not a Windows machine")
        return
    proc = _run(["schtasks", "/Query", "/TN", WINDOWS_TASK, "/FO", "LIST"])
    report["installed"] = proc.returncode == 0
    if proc.returncode != 0:
        report["detail"] = f"{WINDOWS_TASK} is not registered"
        return
    output = proc.stdout or ""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    report["detail"] = " ".join(lines[:4])
    state = _windows_task_state()
    if state is not None:
        report["running"] = state == TASK_STATE_RUNNING
        report["state"] = state
    else:
        report["running"] = _task_running_from_text(output)


# --------------------------------------------------------------------------- #
# Uninstall
# --------------------------------------------------------------------------- #


def uninstall(*, dry_run: bool = False) -> dict[str, Any]:
    """Stop and remove the unit.  Returns a report, never raises."""
    report = _report("uninstall")
    report["dry_run"] = dry_run
    backend = report["backend"]
    if backend == "launchd":
        _uninstall_launchd(report, dry_run)
    elif backend == "systemd":
        _uninstall_systemd(report, dry_run)
    elif backend == "schtasks":
        _uninstall_schtasks(report, dry_run)
    else:
        _fail(
            report,
            f"no supported service backend for platform {report['platform']!r}",
        )
    return report


def _uninstall_launchd(report: dict[str, Any], dry_run: bool) -> None:
    plist = _macos_plist()
    command = ["launchctl", "bootout", _launchd_target()]
    report.update(
        {
            "unit": str(plist),
            "unit_kind": "plist",
            "unit_exists": _exists(plist),
            "commands": [command],
        }
    )
    if dry_run:
        report["message"] = f"dry run: would unload {MACOS_LABEL} and remove {plist}"
        return
    try:
        with contextlib.suppress(ServiceError):
            _run(command)  # not loaded is fine
        plist.unlink(missing_ok=True)
    except OSError as exc:
        _fail(report, f"cannot remove {plist}: {exc}")
        return
    report["unit_exists"] = False
    report["message"] = f"removed {MACOS_LABEL}"


def _uninstall_systemd(report: dict[str, Any], dry_run: bool) -> None:
    unit = _linux_unit()
    commands = [
        ["systemctl", "--user", "disable", "--now", LINUX_UNIT],
        ["systemctl", "--user", "daemon-reload"],
    ]
    report.update(
        {
            "unit": str(unit),
            "unit_kind": "unit",
            "unit_exists": _exists(unit),
            "commands": commands,
        }
    )
    if dry_run:
        report["message"] = f"dry run: would disable {LINUX_UNIT} and remove {unit}"
        return
    try:
        for argv in commands:
            proc = _run(argv)
            # "not loaded" / "no such unit" means it is already gone.
            if proc.returncode != 0 and not _looks_absent(
                (proc.stderr or "") + (proc.stdout or "")
            ):
                _fail(report, f"{subprocess.list2cmdline(argv)} failed: {_detail(proc)}")
                return
        unit.unlink(missing_ok=True)
    except ServiceError as exc:
        with contextlib.suppress(OSError):
            unit.unlink(missing_ok=True)
        _fail(report, str(exc))
        return
    except OSError as exc:
        _fail(report, f"cannot remove {unit}: {exc}")
        return
    report["unit_exists"] = False
    report["message"] = f"removed {LINUX_UNIT}"


def _uninstall_schtasks(report: dict[str, Any], dry_run: bool) -> None:
    command = ["schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F"]
    report.update(
        {
            "unit": WINDOWS_TASK,
            "unit_kind": "task",
            "commands": [command],
        }
    )
    if dry_run:
        report["message"] = f"dry run: would delete the {WINDOWS_TASK} task"
        return
    try:
        proc = _run(command)
    except ServiceError as exc:
        _fail(report, str(exc))
        return
    if proc.returncode != 0:
        if _looks_absent((proc.stderr or "") + (proc.stdout or "")):
            report["message"] = f"{WINDOWS_TASK} was not registered"
            return
        _fail(report, f"schtasks /Delete failed: {_detail(proc)}")
        return
    report["installed"] = False
    report["message"] = f"deleted the {WINDOWS_TASK} task"


# --------------------------------------------------------------------------- #
# Describe
# --------------------------------------------------------------------------- #


def describe(
    *,
    meta: str | None = None,
    interval: float = DEFAULT_INTERVAL,
    agents_interval: float = DEFAULT_AGENTS_INTERVAL,
    python: str | None = None,
) -> dict[str, Any]:
    """What would be installed and run on this machine.  Writes nothing."""
    report = _report("describe")
    plan = _plan(
        meta=meta,
        interval=interval,
        agents_interval=agents_interval,
        python=python,
        report=report,
        probe_pyyaml=False,
        require_meta=False,
    )
    if plan is None:
        return report
    exe = plan["argv"][0]
    meta_path = plan["meta"]
    argv = plan["argv"]
    report.update(
        {
            "python": exe,
            "meta": str(meta_path),
            "meta_exists": meta_path.is_file(),
            "work_dir": str(plan["work_dir"]),
            "working_dir": str(plan["work_dir"]),
            "argv": argv,
            "command": subprocess.list2cmdline(argv),
        }
    )
    backend = report["backend"]
    if backend == "launchd":
        plist = _macos_plist()
        out_log, err_log = _log_paths()
        report.update(
            {
                "unit": str(plist),
                "unit_kind": "plist",
                "unit_exists": _exists(plist),
                "payload": _macos_payload(plan["argv"], plan["work_dir"]),
                "logs": {"out": str(out_log), "err": str(err_log)},
                "commands": [
                    ["launchctl", "bootout", _launchd_target()],
                    ["launchctl", "bootstrap", f"gui/{_uid()}", str(plist)],
                    ["launchctl", "kickstart", "-k", _launchd_target()],
                ],
            }
        )
    elif backend == "systemd":
        unit = _linux_unit()
        out_log, err_log = _log_paths()
        report.update(
            {
                "unit": str(unit),
                "unit_kind": "unit",
                "unit_exists": _exists(unit),
                "logs": {"out": str(out_log), "err": str(err_log)},
                "commands": [
                    ["systemctl", "--user", "daemon-reload"],
                    ["systemctl", "--user", "enable", "--now", LINUX_UNIT],
                    ["systemctl", "--user", "restart", LINUX_UNIT],
                ],
            }
        )
        if not _linger_enabled():
            user = getpass.getuser()
            report["notes"].append(
                f"lingering is off for {user}: enable with "
                f"`sudo loginctl enable-linger {user}` or the unit stops at logout"
            )
    elif backend == "schtasks":
        report.update(
            {
                "unit": WINDOWS_TASK,
                "unit_kind": "task",
                "commands": [
                    [
                        "schtasks",
                        "/Create",
                        "/F",
                        "/SC",
                        "ONLOGON",
                        "/TN",
                        WINDOWS_TASK,
                        "/TR",
                        subprocess.list2cmdline(argv),
                    ]
                ],
            }
        )
        report["notes"].append(
            "/SC ONLOGON only; /SC ONSTART needs an administrator shell"
        )
        report["warnings"].append(
            "Task Scheduler does not redirect stdout/stderr to a file"
        )
    else:
        report["notes"].append(
            "no supported service backend; run `ai-rulec watch` in a terminal"
        )
        _fail(
            report,
            f"no supported service backend for platform {report['platform']!r}",
        )
    return report
