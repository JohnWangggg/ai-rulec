from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import yaml

from ai_rulec.merge import infer_target, merge_profile, parse_target_map
from ai_rulec.normalize import (
    classify_role,
    is_shared_apex,
    is_shared_domain,
    normalize_domain,
)
from ai_rulec.profile import (
    apply_profile,
    doctor_profile,
    inspect_provider_cache,
    repair_provider_cache,
)
from ai_rulec.status import profile_status
from ai_rulec import watch as watch_module
from ai_rulec.watch import reconcile_once


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def base_config() -> dict:
    return {
        "mixed-port": 7890,
        "proxies": [],
        "proxy-groups": [
            {"name": "PROXY", "type": "select", "proxies": ["DIRECT"]}
        ],
        "rules": ["DOMAIN-SUFFIX,example.com,PROXY", "MATCH,DIRECT"],
    }


def meta_config(services: tuple[str, ...] = ("openai",)) -> dict:
    providers = {
        service: {
            "type": "file",
            "behavior": "classical",
            "path": f"./AI-Rules/{service}.yaml",
        }
        for service in services
    }
    groups = [
        {"name": f"AI-{service}", "type": "select", "proxies": ["DIRECT"]}
        for service in services
    ]
    rules = [f"RULE-SET,{service},AI-{service}" for service in services]
    return {"rule-providers": providers, "proxy-groups": groups, "rules": rules}


def make_meta_dir(root: Path, services: tuple[str, ...] = ("openai",)) -> Path:
    meta_dir = root / "meta"
    rules_dir = meta_dir / "AI-Rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for service in services:
        (rules_dir / f"{service}.yaml").write_text(
            "payload:\n"
            f"  - DOMAIN-SUFFIX,{service}.example\n",
            encoding="utf-8",
        )
    write_yaml(meta_dir / "meta.yaml", meta_config(services))
    return meta_dir


def make_home(root: Path) -> tuple[Path, Path, Path]:
    home = root / "home"
    profile = home / "profiles" / "382.yaml"
    rules = home / "AI-Rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "openai.yaml").write_text(
        "payload:\n  - DOMAIN-SUFFIX,openai.example\n",
        encoding="utf-8",
    )
    write_yaml(
        profile,
        {
            **base_config(),
            "rule-providers": {
                "openai": {
                    "type": "file",
                    "behavior": "classical",
                    "path": "./AI-Rules/openai.yaml",
                }
            },
            "proxy-groups": [
                {"name": "PROXY", "type": "select", "proxies": ["DIRECT"]},
                {"name": "AI-openai", "type": "select", "proxies": ["DIRECT"]},
                {"name": "AI", "type": "select", "proxies": ["AI-openai"]},
            ],
            "rules": ["RULE-SET,openai,AI-openai", "MATCH,DIRECT"],
        },
    )
    return home, profile, rules


class NormalizeTests(unittest.TestCase):
    def test_domain_normalization(self) -> None:
        self.assertEqual(normalize_domain(" .OpenAI.COM. "), "openai.com")
        self.assertEqual(normalize_domain("*.example.com"), "example.com")
        self.assertIsNone(normalize_domain("localhost"))
        self.assertIsNone(normalize_domain("https://openai.com/path"))

    def test_shared_and_role_classification(self) -> None:
        self.assertTrue(is_shared_domain("api.auth0.com"))
        self.assertTrue(is_shared_apex("auth0.com"))
        self.assertFalse(is_shared_apex("api.auth0.com"))
        self.assertEqual(classify_role("cdn.openai.com"), "cdn")
        self.assertEqual(classify_role("api.openai.com"), "api")
        self.assertEqual(classify_role("auth.openai.com"), "auth")


class MergeTests(unittest.TestCase):
    def test_merge_inserts_rules_before_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            meta = meta_config()
            merged, report = merge_profile(
                base_config(),
                meta,
                target="PROXY",
                providers_dir=str(meta_dir / "AI-Rules"),
                output_dir=str(meta_dir),
            )
            self.assertEqual(
                merged["rule-providers"]["openai"]["path"],
                "./AI-Rules/openai.yaml",
            )
            self.assertEqual(merged["rules"][0], "DOMAIN-SUFFIX,example.com,PROXY")
            self.assertEqual(merged["rules"][1], "RULE-SET,openai,AI-openai")
            self.assertEqual(merged["rules"][-1], "MATCH,DIRECT")
            self.assertEqual(report["groups_added"], 2)
            groups = {group["name"]: group for group in merged["proxy-groups"]}
            self.assertEqual(groups["AI-openai"]["proxies"], ["PROXY", "DIRECT"])
            self.assertEqual(
                groups["AI"]["proxies"], ["AI-openai", "DIRECT"]
            )

    def test_merge_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            meta = meta_config()
            kwargs = {
                "target": "PROXY",
                "providers_dir": str(meta_dir / "AI-Rules"),
                "output_dir": str(meta_dir),
            }
            first, _ = merge_profile(base_config(), meta, **kwargs)
            second, report = merge_profile(
                first, meta, replace_existing=True, **kwargs
            )
            self.assertEqual(report["groups_replaced"], 2)
            self.assertEqual(report["rules_removed"], 1)
            self.assertEqual(
                len(
                    [
                        group
                        for group in second["proxy-groups"]
                        if group["name"] == "AI-openai"
                    ]
                ),
                1,
            )
            self.assertEqual(
                second["rules"].count("RULE-SET,openai,AI-openai"), 1
            )

    def test_reserved_target_and_provider_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            meta = meta_config()
            reserved_base = base_config()
            reserved_base["proxy-groups"].append(
                {"name": "AI-openai", "type": "select", "proxies": ["DIRECT"]}
            )
            with self.assertRaisesRegex(ValueError, "reserved"):
                merge_profile(reserved_base, meta, target="AI-openai")
            base = base_config()
            base["rule-providers"] = {"openai": {"path": "./user.yaml"}}
            with self.assertRaisesRegex(ValueError, "not ai-rulec-generated"):
                merge_profile(
                    base,
                    meta,
                    target="PROXY",
                    providers_dir=str(meta_dir / "AI-Rules"),
                    output_dir=str(root),
                    replace_existing=True,
                )

    def test_target_map_parsing(self) -> None:
        self.assertEqual(
            parse_target_map(["openai=PROXY,gemini=DIRECT"]),
            {"openai": "PROXY", "gemini": "DIRECT"},
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            parse_target_map(["openai=PROXY", "OpenAI=DIRECT"])
        with self.assertRaisesRegex(ValueError, "service=target"):
            parse_target_map(["openai"])


class EgressTests(unittest.TestCase):
    def test_infer_target_prefers_auto_then_selector(self) -> None:
        data = {
            "proxy-groups": [
                {"name": "手动选择", "type": "select", "proxies": ["DIRECT"]},
                {"name": "自动选择", "type": "url-test", "proxies": ["DIRECT"]},
            ]
        }
        self.assertEqual(infer_target(data), "自动选择")
        only_selector = {
            "proxy-groups": [
                {"name": "漏网之鱼", "type": "select", "proxies": ["DIRECT"]},
                {"name": "手动选择", "type": "select", "proxies": ["DIRECT"]},
            ]
        }
        self.assertEqual(infer_target(only_selector), "手动选择")
        self.assertIsNone(infer_target({"proxy-groups": []}))

    def test_reapply_migrates_stale_direct_egress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            meta = meta_config()
            kwargs = {
                "providers_dir": str(meta_dir / "AI-Rules"),
                "output_dir": str(meta_dir),
            }
            base = base_config()
            base["proxy-groups"].append(
                {"name": "自动选择", "type": "url-test", "proxies": ["PROXY"]}
            )
            # Simulate a profile written by an older ai-rulec that hardcoded DIRECT.
            stale = copy.deepcopy(base)
            stale["rule-providers"] = {
                "openai": {"path": "./AI-Rules/openai.yaml"}
            }
            stale["proxy-groups"] += [
                {"name": "AI-openai", "type": "select", "proxies": ["DIRECT"]},
                {"name": "AI", "type": "select", "proxies": ["AI-openai"]},
            ]
            stale["rules"] = ["RULE-SET,openai,AI-openai", "MATCH,DIRECT"]
            merged, report = merge_profile(
                stale, meta, replace_existing=True, **kwargs
            )
            self.assertEqual(report["target"], "自动选择")
            self.assertEqual(report["target_source"], "inferred")
            groups = {group["name"]: group for group in merged["proxy-groups"]}
            self.assertEqual(groups["AI-openai"]["proxies"], ["自动选择", "DIRECT"])
            self.assertEqual(merged["rules"].count("RULE-SET,openai,AI-openai"), 1)


class ProfileCacheTests(unittest.TestCase):
    def test_generated_timestamp_does_not_invalidate_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, profile, rules = make_home(Path(tmp))
            cached = home / "cache" / "openai.yaml"
            cached.parent.mkdir(parents=True)
            cached.write_text(
                "# generated: 2026-11-22T05:55:18+00:00\n"
                "payload:\n  - DOMAIN-SUFFIX,openai.example\n",
                encoding="utf-8",
            )
            write_yaml(
                home / "config.yaml",
                {
                    "rule-providers": {
                        "openai": {"type": "file", "path": "./cache/openai.yaml"}
                    }
                },
            )
            before = inspect_provider_cache(profile, rules_dir=rules)
            self.assertEqual(before["summary"]["ok"], 1, before)
            self.assertEqual(before["summary"]["mismatch"], 0, before)

    def test_payload_change_still_invalidates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, profile, rules = make_home(Path(tmp))
            cached = home / "cache" / "openai.yaml"
            cached.parent.mkdir(parents=True)
            cached.write_text(
                "# generated: 2026-11-22T05:55:18+00:00\n"
                "payload:\n  - DOMAIN-SUFFIX,stale.example\n",
                encoding="utf-8",
            )
            write_yaml(
                home / "config.yaml",
                {
                    "rule-providers": {
                        "openai": {"type": "file", "path": "./cache/openai.yaml"}
                    }
                },
            )
            before = inspect_provider_cache(profile, rules_dir=rules)
            self.assertEqual(before["summary"]["mismatch"], 1, before)

    def test_missing_cache_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, profile, rules = make_home(Path(tmp))
            write_yaml(
                home / "config.yaml",
                {
                    "mixed-port": 7890,
                    "rule-providers": {
                        "openai": {
                            "type": "file",
                            "behavior": "classical",
                            "path": "./cache/openai.yaml",
                        }
                    },
                },
            )
            before = inspect_provider_cache(profile, rules_dir=rules)
            self.assertEqual(before["summary"]["missing"], 1)
            repaired = repair_provider_cache(profile, rules_dir=rules)
            self.assertTrue(repaired["ok"], repaired)
            after = inspect_provider_cache(profile, rules_dir=rules)
            self.assertTrue(after["ok"], after)
            self.assertTrue((home / "cache" / "openai.yaml").is_file())

    def test_mismatched_cache_is_repaired_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, profile, rules = make_home(Path(tmp))
            target = home / "cache" / "openai.yaml"
            target.parent.mkdir(parents=True)
            target.write_text("stale\n", encoding="utf-8")
            write_yaml(
                home / "config.yaml",
                {
                    "rule-providers": {
                        "openai": {
                            "type": "file",
                            "path": "./cache/openai.yaml",
                        }
                    }
                },
            )
            repaired = repair_provider_cache(profile, rules_dir=rules)
            self.assertTrue(repaired["ok"], repaired)
            self.assertEqual(
                [item["name"] for item in repaired["repaired"]], ["openai"]
            )
            self.assertTrue(list(target.parent.glob("openai.yaml.bak.*")))

    def test_doctor_reports_ok_after_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, profile, rules = make_home(Path(tmp))
            write_yaml(
                home / "config.yaml",
                {
                    "rule-providers": {
                        "openai": {
                            "type": "file",
                            "path": "./cache/openai.yaml",
                        }
                    }
                },
            )
            report = doctor_profile(profile, rules_dir=rules, fix=True)
            self.assertTrue(report["ok"], report)
            self.assertTrue(report["cache"]["ok"], report["cache"])

            status = profile_status(profile)
            self.assertTrue(status["ok"], status)
            self.assertEqual(status["cache"]["summary"]["ok"], 1)
            self.assertIsNone(status["port"])


class ApplyTests(unittest.TestCase):
    def test_in_place_apply_and_idempotent_second_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            home, profile, _ = make_home(root)
            write_yaml(
                home / "config.yaml",
                {
                    "mixed-port": 7890,
                    "rule-providers": {
                        "openai": {
                            "type": "file",
                            "behavior": "classical",
                            "path": "./cache/openai.yaml",
                        }
                    },
                },
            )
            first = apply_profile(
                str(profile),
                str(meta_dir / "meta.yaml"),
                target="PROXY",
                mapping={"openai": "PROXY"},
                backup=False,
                verify_mihomo=None,
            )
            self.assertTrue(first["ok"], first)
            self.assertTrue(first["cache"]["ok"], first["cache"])
            data = yaml.safe_load(profile.read_text(encoding="utf-8"))
            groups = {group["name"]: group for group in data["proxy-groups"]}
            self.assertEqual(
                groups["AI-openai"]["proxies"], ["PROXY", "DIRECT"]
            )
            self.assertEqual(data["rules"][0], "RULE-SET,openai,AI-openai")
            second = apply_profile(
                str(profile),
                str(meta_dir / "meta.yaml"),
                target="PROXY",
                mapping={"openai": "PROXY"},
                backup=False,
                verify_mihomo=None,
            )
            self.assertEqual(second["groups_replaced"], 2)
            self.assertEqual(second["rules_removed"], 1)
            data = yaml.safe_load(profile.read_text(encoding="utf-8"))
            self.assertEqual(data["rules"].count("RULE-SET,openai,AI-openai"), 1)
            self.assertEqual(
                len(
                    [
                        group
                        for group in data["proxy-groups"]
                        if group["name"] == "AI-openai"
                    ]
                ),
                1,
            )


class ApplyValidationTests(unittest.TestCase):
    """A rejected merge must not touch the client's files."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.meta_dir = make_meta_dir(self.root)
        self.home, self.profile, self.rules = make_home(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_runtime_config(self) -> None:
        write_yaml(
            self.home / "config.yaml",
            {
                "mixed-port": 7890,
                "rule-providers": {
                    "openai": {
                        "type": "file",
                        "behavior": "classical",
                        "path": "./cache/openai.yaml",
                    }
                },
            },
        )

    def test_failed_validation_leaves_profile_and_rules_untouched(self) -> None:
        from unittest import mock

        from ai_rulec import profile as profile_module

        before_profile = self.profile.read_bytes()
        before_rules = (self.rules / "openai.yaml").read_bytes()
        with mock.patch.object(
            profile_module,
            "_validate_merged",
            side_effect=ValueError("mihomo validation failed: bad config"),
        ):
            with self.assertRaises(ValueError):
                apply_profile(
                    str(self.profile),
                    str(self.meta_dir / "meta.yaml"),
                    target="PROXY",
                    backup=False,
                    verify_mihomo=None,
                )
        self.assertEqual(self.profile.read_bytes(), before_profile)
        self.assertEqual((self.rules / "openai.yaml").read_bytes(), before_rules)
        self.assertFalse(list(self.root.rglob("*.tmp.yaml")))

    def test_validation_only_keys_never_reach_the_profile(self) -> None:
        from unittest import mock

        from ai_rulec import profile as profile_module

        self.write_runtime_config()
        def fake_validate(merged, **kwargs):
            self.assertIn("proxy-groups", merged)
            return {"ok": True, "returncode": 0}

        with mock.patch.object(profile_module, "_validate_merged", side_effect=fake_validate):
            report = apply_profile(
                str(self.profile),
                str(self.meta_dir / "meta.yaml"),
                target="PROXY",
                backup=False,
                verify_mihomo=None,
            )
        self.assertTrue(report["ok"], report)
        data = yaml.safe_load(self.profile.read_text(encoding="utf-8"))
        self.assertNotIn("geodata-mode", data)
        self.assertEqual(data["rules"][0], "RULE-SET,openai,AI-openai")
        self.assertTrue(report["cache"]["ok"], report["cache"])

    def test_cache_repair_runs_before_the_profile_is_replaced(self) -> None:
        from unittest import mock

        from ai_rulec import profile as profile_module

        self.write_runtime_config()
        seen: dict[str, bool] = {}

        def fake_repair(profile_path, **kwargs):
            # Only apply_profile writes this header, so it marks the swap.
            seen["profile_written"] = Path(profile_path).read_text(
                encoding="utf-8"
            ).startswith("# merged by ai-rulec")
            return {"ok": True, "managed": 1, "ok_count": 1}

        with mock.patch.object(
            profile_module, "_validate_merged", return_value={"ok": True}
        ), mock.patch.object(
            profile_module, "repair_provider_cache", side_effect=fake_repair
        ):
            apply_profile(
                str(self.profile),
                str(self.meta_dir / "meta.yaml"),
                target="PROXY",
                backup=False,
                verify_mihomo=None,
            )
        self.assertFalse(seen.get("profile_written"), seen)

    def test_failed_cache_repair_leaves_the_profile_untouched(self) -> None:
        from unittest import mock

        from ai_rulec import profile as profile_module

        self.write_runtime_config()
        before = self.profile.read_bytes()
        with mock.patch.object(
            profile_module, "_validate_merged", return_value={"ok": True}
        ), mock.patch.object(
            profile_module,
            "repair_provider_cache",
            return_value={"ok": False, "errors": ["permission denied"]},
        ):
            with self.assertRaises(ValueError):
                apply_profile(
                    str(self.profile),
                    str(self.meta_dir / "meta.yaml"),
                    target="PROXY",
                    backup=False,
                    verify_mihomo=None,
                )
        self.assertEqual(self.profile.read_bytes(), before)


class WatchTests(unittest.TestCase):
    def test_reconcile_repairs_cache_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, profile, rules = make_home(Path(tmp))
            write_yaml(
                home / "config.yaml",
                {
                    "mixed-port": 7890,
                    "rule-providers": {
                        "openai": {
                            "type": "file",
                            "path": "./cache/openai.yaml",
                        }
                    },
                },
            )
            state = home / "watch.json"
            first = reconcile_once(
                str(profile),
                rules_dir=str(rules),
                settle=0,
                state_path=str(state),
            )
            self.assertEqual(first["action"], "cache_repaired", first)
            self.assertTrue(first["ok"], first)
            second = reconcile_once(
                str(profile),
                rules_dir=str(rules),
                settle=0,
                state_path=str(state),
            )
            self.assertEqual(second["action"], "skipped", second)

    def test_reconcile_reapplies_then_waits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            home, profile, rules = make_home(root)
            write_yaml(home / "config.yaml", {"mixed-port": 7890})
            state = home / "watch.json"
            first = reconcile_once(
                str(profile),
                meta_path=str(meta_dir / "meta.yaml"),
                rules_dir=str(rules),
                settle=0,
                state_path=str(state),
            )
            self.assertEqual(first["action"], "reapplied", first)
            self.assertTrue(first["ok"], first)
            second = reconcile_once(
                str(profile),
                meta_path=str(meta_dir / "meta.yaml"),
                rules_dir=str(rules),
                settle=0,
                state_path=str(state),
            )
            self.assertEqual(second["action"], "waiting_for_config", second)

    def test_waiting_for_config_escalates_to_restart(self) -> None:
        """A client that never reloads config must not leave us waiting forever."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            home, profile, rules = make_home(root)
            write_yaml(home / "config.yaml", {"mixed-port": 7890})
            state_path = home / "watch.json"
            kwargs = dict(
                meta_path=str(meta_dir / "meta.yaml"),
                rules_dir=str(rules),
                settle=0,
                state_path=str(state_path),
            )
            first = reconcile_once(str(profile), **kwargs)
            self.assertEqual(first["action"], "reapplied", first)
            results = [reconcile_once(str(profile), **kwargs) for _ in range(3)]
            self.assertEqual(
                {item["action"] for item in results}, {"waiting_for_config"}
            )
            self.assertLess(results[0]["waiting_cycles"], watch_module.WAITING_LIMIT)
            self.assertNotIn("restart the client", " ".join(results[0]["warnings"]))
            final = results[-1]
            self.assertEqual(final["waiting_cycles"], watch_module.WAITING_LIMIT)
            self.assertTrue(
                any("restart the client" in warning for warning in final["warnings"]),
                final,
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["waiting_cycles"], watch_module.WAITING_LIMIT)

    def test_reconcile_reapplies_after_profile_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            home, profile, rules = make_home(root)
            write_yaml(profile, base_config())
            write_yaml(home / "config.yaml", {"mixed-port": 7890})
            result = reconcile_once(
                str(profile),
                meta_path=str(meta_dir / "meta.yaml"),
                rules_dir=str(rules),
                settle=0,
                state_path=str(home / "watch.json"),
            )
            self.assertEqual(result["action"], "reapplied", result)
            self.assertTrue(result["ok"], result)
            data = yaml.safe_load(profile.read_text(encoding="utf-8"))
            self.assertIn(
                "AI-openai",
                [group["name"] for group in data["proxy-groups"]],
            )

    def test_reconcile_reapplies_unmanaged_with_matching_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta_dir = make_meta_dir(root)
            home, profile, rules = make_home(root)
            write_yaml(profile, base_config())
            config = home / "config.yaml"
            write_yaml(config, {"mixed-port": 7890})
            state_file = home / "watch.json"
            state_file.write_text(
                json.dumps(
                    {
                        "profile_hash": hashlib.sha256(
                            profile.read_bytes()
                        ).hexdigest(),
                        "config_hash": hashlib.sha256(
                            config.read_bytes()
                        ).hexdigest(),
                        "last_ok": True,
                        "last_action": "cache_ok",
                    }
                ),
                encoding="utf-8",
            )
            result = reconcile_once(
                str(profile),
                meta_path=str(meta_dir / "meta.yaml"),
                rules_dir=str(rules),
                settle=0,
                state_path=str(state_file),
            )
            self.assertEqual(result["action"], "reapplied", result)
            data = yaml.safe_load(profile.read_text(encoding="utf-8"))
            self.assertIn(
                "AI-openai",
                [group["name"] for group in data["proxy-groups"]],
            )

    def test_reconcile_reports_missing_ai_without_reapply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home, profile, rules = make_home(Path(tmp))
            write_yaml(home / "config.yaml", {"mixed-port": 7890})
            result = reconcile_once(
                str(profile),
                rules_dir=str(rules),
                reapply=False,
                settle=0,
                state_path=str(home / "watch.json"),
            )
            self.assertEqual(result["action"], "config_missing_ai", result)
            self.assertFalse(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
