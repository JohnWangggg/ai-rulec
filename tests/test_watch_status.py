from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import yaml

from ai_rulec.status import profile_status
from ai_rulec import watch as watch_module
from ai_rulec.watch import reconcile_once, watch

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


class HealthSweepTests(unittest.TestCase):
    def test_slow_sweep_does_not_block_the_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, profile = make_home(Path(tmp))
            agents = root / "agents.yaml"
            agents.write_text(
                yaml.safe_dump(
                    {
                        "agents": [
                            {
                                "id": "demo",
                                "checks": [
                                    {"type": "http", "url": "https://example.com"}
                                ],
                            }
                        ]
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            state_file = root / "watch.json"
            original = (
                watch_module._check_agents,
                watch_module._load_agents,
            )
            watch_module._load_agents = lambda path: {"demo": {"id": "demo"}}

            def slow(agents, names, *, proxy_url=None, timeout=6.0):
                time.sleep(1.0)
                return {
                    "agents": [
                        {
                            "name": "demo",
                            "status": "ok",
                            "reason": None,
                            "summary": {"ok": 1, "degraded": 0, "down": 0},
                        }
                    ],
                    "summary": {"total": 1, "ok": 1, "degraded": 0, "down": 0},
                }

            watch_module._check_agents = slow
            kwargs = {
                "reapply": False,
                "settle": 0,
                "state_path": str(state_file),
                "agents_path": str(agents),
                "check_agent_health": True,
                "agents_interval": 0.0,
            }
            try:
                started = time.monotonic()
                first = reconcile_once(str(profile), **kwargs)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.9, "health sweep ran inline")
                self.assertTrue(first["health"]["pending"], first["health"])
                self.assertNotIn(
                    "last_health_at",
                    json.loads(state_file.read_text(encoding="utf-8")),
                )

                deadline = time.monotonic() + 5.0
                second = None
                while time.monotonic() < deadline:
                    time.sleep(0.1)
                    second = reconcile_once(str(profile), **kwargs)
                    if not second["health"].get("pending"):
                        break
                assert second is not None
                self.assertFalse(second["health"].get("pending"), second["health"])
                self.assertTrue(second["health"]["ok"], second["health"])
                self.assertEqual(second["health"]["summary"]["ok"], 1)
                state = json.loads(state_file.read_text(encoding="utf-8"))
                self.assertTrue(state["last_health_at"])
                self.assertEqual(state["last_health"]["summary"]["ok"], 1)
                self.assertNotIn("checked_at", state["last_health"])
            finally:
                watch_module._check_agents, watch_module._load_agents = original
                watch_module._HEALTH_JOBS.pop(str(state_file), None)


class FollowActiveTest(unittest.TestCase):
    def test_watch_requires_profile_without_follow_active(self) -> None:
        with self.assertRaises(ValueError):
            watch(None, follow_active=False, once=True)

    def test_watch_resolves_active_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _root, profile = make_home(Path(tmp))
            original = watch_module._active_profile
            watch_module._active_profile = lambda: profile
            try:
                result = watch(
                    None,
                    follow_active=True,
                    once=True,
                    dry_run=True,
                    reapply=False,
                    fix_cache=False,
                    json_output=False,
                )
            finally:
                watch_module._active_profile = original
        self.assertIsNotNone(result)
        self.assertEqual(result["profile"], str(profile.resolve()))


if __name__ == "__main__":
    unittest.main()
