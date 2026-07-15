"""Analyzer unit tests (harmonic_analysis_spec §7): one rule per test on
synthetic charts, plus the region/section/pivot behaviours.
Run: python -m unittest pipelines.chords.harmonic_analysis.test_analyze
"""
from __future__ import annotations

import unittest

from pipelines.chords.harmonic_analysis.analyze import (
    ANALYSIS_VERSION, analyze_tune, load_catalog,
)


def tune_of(*parts: tuple[str, list[str]], key=("C", "major"),
            section_keys=None) -> dict:
    """Analysis of a strains-model tune built from ("A", ["Dm7", "G7 C"])
    part specs — each string is one bar, chords land on beats 1 and 3."""
    built = []
    for label, bars in parts:
        out_bars = []
        for i, text in enumerate(bars):
            beats = {str(1 + 2 * j): sym for j, sym in enumerate(text.split())}
            out_bars.append({"bar": i + 1, "beats": beats})
        built.append({"label": label, "bars": out_bars})
    tune = {"strains": [{"name": "chorus", "role": "chorus", "parts": built}]}
    return analyze_tune(tune, {"tonic": key[0], "mode": key[1]},
                        section_keys=section_keys)


def numerals(part: dict) -> list[str]:
    return [c["numeral"] for c in part["chords"]]


def link_types(part: dict) -> list[str]:
    return [link["type"] for link in part.get("links", [])]


def block_ids(part: dict) -> list[str]:
    return [b["id"] for b in part.get("blocks", [])]


class TestNumeralsAndDevices(unittest.TestCase):
    def test_version_and_shape(self):
        doc = tune_of(("A", ["C", "G7", "C"]))
        self.assertEqual(doc["version"], ANALYSIS_VERSION)
        self.assertEqual(list(doc["parts"]), ["A"])
        self.assertNotIn("flags", doc)

    def test_plain_cadence_numerals_bracket_and_arrow(self):
        part = tune_of(("A", ["Dm7 G7", "Cmaj7"]))["parts"]["A"]
        self.assertEqual(numerals(part), ["ii7", "V7", "IΔ"])
        self.assertIn({"type": "iiV", "from": [1, 1], "to": [1, 3]},
                      part["links"])
        self.assertIn({"type": "fifth", "from": [1, 3], "to": [2, 1]},
                      part["links"])
        self.assertEqual(block_ids(part), ["cadence_251"])

    def test_minor_cadence(self):
        part = tune_of(("A", ["Dm7b5 G7", "Cm"]),
                       key=("C", "minor"))["parts"]["A"]
        self.assertEqual(numerals(part), ["iiø7", "V7", "i"])
        self.assertEqual(block_ids(part), ["cadence_251_minor"])

    def test_secondary_dominant_and_related_ii(self):
        part = tune_of(("A", ["C", "Em7 A7", "Dm7 G7", "C"]))["parts"]["A"]
        # A7 resolves down a fifth to Dm7 (degree II) -> V7/II; its related
        # ii (Em7, a fifth above A7) inherits the slash target.
        self.assertEqual(numerals(part),
                         ["I", "ii7/II", "V7/II", "ii7", "V7", "I"])
        roles = {c["numeral"]: c.get("role") for c in part["chords"]}
        self.assertEqual(roles["V7/II"], "sec_dom")
        self.assertEqual(roles["ii7/II"], "sec_ii")
        self.assertEqual(link_types(part).count("iiV"), 2)

    def test_tritone_sub_plain_and_of_degree(self):
        part = tune_of(("A", ["Dm7 Db7", "C"]))["parts"]["A"]
        # ii + subV to the tonic: dotted bracket, ii keeps its plain name.
        self.assertEqual(numerals(part), ["ii7", "subV7", "I"])
        self.assertIn("iiV_sub", link_types(part))
        self.assertIn({"type": "half", "from": [1, 3], "to": [2, 1]},
                      part["links"])

        part = tune_of(("A", ["C", "Cm7 B7", "Bb7"]),
                       key=("F", "major"))["parts"]["A"]
        # Cm7 B7 -> Bb7 in F: the ii of IV + the subV of IV (symbolism.jpg).
        self.assertEqual(numerals(part)[1:], ["ii7/IV", "subV7/IV", "IV7"])

    def test_subii_with_subv(self):
        part = tune_of(("A", ["Abm7 Db7", "C"]))["parts"]["A"]
        # ii–V *shape* a half step up (subii7 = related 2 of the subV).
        self.assertEqual(numerals(part), ["subii7", "subV7", "I"])
        self.assertIn("iiV_sub", link_types(part))

    def test_backdoor_and_to_minor(self):
        part = tune_of(("A", ["Fm7 Bb7", "C"]))["parts"]["A"]
        self.assertEqual(numerals(part), ["iv7", "bVII7", "I"])
        self.assertEqual(part["chords"][1]["role"], "backdoor")

        part = tune_of(("A", ["C C7", "Fmaj7 Fm7", "C"]))["parts"]["A"]
        self.assertIn({"type": "to_minor", "from": [2, 1], "to": [2, 3]},
                      part["links"])
        # C7 resolving down a fifth to IV reads as its secondary dominant.
        self.assertEqual(numerals(part)[1], "V7/IV")

    def test_diminished_passing_role(self):
        part = tune_of(("A", ["C C#o7", "Dm7 G7", "C"]))["parts"]["A"]
        # Ascending passing dim spells sharp: ♯Io7 between I and ii.
        self.assertEqual(numerals(part)[1], "#io7")
        self.assertEqual(part["chords"][1]["role"], "dim_passing")


class TestRegions(unittest.TestCase):
    def test_short_tonicization_stays_slash_notated(self):
        # ii–V to IV inside 2 bars: slash numerals, NO region (spec §2.3).
        part = tune_of(("A", ["C", "Gm7 C7", "F", "Dm7 G7", "C"]))["parts"]["A"]
        self.assertNotIn("regions", part)
        self.assertEqual(numerals(part)[1:4], ["ii7/IV", "V7/IV", "IV"])

    def test_long_tonicization_opens_region_with_pivot(self):
        # 4+ bars around F at the end of the part -> a region: numerals
        # inside read in F and the seam chord gets the dual reading.
        part = tune_of(("A", ["C", "Am7 Dm7", "Gm7 C7", "F", "Gm7 C7",
                              "F"]))["parts"]["A"]
        regions = part.get("regions") or []
        self.assertEqual(len(regions), 1)
        reg = regions[0]
        self.assertEqual((reg["tonic"], reg["mode"], reg["kind"]),
                         ("F", "major", "modulation"))
        self.assertEqual(reg["from"], [2, 3])
        self.assertEqual(reg["to"], [6, 1])
        # Inside the region the numerals read in F.
        self.assertEqual(numerals(part)[3:],
                         ["ii7", "V7", "I", "ii7", "V7", "I"])
        # Dm7 is diatonic in both C and F: the pivot opens the region.
        pivots = [c for c in part["chords"] if "pivot" in c]
        self.assertEqual(len(pivots), 1)
        self.assertEqual((pivots[0]["bar"], pivots[0]["beat"]), (2, 3))
        self.assertEqual(pivots[0]["pivot"],
                         {"key": "C", "mode": "major", "numeral": "ii7"})

    def test_section_key_part_gets_section_region(self):
        doc = tune_of(("A", ["C", "Dm7 G7", "C", "C"]),
                      ("B", ["Bbmaj7", "Cm7 F7", "Bb", "Dm7 G7"]),
                      section_keys={"B": {"tonic": "Bb", "mode": "major"}})
        b = doc["parts"]["B"]
        reg = b["regions"][0]
        self.assertEqual((reg["tonic"], reg["kind"]), ("Bb", "section"))
        # B numerals are Bb-relative; the closing ii–V resolves across the
        # part boundary into the next part's C (degree II of Bb).
        self.assertEqual(numerals(b),
                         ["IΔ", "ii7", "V7", "I", "ii7/II", "V7/II"])

    def test_relative_key_needs_longer_stretch(self):
        # 4 bars around the relative minor: idiomatic, no region.
        part = tune_of(("A", ["C", "Bm7b5 E7", "Am7", "Am7", "Dm7 G7",
                              "C"]))["parts"]["A"]
        self.assertNotIn("regions", part)
        self.assertEqual(numerals(part)[1:3], ["iiø7/VI", "V7/VI"])


class TestBlocks(unittest.TestCase):
    def test_turnaround_beats_overlapping_cadence(self):
        part = tune_of(("A", ["C", "F", "C Am7", "Dm7 G7"]))["parts"]["A"]
        self.assertEqual(block_ids(part), ["turnaround_1625"])

    def test_dominant_cycle_block(self):
        part = tune_of(("A", ["E7", "A7", "D7", "G7", "C"]))["parts"]["A"]
        self.assertIn("dominant_cycle", block_ids(part))

    def test_iiv_chain_block(self):
        part = tune_of(("A", ["Em7 A7", "Dm7 G7", "C"]))["parts"]["A"]
        self.assertIn("iiv_chain", block_ids(part))

    def test_catalog_loads_and_patterns_parse(self):
        entries = load_catalog()
        self.assertTrue(entries)
        self.assertTrue(all("_tokens" in e for e in entries))


class TestRobustness(unittest.TestCase):
    def test_parse_error_flags_part_not_tune(self):
        tune = {"strains": [{"name": "chorus", "role": "chorus", "parts": [
            {"label": "A", "bars": [{"bar": 1, "beats": {"1": "Qx9"}}]},
            {"label": "B",
             "bars": [{"bar": 1, "beats": {"1": "Dm7", "3": "G7"}},
                      {"bar": 2, "beats": {"1": "C"}}]},
        ]}]}
        doc = analyze_tune(tune, {"tonic": "C", "mode": "major"})
        self.assertNotIn("A", doc["parts"])
        self.assertIn("B", doc["parts"])
        self.assertTrue(any("part A" in f for f in doc["flags"]))


if __name__ == "__main__":
    unittest.main()
