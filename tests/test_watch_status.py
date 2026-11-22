from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import yaml

from ai_rulec.status import profile_status
from ai_rulec.watch import reconcile_once

NOW_FMT = "%Y-%m-%dT%H:%M:%S%z"


def make_home(root: Path) -> tuple[Path, Path]:
    profile = root / "profiles" / "demo.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        yaml.safe_dump(
            {"proxies": [], "proxy-groups": [], "rules": []},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "config.yaml").write_text(
        yaml.safe_dump({"mixed-port": 65001}, sort_keys=False),
        encoding="utf-8",
    )
    return root, profile


def stamp(offset: float) -> str:
    return time.strftime(NOW_FMT, time.localtime(time.time() + offset))


class WatchLivenessTests(unittest.TestCase):
    def test_skipped_cycle_stamps_last_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, profile = make_home(Path(tmp))
            state_file = root / "watch.json"
            result = reconcile_once(
                str(profile),
                reapply=False,
                settle=0,
                state_path=str(state_file),
            )
            self.assertEqual(result["action"], "skipped", result)
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertTrue(state["last_seen"])
            self.assertEqual(state["interval"], 2.0)
            # A skip is not an action; last_run must not pretend otherwise.
            self.assertNotIn("last_run", state)

    def test_status_fresh_state_is_not_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, profile = make_home(Path(tmp))
            state_file = root / "watch.json"
            reconcile_once(
                str(profile),
                reapply=False,
                settle=0,
                state_path=str(state_file),
            )
            state = json.loads(state_file.read_text(encoding="utf-8"))
            report = profile_status(str(profile), state_path=str(state_file))
            watch = report["watch"]
            self.assertEqual(watch["last_seen"], state["last_seen"])
            self.assertEqual(watch["interval"], 2.0)
            self.assertGreaterEqual(watch["age_seconds"], 0)
            self.assertFalse(watch["stale"])
            self.assertFalse(
                [w for w in report["warnings"] if "stale" in w], report["warnings"]
            )

    def test_status_old_state_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, profile = make_home(Path(tmp))
            state_file = root / "watch.json"
            state_file.write_text(
                json.dumps(
                    {
                        "last_seen": stamp(-600),
                        "interval": 2.0,
                        "last_action": "skipped",
                        "last_ok": True,
                    }
                ),
                encoding="utf-8",
            )
            report = profile_status(str(profile), state_path=str(state_file))
            watch = report["watch"]
            self.assertTrue(watch["stale"])
            self.assertGreaterEqual(watch["age_seconds"], 590)
            self.assertTrue(
                [w for w in report["warnings"] if "stale" in w], report["warnings"]
            )

    def test_stale_honours_three_times_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, profile = make_home(Path(tmp))
            state_file = root / "watch.json"
            state_file.write_text(
                json.dumps(
                    {
                        "last_seen": stamp(-600),
                        "interval": 600.0,
                        "last_action": "skipped",
                        "last_ok": True,
                    }
                ),
                encoding="utf-8",
            )
            report = profile_status(str(profile), state_path=str(state_file))
            self.assertFalse(report["watch"]["stale"])
            self.assertEqual(report["watch"]["interval"], 600.0)


if __name__ == "__main__":
    unittest.main()
