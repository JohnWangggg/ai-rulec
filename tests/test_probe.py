"""Probe routing tests: DoH must use the same egress as the target connection."""

from __future__ import annotations

import os
import unittest
import unittest.mock
import urllib.request

from ai_rulec import probe


def _proxy_handler(opener: urllib.request.OpenerDirector):
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler):
            return handler
    return None


class DohOpenerTests(unittest.TestCase):
    def test_proxy_is_honoured(self) -> None:
        opener = probe._doh_opener("http://127.0.0.1:6152")
        handler = _proxy_handler(opener)
        self.assertIsNotNone(handler, "DoH opener has no ProxyHandler")
        self.assertEqual(handler.proxies.get("https"), "http://127.0.0.1:6152")
        self.assertEqual(handler.proxies.get("http"), "http://127.0.0.1:6152")

    def test_direct_ignores_the_environment_proxy(self) -> None:
        # Passing an empty ProxyHandler makes build_opener skip the default
        # one, which would otherwise read https_proxy from the environment and
        # silently make the probe depend on a variable nobody asked for.
        with unittest.mock.patch.dict(
            os.environ, {"https_proxy": "http://example.test:9999"}
        ):
            opener = probe._doh_opener(None)
        # No ProxyHandler at all: build_opener skipped its default one because
        # an explicit (empty) ProxyHandler was passed in.
        self.assertIsNone(_proxy_handler(opener))


class ProxyPlumbingTests(unittest.TestCase):
    def test_dns_lookup_hands_the_proxy_to_doh(self) -> None:
        seen = {}

        def fake_system(domain):
            return {"ok": False, "ips": [], "error": "no local resolver"}

        def fake_doh(domain, endpoint, timeout, proxy_url=None):
            seen["proxy"] = proxy_url
            return {"ok": True, "ips": ["127.0.0.1"], "endpoint": endpoint}

        with unittest.mock.patch.object(probe, "_system_lookup", fake_system):
            with unittest.mock.patch.object(probe, "_doh_lookup", fake_doh):
                result = probe._dns_lookup("example.test", "http://127.0.0.1:6152")

        self.assertTrue(result["ok"])
        self.assertEqual(seen["proxy"], "http://127.0.0.1:6152")
        self.assertEqual(result["source"], "cloudflare")

    def test_probe_domain_hands_the_proxy_to_tls(self) -> None:
        seen = {}
        dns = {"ok": True, "ips": ["127.0.0.1"], "source": "system"}

        def fake_tls(domain, timeout, connect_ips=None, proxy_url=None):
            seen["proxy"] = proxy_url
            return {
                "ok": False,
                "mismatch": False,
                "error": "connection refused",
            }

        def fake_http(domain, timeout, proxy_url=None):
            seen["http"] = proxy_url
            return None

        with unittest.mock.patch.object(probe, "_dns_lookup", lambda d, p=None: dns):
            with unittest.mock.patch.object(probe, "_tls_check", fake_tls):
                with unittest.mock.patch.object(probe, "_http_check", fake_http):
                    row = probe.probe_domain(
                        "example.test", timeout=1.0, proxy_url="http://127.0.0.1:6152"
                    )

        self.assertEqual(seen["proxy"], "http://127.0.0.1:6152")
        self.assertEqual(row["status"], "tls_fail")


if __name__ == "__main__":
    unittest.main()
