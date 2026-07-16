"""Block-mining tool tests: the naming/id rules and the candidate filters.
Run: python -m unittest pipelines.chords.harmonic_analysis.test_mine_blocks
"""
from __future__ import annotations

import unittest

from pipelines.chords.harmonic_analysis.analyze import load_catalog
from pipelines.chords.harmonic_analysis.mine_blocks import (
    candidates, entry_for, in_catalog, is_seam, mine,
)


def doc(bars: list[str], key=("C", "major")) -> dict:
    """A one-part annotated document from ["C", "E7 Am"]-style bar specs
    (chords land on beats 1 and 3)."""
    built = [{"bar": i + 1,
              "beats": {str(1 + 2 * j): s for j, s in enumerate(t.split())}}
             for i, t in enumerate(bars)]
    return {"strains": [{"name": "chorus", "role": "chorus",
                         "parts": [{"label": "A", "bars": built}]}],
            "key": {"tonic": key[0], "mode": key[1]}}


class TestNaming(unittest.TestCase):
    def test_formula_name_id_and_pattern(self):
        e = entry_for(((0, "maj"), (3, "dim"), (2, "min"), (7, "dom")))
        self.assertEqual(e["name"], "I–♭IIIo–ii–V7")
        self.assertEqual(e["id"], "i_biiio_ii_v7")
        self.assertEqual(e["pattern"], "I:maj bIII:dim ii:min V:dom")
        self.assertEqual(e["max_bars"], 8)
        self.assertTrue(e["generic"])

    def test_half_diminished_and_sharp_degree(self):
        e = entry_for(((2, "m7b5"), (7, "dom"), (0, "min")))
        self.assertEqual(e["name"], "iiø–V7–i")
        self.assertEqual(e["id"], "iih_v7_i")
        self.assertEqual(e["pattern"], "ii:m7b5 V:dom i:min")
        e = entry_for(((5, "maj"), (6, "dim"), (0, "maj")))
        self.assertEqual(e["name"], "IV–♯IVo–I")
        self.assertEqual(e["id"], "iv_sivo_i")
        self.assertEqual(e["max_bars"], 6)

    def test_minor_id_suffix_only_on_bare_ascii_clash(self):
        e = entry_for(((5, "maj"), (5, "min"), (0, "maj")))
        self.assertEqual(e["name"], "IV–iv–I")
        self.assertEqual(e["id"], "iv_ivm_i")
        # II7 and ii are already distinct in ascii: no m needed.
        e = entry_for(((2, "dom"), (2, "min"), (7, "dom")))
        self.assertEqual(e["id"], "ii7_ii_v7")


class TestMining(unittest.TestCase):
    def test_counts_catalog_filter_and_coverage(self):
        docs = [("a", doc(["C", "E7", "Am", "Dm7 G7", "C"])),
                ("b", doc(["F", "A7", "Dm"], key=("F", "major")))]
        stats = mine(docs, ns=(3,))
        catalog = load_catalog()

        cand = ((0, "maj"), (4, "dom"), (9, "min"))  # I III7 vi
        self.assertEqual(len(stats[cand]["tunes"]), 2)
        self.assertEqual(stats[cand]["cov"], 0)
        self.assertFalse(in_catalog(cand, catalog))

        # ii–V–I is cataloged, and doc a's occurrence lies inside the
        # cadence_251 block it produced.
        two51 = ((2, "min"), (7, "dom"), (0, "maj"))
        self.assertTrue(in_catalog(two51, catalog))
        self.assertEqual(stats[two51]["cov"], stats[two51]["occ"])
        # An entry's "any" quality is a wildcard (blues_tail V IV I:any).
        self.assertTrue(
            in_catalog(((7, "dom"), (5, "dom"), (0, "maj")), catalog))

        found = candidates(stats, catalog, min_tunes=2, max_covered=0.5)
        self.assertIn(cand, [toks for toks, _ in found])
        self.assertNotIn(two51, [toks for toks, _ in found])

    def test_seam_windows_are_dropped(self):
        catalog = load_catalog()
        # Opens on a V7–I resolution: the previous phrase's cadence tail.
        self.assertTrue(is_seam(((7, "dom"), (0, "maj"), (9, "min")), catalog))
        # cadence_251 padded by one leading chord.
        self.assertTrue(is_seam(
            ((9, "dom"), (2, "min"), (7, "dom"), (0, "maj")), catalog))
        # Contiguous stretch inside turnaround_1625's definition.
        self.assertTrue(is_seam(((9, "min"), (2, "min"), (7, "dom")), catalog))
        # A real block shape survives: I–ii–iii.
        self.assertFalse(is_seam(((0, "maj"), (2, "min"), (4, "min")), catalog))

    def test_reprints_break_windows(self):
        # C C | Am: the window over the reprint is skipped; the collapsed
        # C Am F sequence is counted from the reprint's last event.
        stats = mine([("a", doc(["C", "C", "Am", "F"]))], ns=(3,))
        self.assertNotIn(((0, "maj"), (0, "maj"), (9, "min")), stats)
        self.assertIn(((0, "maj"), (9, "min"), (5, "maj")), stats)


if __name__ == "__main__":
    unittest.main()
