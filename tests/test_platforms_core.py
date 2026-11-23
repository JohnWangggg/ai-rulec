"""The shared platform layer and the modules that must route paths through it."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_rulec import paths, platforms, profile


class StateDirTests(unittest.TestCase):
    def test_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {paths.STATE_ENV: tmp}):
                self.assertEqual(paths.state_dir(), Path(tmp))

    def test_override_expands_user(self) -> None:
        with mock.patch.dict(os.environ, {paths.STATE_ENV: "~/ai-rulec-state-test"}):
            expected = Path(os.path.expanduser("~/ai-rulec-state-test"))
            self.assertEqual(paths.state_dir(), expected)

    def test_default_follows_platform_state_home(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != paths.STATE_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(paths.state_dir(), platforms.state_home() / "ai-rulec")

    @unittest.skipIf(platforms.WINDOWS, "XDG_STATE_HOME is a Unix convention")
    def test_default_honours_xdg_state_home(self) -> None:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {paths.STATE_ENV, "XDG_STATE_HOME"}
        }
        with tempfile.TemporaryDirectory() as tmp:
            env["XDG_STATE_HOME"] = tmp
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(paths.state_dir(), Path(tmp) / "ai-rulec")


class ClientRootsTests(unittest.TestCase):
    def test_default_roots_matches_platform_layer(self) -> None:
        self.assertEqual(profile.DEFAULT_ROOTS, platforms.client_roots())

    def test_roots_are_non_empty(self) -> None:
        roots = platforms.client_roots()
        self.assertTrue(roots)
        for root in roots:
            self.assertIsInstance(root, str)
            self.assertTrue(root.strip())

    def test_roots_expand_under_home(self) -> None:
        home = Path(os.path.expanduser("~")).resolve()
        roots = platforms.client_roots()
        for root in roots:
            expanded = platforms.expand(root).resolve()
            self.assertTrue(
                expanded == home or home in expanded.parents,
                f"{root} expands outside {home}: {expanded}",
            )

    def test_singbox_roots_are_non_empty(self) -> None:
        self.assertTrue(platforms.singbox_config_roots())


class AtomicReplaceTests(unittest.TestCase):
    def test_replaces_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "new.txt"
            dst = root / "old.txt"
            src.write_text("new", encoding="utf-8")
            dst.write_text("old", encoding="utf-8")
            platforms.atomic_replace(src, dst)
            self.assertEqual(dst.read_text(encoding="utf-8"), "new")
            self.assertFalse(src.exists())


class LinkOrCopyTests(unittest.TestCase):
    def test_returns_known_kind_and_copies_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "geo.dat"
            dst = root / "nested" / "alias.dat"
            src.write_bytes(b"payload")
            kind = platforms.link_or_copy(src, dst)
            self.assertIn(kind, {"symlink", "copy"})
            self.assertEqual(dst.read_bytes(), b"payload")


class SurgeProbeTests(unittest.TestCase):
    def test_non_macos_does_not_probe_app_bundle(self) -> None:
        from ai_rulec import surge

        with mock.patch.object(platforms, "MACOS", False):
            candidates = surge.SURGE_CLI_CANDIDATES
            # Absolute candidates are POSIX paths; os.path.sep is "\\" on
            # Windows, so it cannot be used to tell them from bare names.
            app_bundle = [item for item in candidates if "/" in item]
            self.assertTrue(app_bundle, "expected a macOS-only absolute candidate")
            with mock.patch.object(platforms, "which_exe", return_value=None):
                self.assertIsNone(surge.find_surge_cli())


if __name__ == "__main__":
    unittest.main()
