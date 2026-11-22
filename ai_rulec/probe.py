from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Iterable

USER_AGENT = "ai-rulec-probe/0.1"
DOH_RESOLVERS = (
    ("cloudflare", "https://cloudflare-dns.com/dns-query"),
    ("google", "https://dns.google/resolve"),
)
DOH_TIMEOUT = 3.0
PARKED_MARKERS = (
    "domain for sale",
    "buy this domain",
    "this domain is parked",
    "parked domain",
    "domain parking",
    "hugedomains.com",
    "sedo.com",
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _system_lookup(domain: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        infos = socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        return {
            "ok": False,
            "ips": [],
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": _ms(started),
        }
    ips: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in ips:
            ips.append(address)
    return {
        "ok": True,
        "ips": ips[:8],
        "error": None,
        "latency_ms": _ms(started),
    }


def _doh_lookup(domain: str, endpoint: str, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    query = urllib.parse.urlencode({"name": domain, "type": "A"})
    request = urllib.request.Request(
        f"{endpoint}?{query}",
        headers={"Accept": "application/dns-json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return {
            "ok": False,
            "ips": [],
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": _ms(started),
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "ips": [],
            "error": "invalid DoH response",
            "latency_ms": _ms(started),
        }
    ips: list[str] = []
    for answer in payload.get("Answer") or []:
        if not isinstance(answer, dict) or answer.get("type") not in (1, 28):
            continue
        value = str(answer.get("data", "")).strip()
        try:
            ipaddress.ip_address(value)
        except ValueError:
            continue
        if value not in ips:
            ips.append(value)
    status = payload.get("Status")
    if not ips:
        return {
            "ok": False,
            "ips": [],
            "error": f"no A/AAAA answer (Status={status})",
            "latency_ms": _ms(started),
        }
    return {
        "ok": True,
        "ips": ips[:8],
        "error": None,
        "latency_ms": _ms(started),
    }


def _dns_lookup(domain: str) -> dict[str, Any]:
    started = time.monotonic()
    system = _system_lookup(domain)
    resolvers: dict[str, Any] = {"system": system}
    if system["ok"]:
        return {
            "ok": True,
            "ips": system["ips"],
            "error": None,
            "latency_ms": _ms(started),
            "source": "system",
            "fallback": False,
            "resolvers": resolvers,
        }
    for name, endpoint in DOH_RESOLVERS:
        result = _doh_lookup(domain, endpoint, DOH_TIMEOUT)
        resolvers[name] = result
        if result["ok"]:
            return {
                "ok": True,
                "ips": result["ips"],
                "error": None,
                "latency_ms": _ms(started),
                "source": name,
                "fallback": True,
                "resolvers": resolvers,
            }
    return {
        "ok": False,
        "ips": [],
        "error": "all resolvers failed",
        "latency_ms": _ms(started),
        "source": None,
        "fallback": True,
        "resolvers": resolvers,
    }


def _san_matches(domain: str, san: str) -> bool:
    domain = domain.rstrip(".").lower()
    san = san.rstrip(".").lower()
    if san == domain:
        return True
    if san.startswith("*."):
        base = san[2:]
        return domain.endswith("." + base) and domain.count(".") == base.count(".") + 1
    return False


def _cert_name(value: Any) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for rdn in value:
        for key, item in rdn:
            parts.append(f"{key}={item}")
    return ", ".join(parts)


def _proxy_parts(proxy_url: str) -> tuple[str, int]:
    parts = urllib.parse.urlsplit(proxy_url if "//" in proxy_url else f"http://{proxy_url}")
    if not parts.hostname:
        raise ValueError(f"invalid proxy URL: {proxy_url}")
    return parts.hostname, parts.port or 8080


def _connect_via_proxy(domain: str, timeout: float, proxy_url: str) -> socket.socket:
    host, port = _proxy_parts(proxy_url)
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(
            f"CONNECT {domain}:443 HTTP/1.1\r\n"
            f"Host: {domain}:443\r\n"
            f"User-Agent: {USER_AGENT}\r\n\r\n".encode()
        )
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise OSError(f"proxy closed tunnel for {domain}")
            buffer += chunk
            if len(buffer) > 65536:
                raise OSError(f"proxy tunnel header too large for {domain}")
        status_line = buffer.split(b"\r\n", 1)[0].decode("latin-1")
        fields = status_line.split()
        code = int(fields[1]) if len(fields) > 1 and fields[1].isdigit() else 0
        if code != 200:
            raise OSError(f"proxy refused tunnel for {domain}: {status_line}")
        return sock
    except BaseException:
        sock.close()
        raise


def _connect(
    domain: str,
    timeout: float,
    connect_ips: list[str] | None,
    proxy_url: str | None = None,
) -> socket.socket:
    if proxy_url:
        return _connect_via_proxy(domain, timeout, proxy_url)
    if not connect_ips:
        return socket.create_connection((domain, 443), timeout=timeout)
    last_error: OSError | None = None
    for ip in connect_ips[:4]:
        try:
            return socket.create_connection((ip, 443), timeout=timeout)
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise OSError(f"no connect IP for {domain}")


def _tls_check(
    domain: str,
    timeout: float,
    connect_ips: list[str] | None = None,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    context = ssl.create_default_context()
    started = time.monotonic()
    try:
        with _connect(domain, timeout, connect_ips, proxy_url) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as tls:
                cert = tls.getpeercert() or {}
                version = tls.version()
                cipher = tls.cipher()[0] if tls.cipher() else None
    except ssl.SSLCertVerificationError as exc:
        message = getattr(exc, "verify_message", "") or str(exc)
        lowered = message.lower()
        mismatch = any(
            marker in lowered
            for marker in ("hostname mismatch", "doesn't match", "not valid for")
        )
        return {
            "ok": False,
            "hostname_match": False,
            "mismatch": mismatch,
            "san": [],
            "issuer": "",
            "subject": "",
            "not_after": None,
            "version": None,
            "cipher": None,
            "error": f"{type(exc).__name__}: {message}",
            "latency_ms": _ms(started),
        }
    except (OSError, ssl.SSLError, ValueError) as exc:
        return {
            "ok": False,
            "hostname_match": False,
            "mismatch": False,
            "san": [],
            "issuer": "",
            "subject": "",
            "not_after": None,
            "version": None,
            "cipher": None,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": _ms(started),
        }

    san = [
        str(value)
        for key, value in cert.get("subjectAltName", ())
        if key == "DNS"
    ]
    return {
        "ok": True,
        "hostname_match": True,
        "mismatch": False,
        "san": san,
        "issuer": _cert_name(cert.get("issuer")),
        "subject": _cert_name(cert.get("subject")),
        "not_after": cert.get("notAfter"),
        "version": version,
        "cipher": cipher,
        "error": None,
        "latency_ms": _ms(started),
    }


def _title(body: bytes) -> str:
    text = body.decode("utf-8", "ignore")
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:200]


def _http_check(
    domain: str,
    timeout: float,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    context = ssl._create_unverified_context()
    if proxy_url:
        proxy_host, proxy_port = _proxy_parts(proxy_url)
        connection = http.client.HTTPSConnection(
            proxy_host, proxy_port, timeout=timeout, context=context
        )
        connection.set_tunnel(domain, 443)
    else:
        connection = http.client.HTTPSConnection(
            domain, 443, timeout=timeout, context=context
        )
    started = time.monotonic()
    try:
        connection.request(
            "GET",
            "/",
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )
        response = connection.getresponse()
        body = response.read(4096)
        title = _title(body)
        haystack = (title + " " + body.decode("utf-8", "ignore")[:2048]).lower()
        return {
            "ok": True,
            "status": response.status,
            "server": response.getheader("server"),
            "content_type": response.getheader("content-type"),
            "location": response.getheader("location"),
            "title": title,
            "parked": any(marker in haystack for marker in PARKED_MARKERS),
            "error": None,
            "latency_ms": _ms(started),
        }
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError) as exc:
        return {
            "ok": False,
            "status": None,
            "server": None,
            "content_type": None,
            "location": None,
            "title": "",
            "parked": False,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_ms": _ms(started),
        }
    finally:
        try:
            connection.close()
        except OSError:
            pass


def probe_domain(
    domain: str,
    timeout: float = 4.0,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    dns = _dns_lookup(domain)
    if not dns["ok"]:
        tls = {
            "ok": False,
            "hostname_match": False,
            "mismatch": False,
            "san": [],
            "issuer": "",
            "subject": "",
            "not_after": None,
            "version": None,
            "cipher": None,
            "error": "skipped: dns failed",
            "latency_ms": 0,
        }
        http = {
            "ok": False,
            "status": None,
            "server": None,
            "content_type": None,
            "location": None,
            "title": "",
            "parked": False,
            "error": "skipped: dns failed",
            "latency_ms": 0,
        }
        status = "dns_fail"
    else:
        connect_ips = dns["ips"] if dns.get("fallback") else None
        tls = _tls_check(domain, timeout, connect_ips=connect_ips, proxy_url=proxy_url)
        if not tls["ok"]:
            http = {
                "ok": False,
                "status": None,
                "server": None,
                "content_type": None,
                "location": None,
                "title": "",
                "parked": False,
                "error": "skipped: tls failed",
                "latency_ms": 0,
            }
            status = "tls_mismatch" if tls.get("mismatch") else "tls_fail"
        else:
            if dns.get("fallback") and not proxy_url:
                http = {
                    "ok": False,
                    "status": None,
                    "server": None,
                    "content_type": None,
                    "location": None,
                    "title": "",
                    "parked": False,
                    "error": "skipped: local DNS unavailable",
                    "latency_ms": 0,
                }
            else:
                http = _http_check(domain, timeout, proxy_url=proxy_url)
            if http.get("parked"):
                status = "parked"
            elif not tls["hostname_match"]:
                status = "tls_mismatch"
            else:
                status = "ok"

    return {
        "domain": domain,
        "status": status,
        "dns": dns,
        "tls": tls,
        "http": http,
        "checked_at": _now(),
    }


def _safe_probe(
    domain: str,
    timeout: float,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    try:
        return probe_domain(domain, timeout, proxy_url)
    except Exception as exc:  # noqa: BLE001 - keep batch alive on probe bugs
        return {
            "domain": domain,
            "status": "probe_error",
            "dns": {
                "ok": False,
                "ips": [],
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": 0,
                "source": None,
                "fallback": False,
                "resolvers": {},
            },
            "tls": {"ok": False, "hostname_match": False, "mismatch": False, "san": [], "error": None, "latency_ms": 0},
            "http": {"ok": False, "parked": False, "error": None, "latency_ms": 0},
            "checked_at": _now(),
        }


def summarize_probes(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(results)
    counts = Counter(item.get("status", "unknown") for item in items)
    by_status = dict(sorted(counts.items()))
    return {
        "total": len(items),
        "ok": counts.get("ok", 0),
        "issues": len(items) - counts.get("ok", 0),
        "by_status": by_status,
        "http_errors": sum(1 for item in items if not item.get("http", {}).get("ok")),
        "dns_fallback": sum(1 for item in items if item.get("dns", {}).get("fallback")),
        "dns_fallback_domains": [
            item["domain"] for item in items if item.get("dns", {}).get("fallback")
        ][:50],
        "dns_fail_domains": [
            item["domain"] for item in items if item.get("status") == "dns_fail"
        ][:50],
        "tls_fail_domains": [
            item["domain"] for item in items if item.get("status") == "tls_fail"
        ][:50],
        "tls_mismatch_domains": [
            item["domain"] for item in items if item.get("status") == "tls_mismatch"
        ][:50],
        "parked_domains": [
            item["domain"] for item in items if item.get("status") == "parked"
        ][:50],
    }


def probe_domains(
    domains: Iterable[str],
    timeout: float = 4.0,
    workers: int = 24,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    unique = sorted({str(domain).strip().lower() for domain in domains if str(domain).strip()})
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(
            pool.map(lambda domain: _safe_probe(domain, timeout, proxy_url), unique)
        )
    return {
        "schema_version": 2,
        "probed_at": _now(),
        "timeout": timeout,
        "workers": workers,
        "proxy": proxy_url,
        "summary": summarize_probes(results),
        "domains": {item["domain"]: item for item in results},
    }


def load_probes(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else None
