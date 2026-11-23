from __future__ import annotations

import contextlib
import io
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from ai_rulec.cli import main
from ai_rulec.health import (
    check_agent,
    check_agents,
    check_http,
    diagnose,
    load_agents,
    proxy_skip,
    proxy_url_from_config,
)
from ai_rulec.status import profile_status


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        if self.path == "/ok":
            body = b"ok"
            self.send_response(200)
        elif self.path == "/missing":
            body = b"not found"
            self.send_response(404)
        elif self.path == "/forbidden":
            body = b"forbidden"
            self.send_response(403)
        else:
            body = b"unknown"
            self.send_response(500)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # noqa: D401 - silence test server
        return


class HealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_http_expected_status(self) -> None:
        result = check_http({"url": f"{self.base}/ok", "expect": [200]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertIsNone(result["error"])

    def test_http_unexpected_status(self) -> None:
        result = check_http({"url": f"{self.base}/missing", "expect": [200]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 404)
        self.assertEqual(result["error_type"], "http")

    def test_required_failure_is_down(self) -> None:
        agent = {
            "name": "demo",
            "checks": [
                {
                    "id": "missing",
                    "type": "http",
                    "url": f"{self.base}/missing",
                    "expect": [200],
                }
            ],
        }
        result = check_agent(agent)
        self.assertEqual(result["status"], "down")
        self.assertEqual(result["summary"]["required_failed"], 1)

    def test_optional_failure_degrades(self) -> None:
        agent = {
            "name": "demo",
            "checks": [
                {
                    "id": "ok",
                    "type": "http",
                    "url": f"{self.base}/ok",
                    "expect": [200],
                },
                {
                    "id": "optional",
                    "type": "http",
                    "url": f"{self.base}/missing",
                    "expect": [200],
                    "required": False,
                },
            ],
        }
        result = check_agent(agent)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["summary"]["optional_failed"], 1)

    def test_missing_optional_binary_is_skipped(self) -> None:
        agent = {
            "name": "demo",
            "checks": [
                {
                    "id": "cli",
                    "type": "binary",
                    "binary": "ai-rulec-no-such-binary-xyz",
                    "required": False,
                }
            ],
        }
        result = check_agent(agent)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"]["skipped"], 1)

    def test_proxy_url_from_config(self) -> None:
        self.assertEqual(
            proxy_url_from_config({"mixed-port": 6152}),
            "http://127.0.0.1:6152",
        )
        self.assertEqual(
            proxy_url_from_config({"port": "7890"}),
            "http://127.0.0.1:7890",
        )

    def test_load_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.yaml"
            path.write_text(
                yaml.safe_dump(
                    {
                        "agents": {
                            "demo": {
                                "display_name": "Demo",
                                "checks": [
                                    {
                                        "type": "http",
                                        "url": f"{self.base}/ok",
                                    }
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            agents = load_agents(path)
        self.assertIn("demo", agents)
        self.assertEqual(agents["demo"]["checks"][0]["id"], "http-1")
        self.assertTrue(agents["demo"]["checks"][0]["required"])

    def test_diagnose_proxy_route_and_proxy_down(self) -> None:
        failed = {
            "status": "down",
            "checks": [{"ok": False, "error_type": "network"}],
            "reason": "connection refused",
        }
        ok = {"status": "ok", "checks": [], "reason": None}
        self.assertEqual(diagnose(failed, direct=ok)["code"], "proxy_route")
        self.assertEqual(
            diagnose(failed, direct=ok, proxy_open=False)["code"],
            "proxy_down",
        )

    def test_check_agents_and_cli_json(self) -> None:
        agents = {
            "demo": {
                "name": "demo",
                "display_name": "Demo",
                "checks": [
                    {
                        "id": "ok",
                        "type": "http",
                        "url": f"{self.base}/ok",
                        "expect": [200],
                        "required": True,
                    }
                ],
            }
        }
        report = check_agents(agents, proxy_url=None)
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"]["ok"], 1)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agents.yaml"
            path.write_text(
                yaml.safe_dump(
                    {
                        "agents": {
                            "demo": {
                                "display_name": "Demo",
                                "checks": [
                                    {
                                        "type": "http",
                                        "url": f"{self.base}/ok",
                                        "expect": [200],
                                    }
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "health",
                        "--agents",
                        str(path),
                        "--proxy",
                        "direct",
                        "--json",
                    ]
                )
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["agents"][0]["status"], "ok")




class ProxySkipTests(unittest.TestCase):
    """A resolved-but-dead proxy must skip the sweep, not fall back to direct."""

    def test_closed_port_reports_a_reason(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        # Port was bound then released: nothing listens on it now.
        reason = proxy_skip(f"http://127.0.0.1:{port}")
        self.assertIn("is not listening", reason or "")

    def test_open_port_and_direct_route_are_allowed(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertIsNone(
                proxy_skip(f"http://127.0.0.1:{server.server_port}")
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertIsNone(proxy_skip(None))

    def test_compare_reports_that_it_needs_a_listening_proxy(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                [
                    "health",
                    "--proxy",
                    f"http://127.0.0.1:{port}",
                    "--compare",
                    "--timeout",
                    "8",
                    "--json",
                ]
            )
        self.assertEqual(code, 2)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload["compare_skipped"])
        self.assertEqual(payload["agents"], [])

    def test_cli_health_skips_instead_of_probing_directly(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        dead = f"http://127.0.0.1:{port}"
        output = io.StringIO()
        started = time.monotonic()
        with contextlib.redirect_stdout(output):
            code = main(["health", "--proxy", dead, "--timeout", "8", "--json"])
        elapsed = time.monotonic() - started
        payload = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertIsNone(payload["ok"])
        self.assertIn("is not listening", payload["skipped"])
        self.assertEqual(payload["agents"], [])
        self.assertLess(elapsed, 5.0, "health still probed every agent")


class StatusSkipTests(unittest.TestCase):
    def test_status_reports_skipped_agents_when_proxy_is_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "profiles" / "demo.yaml"
            profile.parent.mkdir(parents=True)
            profile.write_text(
                yaml.safe_dump(
                    {"proxies": [], "proxy-groups": [], "rules": []},
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (root / "config.yaml").write_text(
                yaml.safe_dump({"mixed-port": 65001}, sort_keys=False),
                encoding="utf-8",
            )
            agents = root / "agents.yaml"
            agents.write_text(
                yaml.safe_dump(
                    {"agents": [{"id": "demo", "checks": []}]}, sort_keys=False
                ),
                encoding="utf-8",
            )
            report = profile_status(
                str(profile),
                agents_path=str(agents),
                proxy_url="http://127.0.0.1:64999",
                check_agents_run=True,
            )
        self.assertEqual(report["health"]["ok"], None)
        self.assertIn("is not listening", report["health"]["skipped"])
        self.assertTrue(
            any("agent health skipped" in w for w in report["warnings"]),
            report["warnings"],
        )


if __name__ == "__main__":
    unittest.main()
