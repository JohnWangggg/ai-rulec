from __future__ import annotations

import json
import os
import tempfile
import unittest

from ai_rulec.singbox_watch import reconcile


def base_config() -> dict:
    return {
        "outbounds": [
            {"type": "selector", "tag": "select", "outbounds": ["direct"]},
            {"type": "direct", "tag": "direct"},
        ],
        "route": {"final": "select", "rules": []},
    }


class WatchTests(unittest.TestCase):
    def make_home(self, root: str) -> tuple[str, str]:
        config = os.path.join(root, "config.json")
        lists = os.path.join(root, "lists")
        os.makedirs(lists)
        with open(config, "w", encoding="utf-8") as fh:
            json.dump(base_config(), fh)
        with open(os.path.join(lists, "openai.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": 3, "rules": [{"domain_suffix": ["openai.com"]}]}, fh)
        return config, lists

    def test_applies_then_skips_then_reapplies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, lists = self.make_home(tmp)
            first = reconcile(config, ["openai"], lists_dir=lists)
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["action"], "applied")
            self.assertTrue(os.path.exists(os.path.join(tmp, ".ai-rulec-singbox-watch.json")))

            second = reconcile(config, ["openai"], lists_dir=lists)
            self.assertEqual(second["action"], "unchanged")

            with open(config, "w", encoding="utf-8") as fh:
                json.dump(base_config(), fh)
            third = reconcile(config, ["openai"], lists_dir=lists)
            self.assertEqual(third["action"], "applied")
            with open(config, encoding="utf-8") as fh:
                applied = json.load(fh)
            self.assertIn("AI-openai", [item["tag"] for item in applied["outbounds"]])

    def test_dry_run_reports_would_apply_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, lists = self.make_home(tmp)
            with open(config, encoding="utf-8") as fh:
                before = fh.read()
            result = reconcile(config, ["openai"], lists_dir=lists, dry_run=True)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["action"], "would_apply")
            with open(config, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), before)

    def test_missing_rule_set_triggers_reapply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, lists = self.make_home(tmp)
            reconcile(config, ["openai"], lists_dir=lists)
            with open(config, encoding="utf-8") as fh:
                data = json.load(fh)
            data["route"]["rule_set"] = []
            with open(config, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            result = reconcile(config, ["openai"], lists_dir=lists)
            self.assertEqual(result["action"], "applied")
            with open(config, encoding="utf-8") as fh:
                repaired = json.load(fh)
            self.assertEqual(
                [item["tag"] for item in repaired["route"]["rule_set"]], ["ai-openai"]
            )

    def test_force_reapplies_existing_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config, lists = self.make_home(tmp)
            reconcile(config, ["openai"], lists_dir=lists)
            result = reconcile(config, ["openai"], lists_dir=lists, force=True)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["action"], "reapplied")


if __name__ == "__main__":
    unittest.main()
