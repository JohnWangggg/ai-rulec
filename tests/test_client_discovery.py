from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from ai_rulec.profile import detect_client, discover_profiles, inspect_profile


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def minimal_config() -> dict:
    return {
        "mixed-port": 7890,
        "proxies": [],
        "proxy-groups": [],
        "rules": ["MATCH,DIRECT"],
    }


class ClientDiscoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make(self, relative: str) -> Path:
        path = self.root / relative
        write_yaml(path, minimal_config())
        return path

    def by_path(self, result: dict) -> dict[str, dict]:
        return {
            str(Path(item["path"]).resolve()): item for item in result["profiles"]
        }

    def test_client_labels_kinds_and_layouts(self) -> None:
        expected = {
            "Clash Verge Rev/profiles/verge-profile.yaml": (
                "clash-verge",
                "profile",
            ),
            "Clash Verge Rev/config.yaml": ("clash-verge", "config"),
            "io.github.clash-verge-rev.clash-verge-rev/profiles/abc123.yaml": (
                "clash-verge",
                "profile",
            ),
            "com.follow.clash/profiles/123456789012345678.yaml": (
                "flclash",
                "profile",
            ),
            "Stash/profiles/stash-node.yaml": ("stash", "profile"),
            "ClashX/config.yaml": ("clashx", "config"),
            ".config/mihomo/config.yaml": ("mihomo", "config"),
            ".config/clash/config.yaml": ("mihomo", "config"),
            "plain-configs/config.yaml": ("unknown", "config"),
        }
        for relative in expected:
            self.make(relative)

        extra = [
            "Clash Verge Rev",
            "io.github.clash-verge-rev.clash-verge-rev",
            "com.follow.clash",
            "Stash",
            "ClashX",
            ".config/mihomo",
            ".config/clash",
            "plain-configs",
        ]
        result = discover_profiles(
            roots=[str(self.root)],
            extra=[str(self.root / entry) for entry in extra],
        )

        self.assertEqual(result["errors"], [])
        found = self.by_path(result)
        self.assertEqual(len(found), len(expected))
        for relative, (client, kind) in expected.items():
            resolved = str((self.root / relative).resolve())
            self.assertIn(resolved, found)
            self.assertEqual(found[resolved]["client"], client, relative)
            self.assertEqual(found[resolved]["kind"], kind, relative)

    def test_duplicate_roots_and_files_are_deduplicated(self) -> None:
        profile = self.make("Stash/profiles/stash-node.yaml")
        result = discover_profiles(
            roots=[str(profile.parent.parent)],
            extra=[str(profile.parent), str(profile), str(profile)],
        )

        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["profiles"]), 1)
        self.assertEqual(result["profiles"][0]["client"], "stash")
        self.assertEqual(result["profiles"][0]["kind"], "profile")

    def test_inspect_profile_and_detect_client(self) -> None:
        profile = self.make("ClashX/config.yaml")
        info = inspect_profile(profile)
        self.assertEqual(info["client"], "clashx")
        self.assertEqual(info["kind"], "config")
        self.assertEqual(detect_client(profile), "clashx")
        self.assertEqual(detect_client(self.root / "misc" / "config.yaml"), "unknown")


if __name__ == "__main__":
    unittest.main()


class PickProfileTest(unittest.TestCase):
    def item(self, name: str, mtime: float, managed: bool = False) -> dict:
        return {"name": name, "path": f"/tmp/profiles/{name}.yaml",
                "mtime_ts": mtime, "managed": managed}

    def test_active_profile_wins_over_newer_download(self) -> None:
        from ai_rulec.profile import pick_profile

        active = self.item("382", mtime=100.0, managed=True)
        newer = self.item("383", mtime=200.0)
        picked = pick_profile([newer, active], active_hints=["382"])
        self.assertIs(picked, active)

    def test_managed_profile_beats_newest_unmanaged(self) -> None:
        from ai_rulec.profile import pick_profile

        managed = self.item("382", mtime=100.0, managed=True)
        newer = self.item("383", mtime=200.0)
        self.assertIs(pick_profile([newer, managed], active_hints=[]), managed)

    def test_falls_back_to_newest_without_hints_or_managed(self) -> None:
        from ai_rulec.profile import pick_profile

        older = self.item("a", mtime=100.0)
        newer = self.item("b", mtime=200.0)
        self.assertIs(pick_profile([newer, older], active_hints=[]), newer)

    def test_empty_profiles(self) -> None:
        from ai_rulec.profile import pick_profile

        self.assertIsNone(pick_profile([], active_hints=["382"]))
