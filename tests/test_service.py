"""Service manager: backend selection, dry runs, and failure reports.

Every test stubs the platform layer and ``service._run`` so no unit file lands
outside the temporary directory and no real service manager is touched.
"""
from __future__ import annotations

import contextlib
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_rulec import platforms
from ai_rulec import service


def completed(argv: list[str], code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, code, out, err)


class PlatformHarness(unittest.TestCase):
    """Point the service module at a scratch home for a chosen OS family."""

    family = "macos"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.logs = self.root / "logs"
        self.home.mkdir()
        self.meta = self.root / "out" / "mihomo-meta.yaml"
        self.meta.parent.mkdir()
        self.meta.write_text("proxies: []\n", encoding="utf-8")
        self.calls: list[list[str]] = []

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        for name, value in (
            ("os_family", lambda: self.family),
            ("home", lambda: self.home),
            ("config_home", lambda: self.config),
            ("log_dir", lambda: self.logs),
            ("python_executable", lambda: "/fake/python3"),
        ):
            stack.enter_context(mock.patch.object(platforms, name, value))
        # The repo's out/ must not leak into these tests; CI has none anyway.
        stack.enter_context(mock.patch.object(service, "_default_meta", lambda: self.meta))
        # Lingering is a host fact (/var/lib/systemd/linger/$USER); CI runners
        # set it, laptops usually do not. Default to "off" so the warning path
        # is deterministic, and let tests flip it with their own patch.
        stack.enter_context(mock.patch.object(service, "_linger_enabled", lambda: False))
        # Anything that shells out goes through _run; record instead of exec.
        stack.enter_context(mock.patch.object(service, "_run", self.fake_run))
        # Belt and braces: the module must never reach subprocess.run directly.
        stack.enter_context(
            mock.patch.object(
                service.subprocess,
                "run",
                side_effect=AssertionError("tests must not spawn processes"),
            )
        )

    def fake_run(self, argv, *, timeout=120.0, cwd=None):
        self.calls.append(list(argv))
        return completed(list(argv))

    # -- assertions ------------------------------------------------------- #

    def assert_nothing_written(self) -> None:
        self.assertEqual(self.calls, [], "no backend command should run")
        written = [
            path
            for path in self.root.rglob("*")
            if path.is_file() and path != self.meta
        ]
        self.assertEqual(written, [], f"unexpected files written: {written}")


class DescribeTests(PlatformHarness):
    def test_macos_backend(self) -> None:
        self.family = "macos"
        report = service.describe()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["backend"], "launchd")
        self.assertEqual(
            report["unit"], str(self.home / "Library" / "LaunchAgents" / "com.ai-rulec.watch.plist")
        )

    def test_linux_backend(self) -> None:
        self.family = "linux"
        report = service.describe()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["backend"], "systemd")
        self.assertEqual(
            report["unit"], str(self.config / "systemd" / "user" / "ai-rulec-watch.service")
        )

    def test_windows_backend(self) -> None:
        self.family = "windows"
        report = service.describe()
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["backend"], "schtasks")
        self.assertEqual(report["unit"], "ai-rulec-watch")

    def test_describe_before_the_first_build(self) -> None:
        """CI has no out/: describe must still explain what would be installed."""
        self.meta.unlink()
        report = service.describe(meta=str(self.meta))
        self.assertTrue(report["ok"], report)
        self.assertFalse(report["meta_exists"])
        self.assertEqual(report["meta"], str(self.meta))
        self.assertEqual(report["warnings"], [])

    def test_install_still_requires_the_meta_file(self) -> None:
        self.meta.unlink()
        report = service.install(meta=str(self.meta), dry_run=True)
        self.assertFalse(report["ok"])
        self.assertIn("meta file not found", report["message"])

    def test_work_dir_prefers_the_checkout(self) -> None:
        (self.root / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        self.assertEqual(service._work_dir(self.meta), self.root)

    def test_work_dir_falls_back_to_the_meta_directory(self) -> None:
        meta = self.root / "state" / "out" / "mihomo-meta.yaml"
        meta.parent.mkdir(parents=True)
        meta.write_text("proxies: []\n", encoding="utf-8")
        self.assertEqual(service._work_dir(meta), meta.parent)

    def test_describe_runs_nothing(self) -> None:
        for family in ("macos", "linux", "windows"):
            self.family = family
            report = service.describe()
            self.assertEqual(report["argv"][1:4], ["-m", "ai_rulec", "watch"])
        self.assert_nothing_written()


class InstallPlanTests(PlatformHarness):
    def test_dry_run_returns_argv_and_writes_nothing(self) -> None:
        for family in ("macos", "linux", "windows"):
            with self.subTest(family=family):
                self.family = family
                self.calls.clear()
                report = service.install(meta=str(self.meta), dry_run=True)
                self.assertTrue(report["ok"], report)
                self.assertTrue(report["dry_run"])
                argv = report["argv"]
                self.assertEqual(
                    argv,
                    [
                        "/fake/python3",
                        "-m",
                        "ai_rulec",
                        "watch",
                        "--latest",
                        "--meta",
                        str(self.meta.resolve()),
                        "--interval",
                        "5",
                        "--health",
                        "--agents-interval",
                        "300",
                        "--json",
                    ],
                )
                self.assertTrue(report["commands"])
                # Only the interpreter probe ran; no unit was written.
                self.assertEqual(self.calls, [["/fake/python3", "-c", "import yaml"]])
                self.assertFalse(
                    (self.home / "Library" / "LaunchAgents" / "com.ai-rulec.watch.plist").exists()
                )
                self.assertFalse((self.config / "systemd").exists())

    def test_missing_meta_fails_with_hint(self) -> None:
        missing = self.root / "nope" / "mihomo-meta.yaml"
        report = service.install(meta=str(missing), dry_run=True)
        self.assertFalse(report["ok"])
        self.assertIn("meta file not found", report["message"])
        self.assertIn("ai-rulec build", report["message"])
        self.assertEqual(self.calls, [])

    def test_missing_pyyaml_fails_with_hint(self) -> None:
        def run(argv, *, timeout=120.0, cwd=None):
            self.calls.append(list(argv))
            return completed(list(argv), 1, err="ModuleNotFoundError: No module named 'yaml'")

        with mock.patch.object(service, "_run", run):
            report = service.install(meta=str(self.meta), dry_run=True)
        self.assertFalse(report["ok"])
        self.assertIn("PyYAML", report["message"])
        self.assertIn("--python", report["message"])

    def test_interval_appears_in_argv(self) -> None:
        report = service.install(
            meta=str(self.meta), interval=2.5, agents_interval=60, dry_run=True
        )
        self.assertIn("2.5", report["argv"])
        self.assertIn("60", report["argv"])

    def test_bad_python_override_fails_with_hint(self) -> None:
        bogus = self.root / "no-such-python"
        report = service.install(meta=str(self.meta), python=str(bogus), dry_run=True)
        self.assertFalse(report["ok"])
        self.assertIn("not found or not executable", report["message"])
        self.assertIn("--python", report["message"])

    def test_python_override_is_used(self) -> None:
        fake = self.root / "venv" / "bin" / "python3"
        fake.parent.mkdir(parents=True)
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        fake.chmod(0o755)
        report = service.install(meta=str(self.meta), python=str(fake), dry_run=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["argv"][0], os.path.abspath(fake))
        self.assertEqual(self.calls[0], [os.path.abspath(fake), "-c", "import yaml"])


class StatusErrorTests(PlatformHarness):
    family = "linux"

    def test_missing_systemctl_is_a_report(self) -> None:
        with mock.patch.object(platforms, "which_exe", lambda name: None):
            report = service.status()
        self.assertFalse(report["ok"])
        self.assertFalse(report["running"])
        self.assertIn("systemctl not found", report["message"])

    def test_backend_command_error_is_a_report(self) -> None:
        def run(argv, *, timeout=120.0, cwd=None):
            raise service.ServiceError(f"command not found: {argv[0]}")

        with mock.patch.object(
            platforms, "which_exe", lambda name: "/usr/bin/" + name
        ), mock.patch.object(service, "_run", run):
            report = service.status()
        self.assertFalse(report["ok"])
        self.assertIn("command not found", report["message"])


class MacInstallTests(PlatformHarness):
    family = "macos"

    def test_plist_contains_watch_arguments(self) -> None:
        report = service.install(meta=str(self.meta))
        self.assertTrue(report["ok"], report)
        plist_path = self.home / "Library" / "LaunchAgents" / "com.ai-rulec.watch.plist"
        self.assertTrue(plist_path.is_file())
        data = plistlib.loads(plist_path.read_bytes())
        self.assertEqual(data["Label"], "com.ai-rulec.watch")
        self.assertEqual(data["ProgramArguments"], report["argv"])
        self.assertEqual(data["WorkingDirectory"], report["working_dir"])
        self.assertEqual(
            data["EnvironmentVariables"]["PATH"],
            "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
        )
        self.assertTrue(data["RunAtLoad"])
        self.assertTrue(data["KeepAlive"])
        self.assertEqual(data["StandardOutPath"], str(self.logs / "ai-rulec-watch.log"))
        self.assertEqual(data["StandardErrorPath"], str(self.logs / "ai-rulec-watch.err.log"))
        commands = [call[:2] for call in self.calls if call[0] == "launchctl"]
        self.assertIn(["launchctl", "bootout"], commands)
        self.assertIn(["launchctl", "bootstrap"], commands)

    def test_bootstrap_failure_is_a_report(self) -> None:
        def run(argv, *, timeout=120.0, cwd=None):
            self.calls.append(list(argv))
            if argv[:2] == ["launchctl", "bootstrap"]:
                return completed(list(argv), 5, err="Bootstrap failed: 5: Input/output error")
            return completed(list(argv))

        with mock.patch.object(service, "_run", run):
            report = service.install(meta=str(self.meta))
        self.assertFalse(report["ok"])
        self.assertIn("launchctl bootstrap failed", report["message"])
        self.assertIn("Input/output error", report["message"])

    def test_uninstall_dry_run_keeps_plist(self) -> None:
        inst = service.install(meta=str(self.meta))
        self.assertTrue(inst["ok"], inst)
        self.calls.clear()
        report = service.uninstall(dry_run=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["commands"], [["launchctl", "bootout", report["commands"][0][2]]])
        self.assertTrue(Path(inst["unit"]).is_file())
        self.assertEqual(self.calls, [])


class LinuxInstallTests(PlatformHarness):
    family = "linux"

    def test_unit_contains_watch_arguments(self) -> None:
        report = service.install(meta=str(self.meta))
        self.assertTrue(report["ok"], report)
        unit = self.config / "systemd" / "user" / "ai-rulec-watch.service"
        text = unit.read_text(encoding="utf-8")
        self.assertIn("-m ai_rulec watch", text)
        self.assertIn("--latest", text)
        # systemd renders each word through _systemd_arg, which escapes
        # backslashes and quotes; assert on the rendered form so the test holds
        # on Windows hosts too (where the scratch path is full of backslashes).
        self.assertIn(service._systemd_arg(str(self.meta.resolve())), text)
        self.assertIn("--health", text)
        self.assertIn("--agents-interval 300", text)
        self.assertIn("--json", text)
        self.assertIn("Restart=always", text)
        self.assertIn("RestartSec=10", text)
        self.assertIn(f"WorkingDirectory={service._systemd_arg(report['working_dir'])}", text)
        self.assertIn("WantedBy=default.target", text)
        systemctl = [call for call in self.calls if call[0] == "systemctl"]
        self.assertIn(["systemctl", "--user", "daemon-reload"], systemctl)
        self.assertIn(
            ["systemctl", "--user", "enable", "--now", "ai-rulec-watch.service"], systemctl
        )

    def test_linger_warning_when_disabled(self) -> None:
        report = service.install(meta=str(self.meta), dry_run=True)
        self.assertTrue(any("enable-linger" in w for w in report["warnings"]), report)

    def test_space_and_cjk_path_is_quoted(self) -> None:
        spaced = self.root / "带 空格" / "mihomo-meta.yaml"
        spaced.parent.mkdir()
        spaced.write_text("proxies: []\n", encoding="utf-8")
        report = service.install(meta=str(spaced))
        self.assertTrue(report["ok"], report)
        text = (self.config / "systemd" / "user" / "ai-rulec-watch.service").read_text("utf-8")
        self.assertIn(service._systemd_arg(str(spaced.resolve())), text)
        self.assertIn('"', service._systemd_arg(str(spaced.resolve())), "path needs quoting")

    def test_enable_failure_is_a_report(self) -> None:
        def run(argv, *, timeout=120.0, cwd=None):
            self.calls.append(list(argv))
            if argv[-1] == "ai-rulec-watch.service" and "enable" in argv:
                return completed(list(argv), 1, err="Failed to enable unit: Interactive authentication required")
            return completed(list(argv))

        with mock.patch.object(service, "_run", run):
            report = service.install(meta=str(self.meta))
        self.assertFalse(report["ok"])
        self.assertIn("systemctl", report["message"])

    def test_uninstall_removes_unit(self) -> None:
        self.assertTrue(service.install(meta=str(self.meta))["ok"])
        report = service.uninstall()
        self.assertTrue(report["ok"], report)
        self.assertFalse((self.config / "systemd" / "user" / "ai-rulec-watch.service").exists())


class WindowsInstallTests(PlatformHarness):
    family = "windows"

    def test_create_command_shape(self) -> None:
        report = service.install(meta=str(self.meta))
        self.assertTrue(report["ok"], report)
        create = [call for call in self.calls if call[0] == "schtasks"]
        self.assertEqual(len(create), 1)
        argv = create[0]
        self.assertEqual(argv[:2], ["schtasks", "/Create"])
        self.assertIn("/F", argv)
        self.assertEqual(argv[argv.index("/SC") + 1], "ONLOGON")
        self.assertEqual(argv[argv.index("/TN") + 1], "ai-rulec-watch")
        task_run = argv[argv.index("/TR") + 1]
        self.assertIn("--agents-interval", task_run)
        self.assertIn("-m ai_rulec watch", task_run)
        # ONSTART is never attempted without admin rights.
        self.assertNotIn("ONSTART", argv)
        self.assertTrue(any("ONSTART" in note for note in report["notes"]), report)

    def test_uninstall_command_shape(self) -> None:
        report = service.uninstall(dry_run=True)
        self.assertTrue(report["ok"], report)
        self.assertEqual(
            report["commands"], [["schtasks", "/Delete", "/TN", "ai-rulec-watch", "/F"]]
        )
        self.assertEqual(self.calls, [])

    def test_status_uses_query_list(self) -> None:
        def run(argv, *, timeout=120.0, cwd=None):
            self.calls.append(list(argv))
            if argv[:2] == ["schtasks", "/Query"]:
                return completed(list(argv), 0, out="TaskName:      \\ai-rulec-watch\nStatus:        Running\n")
            return completed(list(argv))

        with mock.patch.object(platforms, "which_exe", lambda name: "C:/Windows/System32/" + name + ".exe"):
            with mock.patch.object(service, "_run", run):
                report = service.status()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["installed"])
        self.assertTrue(report["running"])
        self.assertEqual(self.calls[0][:2], ["schtasks", "/Query"])
        self.assertIn("/FO", self.calls[0])

    def test_status_survives_a_localised_schtasks(self) -> None:
        """A Chinese Windows prints 状态 instead of Status; the state enum does not."""

        def run(argv, *, timeout=120.0, cwd=None):
            self.calls.append(list(argv))
            if argv[:2] == ["schtasks", "/Query"]:
                return completed(
                    list(argv),
                    0,
                    out="\r\nTaskName:      \\ai-rulec-watch\r\nStatus:        Running\r\n",
                )
            if argv[0].endswith("powershell.exe"):
                return completed(list(argv), 0, out="4\r\n")
            return completed(list(argv))

        with mock.patch.object(
            platforms, "which_exe", lambda name: "C:/Windows/System32/" + name + ".exe"
        ):
            with mock.patch.object(service, "_run", run):
                report = service.status()
        self.assertTrue(report["running"], report)
        self.assertEqual(report["state"], 4)

    def test_status_falls_back_to_localised_labels(self) -> None:
        def run(argv, *, timeout=120.0, cwd=None):
            self.calls.append(list(argv))
            if argv[:2] == ["schtasks", "/Query"]:
                return completed(
                    list(argv), 0, out="任务名:        \\ai-rulec-watch\n状态:          正在运行\n"
                )
            # No PowerShell: the text is all we have.
            return completed(list(argv), 1, out="")

        def which(name):
            if name in ("powershell", "pwsh"):
                return None
            return "C:/Windows/System32/" + name + ".exe"

        with mock.patch.object(platforms, "which_exe", which):
            with mock.patch.object(service, "_run", run):
                report = service.status()
        self.assertTrue(report["running"], report)
        self.assertNotIn("state", report)


class UnsupportedTests(PlatformHarness):
    family = "freebsd14"

    def test_unsupported_platform_reports(self) -> None:
        report = service.install(meta=str(self.meta), dry_run=True)
        self.assertFalse(report["ok"])
        self.assertIn("no supported service backend", report["message"])
        self.assertFalse(service.describe()["ok"])


class SafeExistsTests(unittest.TestCase):
    def test_a_denied_directory_read_is_treated_as_absent(self) -> None:
        class Denied:
            def is_file(self) -> bool:
                raise PermissionError(13, "Operation not permitted")

        self.assertFalse(service._exists(Denied()))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
