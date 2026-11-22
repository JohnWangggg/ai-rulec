from __future__ import annotations

import json
import os
import tempfile
import unittest

from ai_rulec.singbox import (
    _sanitize_once,
    apply_singbox,
    infer_singbox_target,
    patch_config,
)


def base_config() -> dict:
    return {
        "outbounds": [
            {"type": "selector", "tag": "select", "outbounds": ["direct"]},
            {"type": "selector", "tag": "proxy", "outbounds": ["direct"]},
            {"type": "direct", "tag": "direct"},
        ],
        "route": {
            "final": "select",
            "rules": [{"network": "tcp", "outbound": "direct"}],
        },
    }


class PatchTests(unittest.TestCase):
    def test_adds_selectors_rule_sets_and_routes(self) -> None:
        patched, report = patch_config(base_config(), ["openai", "gemini"], target="select")
        tags = [item["tag"] for item in patched["outbounds"]]
        self.assertIn("AI-openai", tags)
        self.assertIn("AI-gemini", tags)
        self.assertIn("AI", tags)
        self.assertEqual(report["outbounds_added"], 3)
        self.assertEqual(report["rule_sets_added"], 2)
        self.assertEqual(report["rules_added"], 2)
        self.assertEqual(
            [item["tag"] for item in patched["route"]["rule_set"]],
            ["ai-openai", "ai-gemini"],
        )
        self.assertEqual(
            patched["route"]["rules"][0],
            {"rule_set": ["ai-openai"], "outbound": "AI-openai"},
        )
        self.assertEqual(patched["route"]["final"], "select")

    def test_patch_twice_is_idempotent(self) -> None:
        once, _ = patch_config(base_config(), ["openai"], target="select")
        twice, report = patch_config(once, ["openai"], target="select")
        self.assertEqual(report["outbounds_removed"], 2)
        self.assertEqual(report["rule_sets_removed"], 1)
        self.assertEqual(report["rules_removed"], 1)
        self.assertEqual(
            len([tag for tag in (item["tag"] for item in twice["outbounds"]) if tag.startswith("AI")]),
            2,
        )

    def test_infer_target_prefers_select_over_proxy(self) -> None:
        self.assertEqual(infer_singbox_target(base_config()), "select")

    def test_unknown_target_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            patch_config(base_config(), ["openai"], target="missing")

    def test_mapping_must_exist(self) -> None:
        with self.assertRaises(ValueError):
            patch_config(base_config(), ["openai"], target="select", mapping={"openai": "nope"})

    def test_sanitize_stubs_fork_outbound_and_field(self) -> None:
        config = {
            "outbounds": [
                {"type": "balancer", "tag": "balance", "tolerance": 50},
                {"type": "vmess", "tag": "node", "tls_fragment": True},
            ]
        }
        changed = _sanitize_once(
            config, 'outbounds[0].tolerance: json: unknown field "tolerance"'
        )
        self.assertEqual(changed, ["outbounds[0].tolerance"])
        changed = _sanitize_once(config, "unknown outbound type: balancer")
        self.assertEqual(config["outbounds"][0]["type"], "direct")
        self.assertTrue(changed)


class ApplyTests(unittest.TestCase):
    def test_apply_copies_rules_and_backs_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.json")
            lists_dir = os.path.join(tmp, "lists")
            os.makedirs(lists_dir)
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump(base_config(), fh)
            with open(os.path.join(lists_dir, "openai.json"), "w", encoding="utf-8") as fh:
                json.dump({"version": 3, "rules": [{"domain_suffix": ["openai.com"]}]}, fh)

            report = apply_singbox(
                config_path,
                ["openai"],
                lists_dir=lists_dir,
                target="select",
            )
            self.assertTrue(report["ok"])
            self.assertTrue(os.path.exists(report["backup"]))
            self.assertTrue(os.path.exists(os.path.join(tmp, "AI-Rules", "openai.json")))
            with open(config_path, encoding="utf-8") as fh:
                applied = json.load(fh)
            self.assertIn("AI-openai", [item["tag"] for item in applied["outbounds"]])

    def test_dry_run_does_not_touch_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.json")
            with open(config_path, "w", encoding="utf-8") as fh:
                json.dump(base_config(), fh)
            with open(config_path, encoding="utf-8") as fh:
                before = fh.read()
            report = apply_singbox(config_path, ["openai"], target="select", dry_run=True)
            self.assertTrue(report["ok"])
            with open(config_path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)


if __name__ == "__main__":
    unittest.main()
