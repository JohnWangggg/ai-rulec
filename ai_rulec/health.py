from __future__ import annotations

import os
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from . import platforms

from .profile import discover_profiles, pick_profile

USER_AGENT = "ai-rulec-health/0.1"
DEFAULT_TIMEOUT = 6.0
DEFAULT_WORKERS = 6
DEFAULT_CHECKS = [200]
# Transport failures (timeout/tls/network) are retried; an HTTP status is an
# answer and is never retried.  Two retries keep a single cycle bounded:
# at most MAX_ATTEMPTS attempts of `timeout` seconds plus capped backoff.
DEFAULT_RETRIES = 2
MAX_ATTEMPTS = 4
RETRY_BACKOFF = 0.4


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _latency(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _expect(value: Any) -> list[int]:
    if value is None:
        return list(DEFAULT_CHECKS)
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        return [int(part.strip()) for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        codes: list[int] = []
        for item in value:
            codes.extend(_expect(item))
        return codes
    raise ValueError(f"invalid expect value: {value!r}")


def proxy_url_from_config(data: dict[str, Any]) -> str:
    port = data.get("mixed-port") or data.get("port") or 7890
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 7890
    return f"http://127.0.0.1:{port}"


def _config_for(path: Path) -> Path | None:
    for name in ("config.yaml", "config.yml"):
        candidate = path.parent / name
        if candidate.is_file():
            return candidate
    for parent in path.parents:
        for name in ("config.yaml", "config.yml"):
            candidate = parent / name
            if candidate.is_file():
                return candidate
    return None


def resolve_proxy_url(roots: list[str] | None = None) -> str | None:
    discovered = discover_profiles(roots=roots)
    picked = pick_profile(discovered.get("profiles") or [])
    ordered = ([picked] if picked else []) + [
        item for item in discovered.get("profiles", []) if item is not picked
    ]
    for profile in ordered:
        path = Path(profile["path"])
        config_path = _config_for(path)
        if config_path is None:
            continue
        try:
            with config_path.open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            return proxy_url_from_config(data)
    return None


def _opener(proxy_url: str | None) -> urllib.request.OpenerDirector:
    if proxy_url:
        handler = urllib.request.ProxyHandler(
            {"http": proxy_url, "https": proxy_url}
        )
    else:
        handler = urllib.request.ProxyHandler({})
    context = ssl.create_default_context()
    return urllib.request.build_opener(
        handler, urllib.request.HTTPSHandler(context=context)
    )


def _error_type(exc: BaseException) -> str:
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    text = str(reason).lower()
    if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in text:
        return "timeout"
    if isinstance(reason, socket.gaierror) or "name or service not known" in text:
        return "dns"
    if isinstance(reason, ssl.SSLError) or "ssl" in text or "certificate" in text:
        return "tls"
    if isinstance(reason, ConnectionRefusedError) or "refused" in text:
        return "network"
    return "network"


def check_http(
    check: dict[str, Any],
    *,
    proxy_url: str | None = None,
    default_timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    url = str(check.get("url", ""))
    if not url:
        raise ValueError("http check requires url")
    timeout = float(check.get("timeout", default_timeout))
    expected = _expect(check.get("expect"))
    method = str(check.get("method", "GET")).upper()
    headers = {"User-Agent": USER_AGENT}
    headers.update({str(k): str(v) for k, v in (check.get("headers") or {}).items()})
    body = check.get("body")
    data = body.encode("utf-8") if isinstance(body, str) else body

    request = urllib.request.Request(
        url, data=data, headers=headers, method=method
    )
    started = time.monotonic()
    attempts = max(
        1, min(int(check.get("retries", DEFAULT_RETRIES)) + 1, MAX_ATTEMPTS)
    )
    status: int | None = None
    error: str | None = None
    error_type: str | None = None
    excerpt = ""
    final_url = url

    used = 0
    for attempt in range(attempts):
        used = attempt + 1
        status = None
        error = None
        error_type = None
        excerpt = ""
        try:
            with _opener(proxy_url).open(request, timeout=timeout) as response:
                status = int(response.status)
                final_url = response.geturl()
                raw = response.read(4096)
                excerpt = raw.decode("utf-8", "replace")[:512].replace("\n", " ").strip()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            final_url = exc.geturl() or url
            try:
                excerpt = (
                    exc.read(4096)
                    .decode("utf-8", "replace")[:512]
                    .replace("\n", " ")
                    .strip()
                )
            except OSError:
                excerpt = ""
            if status not in expected:
                error = f"HTTP {status}"
                error_type = "http"
        except urllib.error.URLError as exc:
            error_type = _error_type(exc)
            error = f"{type(exc.reason).__name__}: {exc.reason}"
        except (TimeoutError, socket.timeout) as exc:
            error_type = "timeout"
            error = f"{type(exc).__name__}: {exc}"
        except ssl.SSLError as exc:
            error_type = "tls"
            error = f"{type(exc).__name__}: {exc}"
        except OSError as exc:
            error_type = _error_type(exc)
            error = f"{type(exc).__name__}: {exc}"

        if status in expected:
            error = None
            error_type = None
            break
        # Only transport errors are worth retrying; an HTTP status is an answer.
        if error_type not in {"timeout", "tls", "network"} or attempt + 1 >= attempts:
            break
        time.sleep(RETRY_BACKOFF * (attempt + 1))

    ok = status in expected
    if ok:
        error = None
        error_type = None
    return {
        "type": "http",
        "attempts": used,
        "attempts_limit": attempts,
        "url": url,
        "final_url": final_url,
        "method": method,
        "expected": expected,
        "status": status,
        "ok": ok,
        "error": error,
        "error_type": error_type,
        "latency_ms": _latency(started),
        "body_excerpt": excerpt,
        "route": "proxy" if proxy_url else "direct",
        "checked_at": _now(),
    }


def check_binary(
    check: dict[str, Any], *, timeout: float = 3.0
) -> dict[str, Any]:
    binary = str(check.get("binary", ""))
    if not binary:
        raise ValueError("binary check requires binary")
    started = time.monotonic()
    path = platforms.which_exe(binary)
    if path is None and os.path.isabs(binary) and os.access(binary, os.X_OK):
        path = binary
    if path is None:
        return {
            "type": "binary",
            "binary": binary,
            "ok": False,
            "skipped": True,
            "path": None,
            "version": None,
            "returncode": None,
            "error": "not found",
            "error_type": "binary",
            "latency_ms": _latency(started),
            "checked_at": _now(),
        }

    args = [str(item) for item in (check.get("args") or ["--version"])]
    expected = _expect(check.get("expect", [0]))
    try:
        completed = subprocess.run(
            [path, *args],
            capture_output=True,
            text=True,
            timeout=float(check.get("timeout", timeout)),
            check=False,
        )
        output = (completed.stdout or completed.stderr or "").strip()
        ok = completed.returncode in expected
        return {
            "type": "binary",
            "binary": binary,
            "ok": ok,
            "skipped": False,
            "path": path,
            "version": output.splitlines()[0][:160] if output else None,
            "returncode": completed.returncode,
            "error": None if ok else f"exit {completed.returncode}",
            "error_type": None if ok else "binary",
            "latency_ms": _latency(started),
            "checked_at": _now(),
        }
    except subprocess.TimeoutExpired:
        return {
            "type": "binary",
            "binary": binary,
            "ok": False,
            "skipped": False,
            "path": path,
            "version": None,
            "returncode": None,
            "error": "timeout",
            "error_type": "timeout",
            "latency_ms": _latency(started),
            "checked_at": _now(),
        }
    except OSError as exc:
        return {
            "type": "binary",
            "binary": binary,
            "ok": False,
            "skipped": False,
            "path": path,
            "version": None,
            "returncode": None,
            "error": f"{type(exc).__name__}: {exc}",
            "error_type": "binary",
            "latency_ms": _latency(started),
            "checked_at": _now(),
        }


def load_agents(path: str | Path) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
        raise ValueError(f"{path}: expected an agents mapping")
    agents: dict[str, dict[str, Any]] = {}
    for name, raw in data["agents"].items():
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: agent {name!r} must be a mapping")
        checks = raw.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"{path}: agent {name!r} needs checks")
        normalized: list[dict[str, Any]] = []
        for check in checks:
            if not isinstance(check, dict):
                raise ValueError(f"{path}: agent {name!r} check must be a mapping")
            kind = str(check.get("type", "http"))
            if kind not in {"http", "binary"}:
                raise ValueError(f"{path}: unsupported check type {kind!r}")
            if kind == "http" and not check.get("url"):
                raise ValueError(f"{path}: http check in {name!r} needs url")
            if kind == "binary" and not check.get("binary"):
                raise ValueError(f"{path}: binary check in {name!r} needs binary")
            item = dict(check)
            item["type"] = kind
            item["required"] = bool(item.get("required", True))
            item["id"] = str(item.get("id") or f"{kind}-{len(normalized) + 1}")
            normalized.append(item)
        item = dict(raw)
        item["name"] = name
        item["display_name"] = str(raw.get("display_name", name))
        item["checks"] = normalized
        item.setdefault("aliases", [])
        item.setdefault("service", None)
        item.setdefault("process_names", [])
        agents[name] = item
    return agents


def check_agent(
    agent: dict[str, Any],
    *,
    proxy_url: str | None = None,
    default_timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for check in agent.get("checks", []):
        kind = check["type"]
        if kind == "http":
            result = check_http(
                check, proxy_url=proxy_url, default_timeout=default_timeout
            )
        else:
            result = check_binary(check)
        result["id"] = check.get("id")
        result["required"] = bool(check.get("required", True))
        results.append(result)

    required_failed = [
        item for item in results if item["required"] and not item["ok"] and not item.get("skipped")
    ]
    optional_failed = [
        item for item in results if not item["required"] and not item["ok"] and not item.get("skipped")
    ]
    skipped = [item for item in results if item.get("skipped")]

    def _reason(item: dict[str, Any]) -> str:
        label = item.get("id") or item.get("type") or "check"
        detail = item.get("error_type") or item.get("error") or "failed"
        if item.get("skipped"):
            detail = "not installed"
        return f"{label}: {detail}"

    if required_failed:
        status = "down"
        reason = _reason(required_failed[0])
    elif optional_failed:
        status = "degraded"
        reason = _reason(optional_failed[0])
    else:
        status = "ok"
        reason = None

    return {
        "name": agent["name"],
        "display_name": agent.get("display_name", agent["name"]),
        "service": agent.get("service"),
        "process_names": agent.get("process_names", []),
        "status": status,
        "reason": reason,
        "route": "proxy" if proxy_url else "direct",
        "checks": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for item in results if item["ok"]),
            "failed": len(required_failed) + len(optional_failed),
            "required_failed": len(required_failed),
            "optional_failed": len(optional_failed),
            "skipped": len(skipped),
        },
        "checked_at": _now(),
    }


def check_agents(
    agents: dict[str, dict[str, Any]],
    names: list[str] | None = None,
    *,
    proxy_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
) -> dict[str, Any]:
    selected = names or list(agents)
    missing = [name for name in selected if name not in agents]
    if missing:
        raise KeyError(f"unknown agent(s): {', '.join(missing)}")
    workers = max(1, min(int(workers), len(selected) or 1))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(
                lambda name: check_agent(
                    agents[name], proxy_url=proxy_url, default_timeout=timeout
                ),
                selected,
            )
        )

    summary = {
        "total": len(results),
        "ok": sum(1 for item in results if item["status"] == "ok"),
        "degraded": sum(1 for item in results if item["status"] == "degraded"),
        "down": sum(1 for item in results if item["status"] == "down"),
    }
    return {
        "proxy": proxy_url or "direct",
        "agents": results,
        "summary": summary,
        "ok": summary["down"] == 0,
    }


def diagnose(
    result: dict[str, Any],
    *,
    direct: dict[str, Any] | None = None,
    proxy_open: bool | None = None,
) -> dict[str, str] | None:
    if result.get("status") == "ok":
        return None
    if proxy_open is False:
        return {"code": "proxy_down", "message": "local proxy port is not listening"}
    if direct is not None and direct.get("status") == "ok":
        return {
            "code": "proxy_route",
            "message": "direct route works; current proxy route does not",
        }
    if direct is not None and direct.get("status") != "ok":
        return {
            "code": "upstream",
            "message": "both proxy and direct routes fail; check network/upstream",
        }
    if result.get("status") == "degraded":
        return {"code": "optional_check", "message": result.get("reason") or "optional check failed"}
    error_types = {
        item.get("error_type")
        for item in result.get("checks", [])
        if not item.get("ok") and not item.get("skipped")
    }
    for code in ("dns", "tls", "timeout", "network"):
        if code in error_types:
            return {"code": code, "message": f"{code} failure on required check"}
    return {"code": "unknown", "message": result.get("reason") or "unknown failure"}


def port_open(proxy_url: str | None, timeout: float = 0.5) -> bool | None:
    if not proxy_url:
        return None
    parsed = urlsplit(proxy_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def proxy_skip(proxy_url: str | None, timeout: float = 0.5) -> str | None:
    """Reason agent checks must wait, or None when the route is usable.

    A resolved-but-dead proxy is the normal state when the client is closed.
    Checking every agent over the direct route then costs a full timeout per
    endpoint and reports failures that say nothing about the client, so the
    caller skips the sweep and says why instead.
    """
    if not proxy_url:
        return None
    if port_open(proxy_url, timeout=timeout):
        return None
    return f"{proxy_url} is not listening"
