"""Catalog ownership tests: the flags that drive strict filtering must fire."""

from __future__ import annotations

import unittest

from ai_rulec.catalog import build_catalog
from ai_rulec.models import Rule

SERVICES = {
    "openai": {
        "display_name": "OpenAI",
        "aliases": ["openai", "chatgpt"],
        "known_domains": ["openai.com", "chatgpt.com"],
        "known_processes": ["ChatGPT"],
    },
    "meta": {
        "display_name": "Meta AI",
        # "meta" is a bare brand alias: it must only match the first label.
        "aliases": ["meta", "muse"],
        "known_domains": ["muse.ai", "ai.meta.com"],
        "known_processes": ["muse"],
    },
}


def _domain(value: str, hint: str | None, source: str = "jinx") -> Rule:
    return Rule(
        source=source,
        path=f".sources/{source}.yaml",
        lineno=1,
        kind="domain_suffix",
        value=value,
        service_hint=hint,
    )


class OwnerMatchingTests(unittest.TestCase):
    def test_bare_alias_anchors_at_the_first_label(self) -> None:
        # No service hint: ownership has to come from the domain itself.
        catalog, _ = build_catalog(
            [_domain("shop.meta.example", None), _domain("meta.example", None)],
            SERVICES,
            {},
        )
        domains = catalog["domains"]
        # "shop.meta.example" starts with "shop", so the bare alias "meta"
        # must not claim it for the Meta service.
        self.assertNotIn("meta", domains["shop.meta.example"]["services"])
        self.assertIn("unknown_service", domains["shop.meta.example"]["flags"])
        self.assertIn("meta", domains["meta.example"]["services"])

    def test_dotted_alias_still_matches_as_a_suffix(self) -> None:
        services = dict(SERVICES)
        services["copilot"] = {
            "display_name": "Copilot",
            "aliases": ["github-copilot.io"],
            "known_domains": [],
            "known_processes": [],
        }
        catalog, _ = build_catalog(
            [_domain("api.github-copilot.io", None)], services, {}
        )
        self.assertIn(
            "copilot", catalog["domains"]["api.github-copilot.io"]["services"]
        )

    def test_hint_conflicting_with_the_domain_owner_is_flagged(self) -> None:
        # jinx files the OpenAI-owned domain under a Meta hint: a real
        # cross-repository conflict, and the only thing this flag is for.
        catalog, coverage = build_catalog([_domain("chatgpt.com", "meta")], SERVICES, {})
        self.assertIn("chatgpt.com", coverage["conflicts"]["hint_domain_mismatch"])
        # The hint wins for ownership (it is what the source intends), but the
        # conflict stays visible.
        self.assertIn("meta", catalog["domains"]["chatgpt.com"]["services"])

    def test_matching_hint_is_not_a_conflict(self) -> None:
        catalog, coverage = build_catalog(
            [_domain("chatgpt.com", "openai")], SERVICES, {}
        )
        self.assertEqual(coverage["conflicts"]["hint_domain_mismatch"], [])
        self.assertNotIn("hint_domain_mismatch", catalog["domains"]["chatgpt.com"]["flags"])


if __name__ == "__main__":
    unittest.main()
