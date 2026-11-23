import unittest

from ai_rulec.sources import filter_redistributable


class FilterRedistributableTests(unittest.TestCase):
    def test_keeps_only_marked_sources(self) -> None:
        cfg = {
            "default_root": ".sources",
            "sources": [
                {"name": "mit", "redistributable": True},
                {"name": "gpl2", "redistributable": False},
                {"name": "unknown"},
            ],
        }
        kept, dropped = filter_redistributable(cfg)
        self.assertEqual([s["name"] for s in kept["sources"]], ["mit"])
        self.assertEqual(dropped, ["gpl2", "unknown"])

    def test_input_config_is_not_mutated(self) -> None:
        cfg = {"sources": [{"name": "mit", "redistributable": True}]}
        filter_redistributable(cfg)
        self.assertEqual(len(cfg["sources"]), 1)

    def test_no_redistributable_source_raises(self) -> None:
        cfg = {"sources": [{"name": "gpl2", "redistributable": False}]}
        with self.assertRaises(ValueError):
            filter_redistributable(cfg)


if __name__ == "__main__":
    unittest.main()
