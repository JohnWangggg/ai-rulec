from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "install_singbox_agent.sh")
LABEL = "com.ai-rulec.singbox-watch"
DEFAULT_CONFIG = (
    "Library/Application Support/app.hiddify.com/data/current-config.json"
)


def run_script(*args: str, home: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = home
    env.pop("PYTHON", None)
    return subprocess.run(
        ["bash", SCRIPT, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


@unittest.skipUnless(
    shutil.which("bash"), "install_singbox_agent.sh is a bash script"
)
class SingboxAgentScriptTests(unittest.TestCase):
    def test_syntax_is_valid(self) -> None:
        proc = subprocess.run(
            ["bash", "-n", SCRIPT], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_print_emits_valid_plist_without_touching_home(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            proc = run_script("--print", home=home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(LABEL, proc.stdout)
            self.assertIn("singbox-watch", proc.stdout)

            plist = plistlib.loads(proc.stdout.encode("utf-8"))
            self.assertEqual(plist["Label"], LABEL)
            args = plist["ProgramArguments"]
            self.assertEqual(args[1:4], ["-m", "ai_rulec", "singbox-watch"])
            self.assertEqual(args[4], os.path.join(home, DEFAULT_CONFIG))
            self.assertEqual(args[5:7], ["--interval", "5"])
            self.assertEqual(args[-1], "--json")
            self.assertTrue(plist["RunAtLoad"])
            self.assertTrue(plist["KeepAlive"])
            self.assertEqual(plist["ThrottleInterval"], 10)
            self.assertEqual(plist["WorkingDirectory"], REPO_ROOT)
            self.assertEqual(
                plist["StandardOutPath"],
                os.path.join(home, "Library/Logs/ai-rulec-singbox-watch.log"),
            )
            self.assertEqual(
                plist["StandardErrorPath"],
                os.path.join(home, "Library/Logs/ai-rulec-singbox-watch.err.log"),
            )

            # --print must not create anything under HOME.
            self.assertFalse(os.path.exists(os.path.join(home, "Library")))

    def test_print_is_the_default_action(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            proc = run_script(home=home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn(LABEL, proc.stdout)
            self.assertFalse(os.path.exists(os.path.join(home, "Library")))

    def test_config_and_interval_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            config = os.path.join(home, "custom", "config.json")
            proc = run_script(
                "--config", config, "--interval", "30", home=home
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            plist = plistlib.loads(proc.stdout.encode("utf-8"))
            args = plist["ProgramArguments"]
            self.assertIn(config, args)
            self.assertEqual(args[args.index("--interval") + 1], "30")
            self.assertFalse(os.path.exists(os.path.join(home, "Library")))

    def test_tilde_config_is_expanded(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            proc = run_script("--config", "~/hiddify.json", home=home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            plist = plistlib.loads(proc.stdout.encode("utf-8"))
            self.assertIn(
                os.path.join(home, "hiddify.json"), plist["ProgramArguments"]
            )
            self.assertNotIn("~/hiddify.json", proc.stdout)

    def test_xml_special_characters_are_escaped(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            config = os.path.join(home, "a&b<c>.json")
            proc = run_script("--config", config, home=home)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("a&amp;b&lt;c&gt;.json", proc.stdout)
            plist = plistlib.loads(proc.stdout.encode("utf-8"))
            self.assertIn(config, plist["ProgramArguments"])

    def test_bad_argument_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            proc = run_script("--nope", home=home)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("unknown argument", proc.stderr)
            self.assertFalse(os.path.exists(os.path.join(home, "Library")))

    def test_bad_interval_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            proc = run_script("--interval", "soon", home=home)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
