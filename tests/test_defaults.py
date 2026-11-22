from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_rulec import defaults

ROOT = Path(__file__).resolve().parent.parent


class BundledDefaultsTest(unittest.TestCase):
    def test_bundled_files_exist_and_parse(self) -> None:
        import yaml

        for name in defaults.DEFAULTS:
            path = defaults.bundled(name)
            self.assertTrue(path.is_file(), path)
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict, path)

    def test_bundled_matches_repo_copy(self) -> None:
        """Repo copies and shipped defaults must not drift apart."""
        for name, (filename, local) in defaults.DEFAULTS.items():
            repo_copy = ROOT / local
            if not repo_copy.is_file():
                continue
            self.assertEqual(
                repo_copy.read_text(encoding="utf-8"),
                defaults.bundled(name).read_text(encoding="utf-8"),
                f"{local} and ai_rulec/data/{filename} differ; copy one over the other",
            )

    def test_unknown_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            defaults.bundled("nope")

    def test_explicit_path_wins(self) -> None:
        self.assertEqual(defaults.resolve("agents", "/tmp/x.yaml"), "/tmp/x.yaml")

    def test_local_override_beats_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp)
            (harness / "config").mkdir()
            override = harness / "config" / "agents.yaml"
            override.write_text("agents: {}\n", encoding="utf-8")
            previous = Path.cwd()
            try:
                import os

                os.chdir(harness)
                self.assertEqual(defaults.resolve("agents"), "config/agents.yaml")
                self.assertEqual(
                    defaults.resolve("services"), str(defaults.bundled("services"))
                )
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
