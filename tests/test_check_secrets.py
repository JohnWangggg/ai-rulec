"""The pre-push scanner must not cry wolf on GitHub noreply identities."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_secrets", ROOT / "scripts" / "check_secrets.py"
)
scanner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(scanner)


class ScanTextTests(unittest.TestCase):
    def test_a_real_address_is_flagged(self) -> None:
        # Split so this fixture is not itself a scan hit in the repo/history.
        address = "someone" + "@" + "example.com"
        hits = scanner.scan_text(f"mail: {address}", "x")
        self.assertTrue(any("email:" in hit for hit in hits))

    def test_a_github_noreply_address_is_not_flagged(self) -> None:
        hits = scanner.scan_text(
            "51693560+JohnWangggg@users.noreply.github.com", "x"
        )
        self.assertEqual(hits, [])

    def test_tokens_are_still_flagged(self) -> None:
        hits = scanner.scan_text("sk-proj-" + "a" * 24, "x")
        self.assertTrue(any("openai-key" in hit for hit in hits))


if __name__ == "__main__":
    unittest.main()
