import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_rulec import paths, ui

ROOT = Path(__file__).resolve().parent.parent


class PathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.env = mock.patch.dict(os.environ, {paths.STATE_ENV: str(self.state)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_record_and_resolve_out(self):
        out = Path(self.tmp.name) / "build"
        out.mkdir()
        paths.record_build(out)
        self.assertEqual(paths.last_build(), out.resolve())
        with mock.patch("pathlib.Path.cwd", return_value=Path(self.tmp.name)):
            self.assertEqual(paths.resolve_out().resolve(), out.resolve())
            self.assertEqual(
                paths.resolve_meta().resolve(), (out / "mihomo-meta.yaml").resolve()
            )

    def test_explicit_wins(self):
        explicit = Path(self.tmp.name) / "elsewhere"
        self.assertEqual(paths.resolve_out(explicit), explicit)
        self.assertEqual(paths.resolve_meta(explicit), explicit)

    def test_last_build_ignores_removed_dir(self):
        out = Path(self.tmp.name) / "gone"
        out.mkdir()
        paths.record_build(out)
        out.rmdir()
        self.assertIsNone(paths.last_build())

    def test_missing_mihomo_has_actionable_hint(self):
        binary, hint = paths.mihomo_binary("/definitely/not/here")
        self.assertIsNone(binary)
        self.assertIn("brew install mihomo", hint or "")


class UiTests(unittest.TestCase):
    def test_group_egress_reads_ai_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "p.yaml"
            profile.write_text(
                "proxy-groups:\n"
                "  - name: AI-openai\n"
                "    type: select\n"
                "    proxies: [自动选择, DIRECT]\n"
                "  - name: Other\n"
                "    type: select\n"
                "    proxies: [DIRECT]\n",
                encoding="utf-8",
            )
            self.assertEqual(
                ui._group_egress(str(profile)),
                {"AI-openai": ["自动选择", "DIRECT"]},
            )

    def test_unknown_action_is_rejected(self):
        result = ui._run_action("definitely-not-an-action")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])

    def test_health_payload_marks_a_closed_route_as_skipped(self):
        with mock.patch.object(
            ui, "_health", return_value={"ok": None, "skipped": "x is not listening"}
        ):
            payload = ui._health_payload()
        self.assertIsNone(payload["ok"])
        self.assertIn("is not listening", payload["skipped"])

    def test_state_falls_back_when_no_profile(self):
        with mock.patch.object(ui, "_latest_profile", return_value=None):
            self.assertEqual(ui._state()["ok"], False)
            self.assertIn("no profile", ui._state()["error"])


@unittest.skipUnless(
    shutil.which("bash"), "install.command is a bash script"
)
class InstallerTests(unittest.TestCase):
    def test_install_script_parses_in_bash(self):
        proc = subprocess.run(
            ["bash", "-n", str(ROOT / "install.command")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_install_script_has_no_empty_array_expansion(self):
        text = (ROOT / "install.command").read_text(encoding="utf-8")
        self.assertNotIn("PROBE=(", text)
        self.assertNotIn('"${PROBE[@]}"', text)


if __name__ == "__main__":
    unittest.main()
