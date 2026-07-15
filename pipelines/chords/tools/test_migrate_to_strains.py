"""Unit tests for the Phase C legacy->strains reshape (plan §6).

The corpus-wide equivalence gate lives in the tool itself
(`migrate_to_strains.py --check`); these tests pin the reshape on a
synthetic legacy tune so they keep working after the corpus is migrated.
Run: python -m unittest.
"""
from __future__ import annotations

import unittest

from pipelines.chords.tools.migrate_to_strains import (
    HARD, check_equivalence, derive_labels, parse_form, section_groups,
    strains_to_sections, tune_to_strains,
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

# ---------------------------------------------------------------------------
# Legacy ingest parsing (quarantined with the reshape): the printed-form
# alignment that seeds labels/plays when a raw tune is first converted.
# ---------------------------------------------------------------------------

def _hard(tune) -> list:
    return [m for lv, m in derive_labels(tune)[2] if lv == HARD]


class TestFormStrains(unittest.TestCase):
    def test_parse_form_splits_strains(self):
        # verse | chorus, each with its own bar count and label sequence
        strains = parse_form("8 A A' | 32 A A B A")
        self.assertEqual(strains, [
            {"bars": 8, "labels": ["A", "A'"]},
            {"bars": 32, "labels": ["A", "A", "B", "A"]},
        ])

    def test_parse_form_hyphen_and_jammed_and_coda(self):
        # a spaced hyphen also separates strains; "A'C" is jammed (two labels);
        # a "+ Coda" tail is auxiliary and contributes no label
        self.assertEqual(parse_form("16 A B - 12 BLUES"),
                         [{"bars": 16, "labels": ["A", "B"]},
                          {"bars": 12, "labels": ["BLUES"]}])
        self.assertEqual(parse_form("32 A B A'C")[0]["labels"],
                         ["A", "B", "A'", "C"])
        self.assertEqual(parse_form("64 A A B A + Coda")[0]["labels"],
                         ["A", "A", "B", "A"])

    def test_section_groups_split_verse_chorus_and_aux(self):
        groups = section_groups({"verse_A": [], "verse_A1": [], "A": [],
                                 "A1": [], "B": [], "A2": [], "coda": [],
                                 "Transition": []})
        # verse_* -> verse strain, plain letters -> chorus; coda and a
        # capitalised named key are aux (excluded)
        self.assertEqual(list(groups), ["verse", "chorus"])
        self.assertEqual(groups["verse"], ["verse_A", "verse_A1"])
        self.assertEqual(groups["chorus"], ["A", "A1", "B", "A2"])


class TestSectionLabels(unittest.TestCase):
    def test_prime_recovered_from_form(self):
        # "32 A A' B A" -> A1 is the primed variation, A2 an exact repeat
        _, labels, warn = derive_labels(
            {"form": "32 A A' B A",
             "sections": {"A": [], "A1": [], "B": [], "A2": []}})
        self.assertEqual(labels, {"A": "A", "A1": "A'", "B": "B", "A2": "A"})
        self.assertEqual([m for lv, m in warn if lv == HARD], [])

    def test_verse_chorus_joined_form(self):
        _, labels, warn = derive_labels(
            {"form": "8 A A' | 32 A A B A",
             "sections": {"verse_A": [], "verse_A1": [], "A": [], "A1": [],
                          "B": [], "A2": []}})
        self.assertEqual(labels["verse_A1"], "A'")
        self.assertEqual(labels["A1"], "A")
        self.assertFalse([m for lv, m in warn if lv == HARD])

    def test_verse_form_recovered_from_prose(self):
        # chorus-only form string; the verse letters live in the note
        struct, labels, warn = derive_labels({
            "form": "32 A A B A",
            "sections": {"verse_A": [], "verse_A1": [], "A": [], "A1": [],
                         "B": [], "A2": []},
            "notation_notes": {"verse": "A 16 A A grid sits above the chorus."},
        })
        self.assertEqual(struct["verse"]["labels"], ["A", "A"])
        self.assertEqual(struct["verse"]["source"], "notation_notes")
        self.assertEqual(labels["verse_A"], "A")
        self.assertFalse([m for lv, m in warn if lv == HARD])  # soft note only

    def test_aux_section_labelled_from_key(self):
        _, labels, _ = derive_labels(
            {"form": "32 A A B A''",
             "sections": {"A": [], "A1": [], "B": [], "A2": [], "coda": []}})
        self.assertEqual(labels["coda"], "Coda")
        self.assertEqual(labels["A2"], "A''")

    def test_identical_parts_strain_stored_once(self):
        # "16 A A" (a strain of identical parts) may store ONE A row; the form
        # keeps the repeat, form_strains carries it, no hard warning. Modelled
        # here as a three-strain piece (intro / theme / improv) like Minor Swing.
        tune = {
            "form": "16 A A | 16 A A | 16 A B",
            "sections": {"intro_A": [], "theme_A": [], "A": [], "B": []},
        }
        struct, labels, warn = derive_labels(tune)
        self.assertFalse([m for lv, m in warn if lv == HARD])
        self.assertEqual(labels, {"intro_A": "A", "theme_A": "A",
                                  "A": "A", "B": "B"})
        # the shortened strains keep their full "A A" label sequence
        self.assertEqual(struct["intro"]["labels"], ["A", "A"])
        self.assertEqual(struct["theme"]["labels"], ["A", "A"])
        self.assertEqual(struct["chorus"]["labels"], ["A", "B"])

    def test_bare_named_sections_absorb_extra_strains(self):
        # Minor Swing: three-strain form where the two "16 A A" strains are named
        # by bare sections (intro / thema) storing one row each. They are
        # promoted to strains because the form has spare strains for them.
        tune = {
            "form": "16 A A | 16 A A | 16 A B",
            "sections": {"intro": [], "thema": [], "impro_A": [], "impro_B": []},
        }
        _struct, labels, warn = derive_labels(tune)
        self.assertFalse([m for lv, m in warn if lv == HARD])
        self.assertEqual(labels, {"intro": "A", "thema": "A",
                                  "impro_A": "A", "impro_B": "B"})

    def test_bare_intro_without_spare_strain_stays_aux(self):
        # A genuine intro connector (form has no extra strain) is NOT promoted.
        _struct, labels, warn = derive_labels(
            {"form": "32 A A B A",
             "sections": {"intro": [], "A": [], "A1": [], "B": [], "A2": []}})
        self.assertFalse([m for lv, m in warn if lv == HARD])
        self.assertEqual(labels["intro"], "Intro")  # stayed auxiliary

    def test_mixed_repeat_stays_hard(self):
        # A repeat that is NOT all-identical (A A B -> 2 rows) is ambiguous and
        # must stay hard, not silently collapse.
        hard = _hard(
            {"form": "24 A A B", "sections": {"verse_A": [], "verse_A1": []}})
        self.assertTrue(hard)

    def test_count_mismatch_is_hard(self):
        hard = _hard(
            {"form": "64 A A A' B A'",
             "sections": {"A": [], "A1": [], "A2": [], "A3": [], "B": [],
                          "B1": []}})
        self.assertTrue(hard)


if __name__ == "__main__":
    unittest.main()
