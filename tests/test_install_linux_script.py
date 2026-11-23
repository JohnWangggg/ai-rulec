from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "install.sh")


def run_script(*args: str, cwd: str = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sh", SCRIPT, *args], capture_output=True, text=True, cwd=cwd
    )


class InstallLinuxScriptTests(unittest.TestCase):
    def test_syntax_is_valid_for_bash_and_sh(self) -> None:
        for shell in ("bash", "sh"):
            proc = subprocess.run(
                [shell, "-n", SCRIPT], capture_output=True, text=True
            )
            self.assertEqual(proc.returncode, 0, f"{shell}: {proc.stderr}")

    def test_help_exits_zero_without_touching_anything(self) -> None:
        proc = run_script("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in ("--no-agent", "--no-ui", "--skip-probe", "--no-mihomo", "--python", "--project"):
            self.assertIn(flag, proc.stdout)

    def test_unknown_flag_is_a_usage_error(self) -> None:
        proc = run_script("--bogus")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--bogus", proc.stderr)

    @unittest.skipIf(sys.platform == "linux", "linux is the supported platform")
    def test_aborts_on_non_linux(self) -> None:
        proc = run_script("--dry-run")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("this script is for Linux", proc.stderr)

    @unittest.skipUnless(sys.platform == "linux", "full path needs a linux host")
    def test_dry_run_plans_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            os.makedirs(os.path.join(project, "scripts"))
            open(os.path.join(project, "pyproject.toml"), "w").close()
            proc = subprocess.run(
                ["sh", SCRIPT, "--dry-run", "--no-ui"],
                capture_output=True,
                text=True,
                cwd=project,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("[dry-run]", proc.stdout)
            self.assertIn("service install", proc.stdout)
            self.assertFalse(os.path.exists(os.path.join(project, ".venv")))
            self.assertFalse(os.path.exists(os.path.join(project, "out")))


if __name__ == "__main__":
    unittest.main()
