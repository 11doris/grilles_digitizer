"""Unit tests for the Phase C strain-model read layer + validator
(strain_model_phase_c_plan §3/§4, rollout step 1). Hand-written fixtures —
no corpus data. Run: python -m unittest."""
from __future__ import annotations

import copy
import unittest

from pipelines.chords.similarity.normalize import (
    derived_form_strains, expand_tune, is_compared, iter_parts, part_ids,
    resolve_anchor, resolve_part_ref, sections_view, strain_bars_total,
    strain_label_seq, validate_strains,
)


def bars(*symbols):
    return [{"bar": i + 1, "beats": {"1": sym}} for i, sym in enumerate(symbols)]


def part(label, *symbols, plays=None):
    out = {"label": label, "bars": bars(*symbols)}
    if plays is not None:
        out["plays"] = plays
    return out


def aaba() -> dict:
    """A plain lettered chorus (the common case) plus an aux coda."""
    return {
        "form": "32 A A B A'",
        "strains": [
            {"name": "chorus", "role": "chorus", "parts": [
                part("A", "C", "Am"),
                part("A", "C", "Am"),
                part("B", "F", "G7"),
                part("A'", "C", "G7"),
            ]},
            {"name": "coda", "role": "aux", "parts": [
                part("Coda", "C"),
            ]},
        ],
        "coda_jump": {"caption": "CODA Bar 2 :",
                      "from": {"strain": "chorus", "part": 3, "bar": 2}},
        "variants": [{"applies_to": "Bar 4",
                      "targets": [{"strain": "chorus", "part": 1, "bar": 2}],
                      "bars": bars("A7")}],
    }


def multi_strain() -> dict:
    """Verse + chorus + a stored-once repeat (plays: 2), Minor Swing style."""
    return {
        "strains": [
            {"name": "intro", "role": "strain", "parts": [
                part("A", "Am", "Dm", plays=2),
            ]},
            {"name": "verse", "role": "verse", "parts": [
                part("A", "E7", "Am"),
                part("A'", "E7", "Am"),
            ]},
            {"name": "chorus", "role": "chorus", "parts": [
                part("A", "Am", "Dm"),
                part("B", "Dm", "E7"),
            ]},
        ],
    }


class TestPartIds(unittest.TestCase):
    def test_chorus_ids_use_letters_and_counters(self):
        self.assertEqual(part_ids(aaba()["strains"][0]),
                         ["A", "A1", "B", "A2"])

    def test_prefixed_ids(self):
        t = multi_strain()
        self.assertEqual(part_ids(t["strains"][0]), ["intro_A"])
        self.assertEqual(part_ids(t["strains"][1]), ["verse_A", "verse_A1"])

    def test_single_part_aux_id_is_bare_name(self):
        self.assertEqual(part_ids(aaba()["strains"][1]), ["coda"])

    def test_word_label_id(self):
        blues = {"name": "chorus", "role": "chorus",
                 "parts": [part("BLUES", "F7")]}
        self.assertEqual(part_ids(blues), ["BLUES"])

    def test_iter_parts_document_order(self):
        self.assertEqual([pid for pid, _s, _p in iter_parts(multi_strain())],
                         ["intro_A", "verse_A", "verse_A1", "A", "B"])


class TestDerivedViews(unittest.TestCase):
    def test_sections_view_strains(self):
        view = sections_view(aaba())
        self.assertEqual(list(view), ["A", "A1", "B", "A2", "coda"])
        self.assertEqual(view["B"][1]["beats"]["1"], "G7")

    def test_sections_view_legacy_passthrough(self):
        legacy = {"sections": {"A": bars("C"), "B": bars("F")}}
        self.assertIs(sections_view(legacy), legacy["sections"])

    def test_label_seq_and_bars_respect_plays(self):
        intro = multi_strain()["strains"][0]
        self.assertEqual(strain_label_seq(intro), ["A", "A"])
        self.assertEqual(strain_bars_total(intro), 4)  # 2 bars x plays 2

    def test_derived_form_strains(self):
        fs = derived_form_strains(aaba())
        self.assertEqual(fs["chorus"],
                         {"bars": 8, "labels": ["A", "A", "B", "A'"]})
        self.assertEqual(fs["coda"], {"bars": 1, "labels": ["Coda"]})

    def test_is_compared_excludes_verse(self):
        flags = {s["name"]: is_compared(s) for s in multi_strain()["strains"]}
        self.assertEqual(flags,
                         {"intro": True, "verse": False, "chorus": True})

    def test_expand_tune_one_entry_per_part(self):
        expanded = expand_tune(multi_strain())
        self.assertEqual(list(expanded),
                         ["intro_A", "verse_A", "verse_A1", "A", "B"])
        # 2 bars -> 4 half-bar slots each; plays does NOT re-expand
        self.assertTrue(all(len(s) == 4 for s in expanded.values()))


class TestAnchors(unittest.TestCase):
    def test_resolve_anchor(self):
        t = aaba()
        strain, prt, pid = resolve_anchor(t, t["coda_jump"]["from"])
        self.assertEqual(strain["name"], "chorus")
        self.assertEqual(prt["label"], "A'")
        self.assertEqual(pid, "A2")

    def test_resolve_anchor_dangling(self):
        t = aaba()
        with self.assertRaisesRegex(ValueError, "unknown strain"):
            resolve_anchor(t, {"strain": "bridge", "part": 0})
        with self.assertRaisesRegex(ValueError, "part 9"):
            resolve_anchor(t, {"strain": "chorus", "part": 9})
        with self.assertRaisesRegex(ValueError, "bar 99"):
            resolve_anchor(t, {"strain": "chorus", "part": 0, "bar": 99})

    def test_resolve_part_ref(self):
        t = aaba()
        strain, idx = resolve_part_ref(t, "A2")
        self.assertEqual((strain["name"], idx), ("chorus", 3))
        # bare strain name works for single-part strains
        self.assertEqual(resolve_part_ref(t, "coda"), (t["strains"][1], 0))
        self.assertIsNone(resolve_part_ref(t, "nope"))


class TestValidator(unittest.TestCase):
    def errs(self, mutate) -> str:
        tune = copy.deepcopy(aaba())
        mutate(tune)
        return "\n".join(validate_strains(tune))

    def test_clean(self):
        self.assertEqual(validate_strains(aaba()), [])
        self.assertEqual(validate_strains(multi_strain()), [])

    def test_loud_rejects(self):
        self.assertIn("non-empty list",
                      self.errs(lambda t: t.update(strains=[])))
        self.assertIn("role must be one of",
                      self.errs(lambda t: t["strains"][0].update(role="lead")))
        self.assertIn("role 'chorus' requires name 'chorus'",
                      self.errs(lambda t: t["strains"][0].update(name="thema")))
        self.assertIn("unknown named strain", self.errs(
            lambda t: t["strains"][1].update(name="outro", role="strain")))
        self.assertIn("unknown aux connector",
                      self.errs(lambda t: t["strains"][1].update(name="outro")))
        self.assertIn("duplicate strain name", self.errs(
            lambda t: t["strains"][1].update(name="chorus", role="chorus")))
        self.assertIn("plays must be an int >= 1", self.errs(
            lambda t: t["strains"][0]["parts"][0].update(plays=0)))
        self.assertIn("label must be a non-empty string", self.errs(
            lambda t: t["strains"][0]["parts"][0].update(label="")))
        self.assertIn("bars must be a non-empty list", self.errs(
            lambda t: t["strains"][0]["parts"][0].update(bars=[])))
        # within a strain, counters dedupe; a cross-strain collision (a chorus
        # part labelled like the aux connector's bare id) is caught tune-wide
        self.assertIn("duplicate part id", self.errs(
            lambda t: t["strains"][0]["parts"][1].update(label="coda")))
        self.assertIn("targets[0]", self.errs(
            lambda t: t["variants"][0]["targets"][0].update(part=7)))
        self.assertIn("coda_jump.from", self.errs(
            lambda t: t["coda_jump"]["from"].update(strain="gone")))
        self.assertIn("section_keys", self.errs(
            lambda t: t.update(section_keys={"Z": {"tonic": "F",
                                                   "mode": "major"}})))


if __name__ == "__main__":
    unittest.main()
