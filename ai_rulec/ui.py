"""Local web console: the same actions as the CLI, without memorising flags.

Design constraints:

* Loopback only. A random token is required on every request, and cross-origin
  requests are rejected, so a random web page cannot drive the proxy.
* No external assets and no bundler: one HTML string, plain ``fetch``.
* Long actions (rebuild, apply) run through the CLI in a subprocess so the UI
  cannot drift from the command-line behaviour.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from . import paths
from .profile import discover_profiles
from .status import profile_status

HEALTH_TTL = 60.0
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
ACTIONS = {
    "apply": ["apply", "--latest", "--json"],
    "doctor": ["doctor", "--latest", "--fix", "--json"],
    "rollback": ["rollback", "--latest", "--json"],
    "rebuild": [
        "build",
        "--out",
        "__OUT__",
        "--formats",
        "all",
        "--asn-mode",
        "drop",
        "--probe",
        "--probe-proxy",
        "auto",
    ],
}

_state_lock = threading.Lock()
_action_lock = threading.Lock()
_health_cache: dict[str, object] = {"at": 0.0, "data": None}


def _latest_profile() -> str | None:
    discovered = discover_profiles()
    profiles = discovered.get("profiles") or []
    return profiles[0]["path"] if profiles else None


def _group_egress(profile_path: str) -> dict[str, list[str]]:
    try:
        data = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out: dict[str, list[str]] = {}
    for group in data.get("proxy-groups") or []:
        name = group.get("name")
        if isinstance(name, str) and name.startswith("AI"):
            out[name] = [str(item) for item in group.get("proxies") or []]
    return out


def _state() -> dict[str, object]:
    profile = _latest_profile()
    if profile is None:
        return {"ok": False, "error": "no profile discovered"}
    try:
        report = profile_status(profile)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {"ok": False, "error": str(exc), "profile": profile}
    info = report.get("profile_info") or {}
    cache = report.get("cache") or {}
    return {
        "ok": report.get("ok"),
        "profile": report.get("profile"),
        "client": info.get("client"),
        "managed": info.get("managed"),
        "groups": info.get("proxy_groups"),
        "rules": info.get("rules"),
        "providers": info.get("providers"),
        "target": info.get("target_hint"),
        "port": report.get("port"),
        "port_open": report.get("port_open"),
        "cache": cache.get("summary"),
        "watch": report.get("watch"),
        "backups": report.get("backups") or {},
        "egress": _group_egress(profile),
        "warnings": report.get("warnings") or [],
        "errors": report.get("errors") or [],
    }


def _health(force: bool = False) -> dict[str, object]:
    now = time.time()
    with _state_lock:
        cached = _health_cache.get("data")
        if not force and cached is not None and now - float(_health_cache["at"]) < HEALTH_TTL:
            return cached  # type: ignore[return-value]
    proc = subprocess.run(
        [sys.executable, "-m", "ai_rulec", "health", "--json"],
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = {"ok": False, "error": (proc.stderr or proc.stdout).strip()[-2000:]}
    with _state_lock:
        _health_cache["at"] = now
        _health_cache["data"] = data
    return data


def _run_action(action: str) -> dict[str, object]:
    argv = ACTIONS.get(action)
    if argv is None:
        return {"ok": False, "error": f"unknown action: {action}"}
    out = str(paths.resolve_out())
    argv = [out if item == "__OUT__" else item for item in argv]
    started = time.time()
    with _action_lock:
        proc = subprocess.run(
            [sys.executable, "-m", "ai_rulec", *argv],
            capture_output=True,
            text=True,
            timeout=900,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
        )
    output = (proc.stdout or "").strip()
    try:
        parsed = json.loads(output)
    except ValueError:
        parsed = None
    return {
        "ok": proc.returncode == 0 and (parsed or {}).get("ok") is not False,
        "action": action,
        "returncode": proc.returncode,
        "seconds": round(time.time() - started, 1),
        "result": parsed,
        "output": output[-4000:],
        "stderr": (proc.stderr or "").strip()[-2000:],
    }


PAGE = """<!doctype html>
<html lang="zh">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ai-rulec</title>
<link rel="icon" href="data:,">
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#282d38;--fg:#e6e9ef;--muted:#98a0b3;
--ok:#3ecf8e;--warn:#e5b567;--bad:#ef6b73;--accent:#6aa9ff}
@media (prefers-color-scheme:light){:root{--bg:#f6f7fa;--card:#fff;--line:#e3e6ee;
--fg:#1a1d24;--muted:#5d6475;--accent:#2f6fed}}
*{box-sizing:border-box}
body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
background:var(--bg);color:var(--fg)}
main{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:20px;margin:0 0 4px;font-weight:650}
.sub{color:var(--muted);font-size:12px;word-break:break-all}
.row{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;flex:1 1 180px;min-width:160px}
.card .k{color:var(--muted);font-size:12px}
.card .v{font-size:22px;font-weight:600;margin-top:2px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.ok{background:var(--ok)}.warn{background:var(--warn)}.bad{background:var(--bad)}
button{background:var(--card);color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:9px 14px;font-size:13px;cursor:pointer}
button:hover{border-color:var(--accent)}
button.primary{background:var(--accent);color:#fff;border-color:transparent}
button:disabled{opacity:.5;cursor:default}
table{width:100%;border-collapse:collapse;margin-top:8px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--muted);font-weight:500;font-size:12px}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:12px;overflow:auto;max-height:260px;font-size:12px;white-space:pre-wrap}
.pill{font-size:11px;padding:2px 8px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
.tag{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
</style>
<main>
<h1>ai-rulec <span id="pill" class="pill">…</span></h1>
<div class="sub" id="profile">…</div>

<div class="row">
  <div class="card"><div class="k">规则组 / 规则 / provider</div><div class="v" id="counts">–</div></div>
  <div class="card"><div class="k">Provider 缓存</div><div class="v" id="cache">–</div></div>
  <div class="card"><div class="k">代理端口</div><div class="v" id="port">–</div></div>
  <div class="card"><div class="k">后台看护</div><div class="v" id="watch">–</div></div>
</div>

<div class="row">
  <button class="primary" data-action="apply">重新应用</button>
  <button data-action="doctor">修复缓存</button>
  <button data-action="rollback">回滚</button>
  <button data-action="rebuild">重建规则</button>
  <button id="refresh">刷新</button>
</div>

<h3>AI 出口</h3>
<table><thead><tr><th>组</th><th>出口</th></tr></thead><tbody id="egress"></tbody></table>

<h3>Agent 连通性 <span class="pill" id="health-at">–</span></h3>
<table><thead><tr><th>Agent</th><th>状态</th><th>原因</th></tr></thead><tbody id="agents"></tbody></table>

<h3>操作日志</h3>
<pre id="log">就绪。</pre>
</main>
<script>
const token = new URLSearchParams(location.search).get('token') || '';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const api = (path) => fetch(path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token));

function pill(ok) {
  const el = $('pill');
  el.textContent = ok ? '正常' : '需要注意';
  el.style.color = ok ? 'var(--ok)' : 'var(--warn)';
}
function dot(status) {
  const cls = status === 'ok' ? 'ok' : status === 'degraded' ? 'warn' : 'bad';
  return `<span class="dot ${cls}"></span>${esc(status)}`;
}
async function loadState() {
  const r = await api('/api/state');
  const s = await r.json();
  pill(s.ok);
  $('profile').textContent = s.profile || s.error || '';
  $('counts').textContent = s.error ? '–' : `${s.groups} / ${s.rules} / ${s.providers}`;
  const c = s.cache || {};
  $('cache').textContent = s.error ? '–' : `${c.ok ?? 0}/${c.managed ?? 0}`;
  $('port').textContent = s.error ? '–' : `${s.port ?? '?'} ${s.port_open ? '✓' : '✗'}`;
  const w = s.watch || {};
  $('watch').textContent = s.error ? '–' : (w.last_action || '无状态') + (w.stale ? ' (stale)' : '');
  $('egress').innerHTML = Object.entries(s.egress || {}).map(([name, list]) =>
    `<tr><td class="tag">${esc(name)}</td><td class="tag">${esc(list.join(' → '))}</td></tr>`).join('')
    || '<tr><td colspan="2" style="color:var(--muted)">暂无 AI 组</td></tr>';
}
async function loadHealth(force=false) {
  const r = await api('/api/health' + (force ? '?force=1' : ''));
  const h = await r.json();
  $('health-at').textContent = h.ok === undefined ? '不可用' : `${h.summary?.ok ?? 0} ok / ${h.summary?.degraded ?? 0} 降级 / ${h.summary?.down ?? 0} 断开`;
  $('agents').innerHTML = (h.agents || []).map((a) =>
    `<tr><td>${esc(a.display_name || a.name)}</td><td>${dot(a.status)}</td><td class="sub">${esc(a.reason || '')}</td></tr>`).join('')
    || '<tr><td colspan="3" style="color:var(--muted)">无可检查的 agent</td></tr>';
}
async function act(action) {
  const buttons = [...document.querySelectorAll('button[data-action]')];
  buttons.forEach((b) => b.disabled = true);
  $('log').textContent = `$ ai-rulec ${action}\n运行中…`;
  try {
    const r = await fetch('/api/action?token=' + encodeURIComponent(token), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action})
    });
    const out = await r.json();
    $('log').textContent = (out.output || out.error || '') + (out.stderr ? String.fromCharCode(10) + out.stderr : '');
    await loadState(); await loadHealth(true);
  } catch (e) {
    $('log').textContent = String(e);
  } finally {
    buttons.forEach((b) => b.disabled = false);
  }
}
document.querySelectorAll('button[data-action]').forEach((b) =>
  b.addEventListener('click', () => act(b.dataset.action)));
$('refresh').addEventListener('click', () => { loadState(); loadHealth(true); });
loadState(); loadHealth();
setInterval(loadState, 8000);
</script>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ai-rulec-ui"
    token = ""

    def log_message(self, *args):  # keep the terminal clean
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ALLOWED_HOSTS

    def _token_ok(self) -> bool:
        query = parse_qs(urlparse(self.path).query)
        supplied = (query.get("token") or [""])[0]
        return bool(self.token) and secrets.compare_digest(supplied, self.token)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlparse(origin).hostname in ALLOWED_HOSTS

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: object, status: int = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok() or not self._token_ok() or not self._origin_ok():
            self._json({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        path = urlparse(self.path).path
        if path == "/":
            self._send(HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/api/state":
            self._json(_state())
        elif path == "/api/health":
            force = (parse_qs(urlparse(self.path).query).get("force") or ["0"])[0] == "1"
            self._json(_health(force))
        else:
            self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_ok() or not self._token_ok() or not self._origin_ok():
            self._json({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
            return
        if urlparse(self.path).path != "/api/action":
            self._json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        action = payload.get("action") or (parse_qs(urlparse(self.path).query).get("action") or [""])[0]
        self._json(_run_action(str(action)))


def serve(port: int = 8787, *, open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(18)
    Handler.token = token
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise SystemExit(
            f"ui: cannot bind 127.0.0.1:{port} ({exc}); pass --port to pick another"
        ) from exc
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_address[1]}/?token={token}"
    print(f"ai-rulec ui: {url}")
    print("Ctrl-C to stop. The token is required; the server binds loopback only.")
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="ai-rulec ui")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)
    serve(args.port, open_browser=not args.no_open)
    return 0
