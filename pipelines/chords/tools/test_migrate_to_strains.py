"""Unit tests for the Phase C legacy->strains reshape (plan §6).

The corpus-wide equivalence gate lives in the tool itself
(`migrate_to_strains.py --check`); these tests pin the reshape on a
synthetic legacy tune so they keep working after the corpus is migrated.
Run: python -m unittest.
"""
from __future__ import annotations

import unittest

from pipelines.chords.tools.migrate_to_strains import (
    check_equivalence, strains_to_sections, tune_to_strains,
)


def bars(*symbols):
    return [{"bar": i + 1, "beats": {"1": s}} for i, s in enumerate(symbols)]


def legacy_tune() -> dict:
    return {
        "title": "FIXTURE",
        "form": "8 A A | 8 A B A' + Coda",
        "sections": {
            "intro_A": bars("Am", "E7", "Am", "E7"),  # stored once, plays 2
            "A": bars("C", "Am"),
            "B": bars("F", "G7"),
            "A1": bars("C", "G7"),
            "coda": bars("C"),
        },
        "section_labels": {"intro_A": "A", "A": "A", "B": "B", "A1": "A'",
                           "coda": "Coda"},
        "form_strains": {
            "intro": {"bars": 8, "labels": ["A", "A"]},
            "chorus": {"bars": 8, "labels": ["A", "B", "A'"]},
        },
        "variants": [{"applies_to": "Bar 4",
                      "targets": [{"section": "B", "bar": 2}],
                      "bars": bars("G7b9")}],
        "coda_jump": {"caption": "CODA :",
                      "from": {"section": "A1", "bar": 2}},
        "section_keys": {"B": {"tonic": "F", "mode": "major"}},
    }


class TestTuneToStrains(unittest.TestCase):
    def test_reshape(self):
        new = tune_to_strains(legacy_tune())
        self.assertNotIn("sections", new)
        self.assertNotIn("form_strains", new)
        self.assertNotIn("section_labels", new)
        names = [(s["name"], s["role"]) for s in new["strains"]]
        self.assertEqual(names, [("intro", "strain"), ("chorus", "chorus"),
                                 ("coda", "aux")])
        intro = new["strains"][0]["parts"]
        self.assertEqual(intro[0].get("plays"), 2)  # stored-once repeat
        chorus = new["strains"][1]["parts"]
        self.assertEqual([p["label"] for p in chorus], ["A", "B", "A'"])
        self.assertNotIn("plays", chorus[0])

    def test_anchors_and_maps_rewritten(self):
        new = tune_to_strains(legacy_tune())
        self.assertEqual(new["variants"][0]["targets"],
                         [{"strain": "chorus", "part": 1, "bar": 2}])
        self.assertEqual(new["coda_jump"]["from"],
                         {"strain": "chorus", "part": 2, "bar": 2})
        self.assertEqual(list(new["section_keys"]), ["B"])

    def test_equivalence_gate_clean_and_idempotent(self):
        old = legacy_tune()
        new = tune_to_strains(old)
        self.assertEqual(check_equivalence(old, new), [])
        self.assertIs(tune_to_strains(new), new)

    def test_down_conversion_round_trip(self):
        old = legacy_tune()
        back = strains_to_sections(tune_to_strains(old))
        self.assertEqual(list(back["sections"]),
                         ["intro_A", "A", "B", "A1", "coda"])
        self.assertEqual(back["sections"]["B"], old["sections"]["B"])
        self.assertEqual(back["variants"][0]["targets"],
                         [{"section": "B", "bar": 2}])
        self.assertEqual(back["coda_jump"]["from"],
                         {"section": "A1", "bar": 2})


if __name__ == "__main__":
    unittest.main()
