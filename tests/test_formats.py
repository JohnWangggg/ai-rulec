from __future__ import annotations

import json
import unittest

from ai_rulec.formats import (
    infer_surge_target,
    singbox_document,
    singbox_route_fragment,
    surge_fragment,
    surge_lines,
    surge_list_text,
    surge_profile,
    tower_document,
)
from ai_rulec.surge import patch_profile

SUMMARY = {
    "domains": ["api.openai.com", "chatgpt.com"],
    "processes": ["Codex"],
    "ip_asns": ["399358"],
    "ip_cidrs": ["199.47.142.0/23", "2604:f20::/32"],
}


class SingBoxTests(unittest.TestCase):
    def test_document_drops_asns(self) -> None:
        doc = singbox_document("openai", SUMMARY)
        self.assertEqual(doc["version"], 3)
        joined = json.dumps(doc)
        self.assertNotIn("ip_asn", joined)
        self.assertNotIn("399358", joined)
        self.assertEqual(doc["rules"][0]["domain_suffix"], SUMMARY["domains"])
        self.assertEqual(doc["rules"][1]["process_name"], ["Codex"])
        self.assertEqual(
            doc["rules"][2]["ip_cidr"], ["199.47.142.0/23", "2604:f20::/32"]
        )

    def test_route_fragment_has_no_duplicate_outbounds(self) -> None:
        frag = singbox_route_fragment(["openai", "gemini"], "direct")
        tags = [item["tag"] for item in frag["outbounds"]]
        self.assertEqual(tags, ["AI-openai", "AI-gemini", "AI"])
        for group in frag["outbounds"]:
            self.assertEqual(
                len(group["outbounds"]), len(set(group["outbounds"])), group
            )
        self.assertEqual(frag["route"]["final"], "AI")
        self.assertEqual(
            frag["route"]["rule_set"][0]["path"], "./AI-Rules/openai.json"
        )


class SurgeFormatTests(unittest.TestCase):
    def test_lines_only_use_valid_prefixes(self) -> None:
        lines = surge_lines(SUMMARY)
        self.assertIn("DOMAIN-SUFFIX,chatgpt.com", lines)
        self.assertIn("PROCESS-NAME,Codex", lines)
        self.assertIn("IP-ASN,399358", lines)
        self.assertIn("IP-CIDR6,2604:f20::/32,no-resolve", lines)
        self.assertFalse(any(line.startswith("FINAL") for line in lines))

    def test_drop_asn_mode(self) -> None:
        lines = surge_lines(SUMMARY, asn_mode="drop")
        self.assertFalse(any(line.startswith("IP-ASN") for line in lines))

    def test_list_header(self) -> None:
        text = surge_list_text("openai", SUMMARY, "2026-01-01T00:00:00Z")
        self.assertTrue(text.startswith("# NAME: ai-rulec openai"))
        self.assertIn("# TOTAL: 6", text)

    def test_profile_is_complete(self) -> None:
        text = surge_profile(["openai"], "Proxy", generated_at="x")
        for section in ("[General]", "[Proxy]", "[Proxy Group]", "[Rule]"):
            self.assertIn(section, text)
        self.assertIn("AI-openai = select, Proxy, DIRECT", text)
        self.assertIn("RULE-SET,AI-Rules/openai.list,AI-openai", text)
        self.assertIn("FINAL,Proxy", text)

    def test_fragment_omits_final(self) -> None:
        text = surge_fragment(["openai"], "Proxy")
        self.assertNotIn("FINAL", text)
        self.assertIn("[Proxy Group]", text)


class SurgePatchTests(unittest.TestCase):
    BASE = """[General]
loglevel = notify

[Proxy Group]
Proxy = select, HK, US
AIGC = select, Proxy, DIRECT

[Rule]
RULE-SET,https://example.com/aigc.list,AIGC
FINAL,Proxy
"""

    def test_infers_existing_policy(self) -> None:
        self.assertEqual(infer_surge_target(self.BASE), "Proxy")

    def test_patch_inserts_groups_and_rules(self) -> None:
        patched, report = patch_profile(self.BASE, ["openai", "gemini"])
        self.assertEqual(report["target"], "Proxy")
        self.assertEqual(report["groups_added"], 3)
        self.assertEqual(report["rules_added"], 2)
        self.assertIn("AI-openai = select, Proxy, DIRECT", patched)
        self.assertIn("RULE-SET,AI-Rules/gemini.list,AI-gemini", patched)
        # user rules survive and AI rules land above them
        self.assertIn("RULE-SET,https://example.com/aigc.list,AIGC", patched)
        self.assertLess(
            patched.index("RULE-SET,AI-Rules/openai.list"),
            patched.index("RULE-SET,https://example.com/aigc.list"),
        )
        self.assertTrue(patched.rstrip().endswith("FINAL,Proxy"))

    def test_patch_is_idempotent(self) -> None:
        once, _ = patch_profile(self.BASE, ["openai"])
        twice, report = patch_profile(once, ["openai"])
        self.assertEqual(report["groups_removed"], 2)
        self.assertEqual(twice.count("AI-openai = select"), 1)
        self.assertEqual(twice.count("RULE-SET,AI-Rules/openai.list"), 1)

    def test_rules_last_keeps_user_priority(self) -> None:
        patched, _ = patch_profile(self.BASE, ["openai"], rules_position="before-final")
        self.assertLess(
            patched.index("RULE-SET,https://example.com/aigc.list"),
            patched.index("RULE-SET,AI-Rules/openai.list"),
        )
        self.assertLess(
            patched.index("RULE-SET,AI-Rules/openai.list"), patched.index("FINAL,Proxy")
        )

    def test_mapping_overrides_target(self) -> None:
        patched, _ = patch_profile(
            self.BASE, ["openai"], mapping={"openai": "AIGC"}
        )
        self.assertIn("AI-openai = select, AIGC, DIRECT", patched)

    def test_missing_rule_section_raises(self) -> None:
        with self.assertRaises(ValueError):
            patch_profile("[General]\nx = y\n", ["openai"], target="Proxy")


class TowerDocumentTests(unittest.TestCase):
    def test_inline_providers_and_groups(self) -> None:
        doc = tower_document(["openai"], {"openai": SUMMARY})
        providers = doc["rule-providers"]
        self.assertEqual(providers["openai"]["type"], "inline")
        self.assertEqual(providers["openai"]["behavior"], "classical")
        # Tower's reader cannot open loose files; path/url must never appear.
        for forbidden in ("path", "url"):
            self.assertNotIn(forbidden, providers["openai"])
        payload = providers["openai"]["payload"]
        self.assertIn("DOMAIN-SUFFIX,chatgpt.com", payload)
        self.assertIn("PROCESS-NAME,Codex", payload)
        self.assertIn("IP-CIDR,199.47.142.0/23,no-resolve", payload)
        self.assertIn("IP-CIDR6,2604:f20::/32,no-resolve", payload)
        names = [group["name"] for group in doc["proxy-groups"]]
        self.assertEqual(names, ["AI-openai", "AI"])
        self.assertIn("MATCH,AI", doc["rules"])

    def test_asn_drop_removes_ip_asn(self) -> None:
        doc = tower_document(["openai"], {"openai": SUMMARY}, asn_mode="drop")
        self.assertNotIn(
            "IP-ASN,399358", doc["rule-providers"]["openai"]["payload"]
        )

    def test_mapping_replaces_default_member(self) -> None:
        doc = tower_document(
            ["openai"], {"openai": SUMMARY}, mapping={"openai": "节点选择"}
        )
        group = doc["proxy-groups"][0]
        self.assertEqual(group["proxies"], ["节点选择"])
        self.assertTrue(group["include-all-proxies"])

    def test_service_without_rules_is_skipped(self) -> None:
        empty = {"domains": [], "processes": [], "ip_asns": [], "ip_cidrs": []}
        doc = tower_document(["openai", "gemini"], {"openai": SUMMARY, "gemini": empty})
        self.assertEqual(list(doc["rule-providers"]), ["openai"])
        self.assertEqual(doc["rules"], ["RULE-SET,openai,AI-openai", "MATCH,AI"])
        self.assertEqual([g["name"] for g in doc["proxy-groups"]], ["AI-openai", "AI"])

    def test_no_services_raises(self) -> None:
        empty = {"domains": [], "processes": [], "ip_asns": [], "ip_cidrs": []}
        with self.assertRaises(ValueError):
            tower_document(["gemini"], {"gemini": empty})


class TowerEmitTests(unittest.TestCase):
    """End-to-end: emit the Tower file, then re-check it the way verify does."""

    def _emit(self, directory: str) -> dict:
        from ai_rulec.emit import emit_all

        catalog = {
            "generated_at": "2026-11-23T00:00:00+00:00",
            "domains": {
                "api.openai.com": {"flags": [], "primary_service": "openai"},
                "chatgpt.com": {"flags": [], "primary_service": "openai"},
            },
            "source_meta": {},
        }
        coverage = {
            "services": {"openai": SUMMARY},
            "summary": {},
        }
        return emit_all(
            catalog,
            coverage,
            {"openai": {"display_name": "OpenAI"}},
            directory,
            strict=True,
            formats=("tower",),
        )

    def test_emit_writes_one_self_contained_file(self) -> None:
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            lock = self._emit(directory)
            self.assertEqual(lock["formats"], ["tower"])
            self.assertEqual(list(lock["artifacts"]), ["tower/ai-rules.yaml"])
            path = os.path.join(directory, "tower", "ai-rules.yaml")
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            self.assertNotIn("type: file", body)
            self.assertNotIn("./AI-Rules", body)

    def test_lock_artifact_keys_are_posix(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            lock = self._emit(directory)
            for key in lock["artifacts"]:
                # Windows builds used to leak `tower\ai-rules.yaml` into the lock.
                self.assertNotIn("\\", key)
            self.assertEqual(list(lock["artifacts"]), ["tower/ai-rules.yaml"])

    def test_verifier_rejects_file_providers(self) -> None:
        import os
        import tempfile

        from ai_rulec.verify import verify_outputs

        with tempfile.TemporaryDirectory() as directory:
            self._emit(directory)
            path = os.path.join(directory, "tower", "ai-rules.yaml")
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
            # The exact shape Tower's parser throws unsupportedSyntax on.
            broken = body.replace(
                "type: inline", "type: file", 1
            ).replace("behavior: classical", "path: ./AI-Rules/openai.yaml", 1)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(broken)
            report = verify_outputs(directory, {"services": {}, "summary": {}})
            self.assertFalse(report["ok"])
            self.assertTrue(
                any("type:file" in error or "type inline" in error for error in report["errors"]),
                report["errors"],
            )


if __name__ == "__main__":
    unittest.main()
