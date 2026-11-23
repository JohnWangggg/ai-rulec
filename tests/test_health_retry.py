from __future__ import annotations

import io
import unittest
import urllib.error
from unittest import mock

from ai_rulec import health


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"ok") -> None:
        self.status = status
        self._body = body

    def geturl(self) -> str:
        return "http://example.test/"

    def read(self, size: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class FakeOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def open(self, request: object, timeout: float | None = None) -> object:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RetryTests(unittest.TestCase):
    def run_check(
        self, outcomes: list[object], check: dict | None = None
    ) -> tuple[dict, FakeOpener]:
        opener = FakeOpener(outcomes)
        payload = {"url": "http://example.test/", "expect": [200]}
        payload.update(check or {})
        with mock.patch.object(health, "_opener", lambda proxy_url: opener):
            with mock.patch.object(health.time, "sleep"):
                result = health.check_http(payload)
        return result, opener

    def test_transient_failure_retries_then_succeeds(self) -> None:
        result, opener = self.run_check(
            [urllib.error.URLError("timed out"), FakeResponse(200)]
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], 200)
        self.assertEqual(opener.calls, 2)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["attempts_limit"], 3)

    def test_persistent_transport_failure_uses_all_attempts(self) -> None:
        result, opener = self.run_check(
            [
                urllib.error.URLError("timed out"),
                urllib.error.URLError("timed out"),
                urllib.error.URLError("timed out"),
            ]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_type"], "timeout")
        self.assertEqual(opener.calls, 3)

    def test_4xx_is_not_retried(self) -> None:
        error = urllib.error.HTTPError(
            "http://example.test/missing",
            404,
            "Not Found",
            {},
            io.BytesIO(b"not found"),
        )
        result, opener = self.run_check([error])
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 404)
        self.assertEqual(result["error_type"], "http")
        self.assertEqual(opener.calls, 1)

    def test_explicit_zero_retries_disables_retry(self) -> None:
        result, opener = self.run_check(
            [urllib.error.URLError("timed out")], {"retries": 0}
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(opener.calls, 1)


if __name__ == "__main__":
    unittest.main()
