"""Tests for the house-dialect ABC parser and validator (plan §5).

Run from the repo root:
    python -m unittest pipelines.melody.digitizer.test_validation
"""

import unittest
from fractions import Fraction

from pipelines.melody.digitizer.validation import (
    parse_tune, validate_tune, section_bar_counts, effective_pickup)

HEADER = "X:1\nT:T\nM:4/4\nL:1/8\nK:F\n"


def tune(body: str) -> str:
    return HEADER + body


class TestParsing(unittest.TestCase):
    def test_octave_from_case_and_marks(self):
        t = parse_tune(tune('C c C, c\' | z8 |'))
        n = t.all_bars[0].notes
        self.assertEqual([x.octave for x in n[:4]], [4, 5, 3, 6])

    def test_beaming_from_adjacency(self):
        t = parse_tune(tune('GABc G A B c | z8 |'))
        beamed = [x.beamed_prev for x in t.all_bars[0].notes]
        # first group adjacent -> beamed_prev True after the first;
        # second group space-separated -> all False
        self.assertEqual(beamed, [False, True, True, True, False, False, False, False])

    def test_eighth_triplet_duration(self):
        t = parse_tune(tune('(3B2c2B2 (3G2B2G2 | z8 |'))
        self.assertEqual(t.all_bars[0].units, Fraction(8))

    def test_quarter_triplet_duration(self):
        t = parse_tune(tune('(3ABc z2 z2 | z8 |'))
        # (3ABc = three eighths as a triplet = 2 units, + z2 + z2 = 6? no:
        # (3ABc has 3 notes each length 1 -> 2 units total
        self.assertEqual(t.all_bars[0].notes[0].units, Fraction(2, 3))


class TestBarSums(unittest.TestCase):
    def test_good_sums_pass(self):
        _, rep = validate_tune(tune('c8 | c4 c4 | z8 |'))
        self.assertTrue(rep.ok, [str(e) for e in rep.errors])

    def test_short_bar_flagged(self):
        # short bar in mid-tune (not the anacrusis) must fail the sum check
        _, rep = validate_tune(tune('"^A" c8 | (3BcB (3GBG | c8 |'))
        self.assertTrue(any(e.code == "barsum" for e in rep.errors))

    def test_pickup_before_label_ok(self):
        _, rep = validate_tune(tune('c3 G ||"^A" c8 | c8 |'))
        self.assertTrue(rep.ok, [str(e) for e in rep.errors])

    def test_pickup_inside_first_section(self):
        # COTTAGE style: short first bar right after the label
        t = parse_tune(tune('"^A" B | c8 | c8 |'))
        self.assertIsNotNone(effective_pickup(t))
        self.assertEqual(section_bar_counts(t), [("A", 2)])


class TestTies(unittest.TestCase):
    def test_tie_same_pitch_ok(self):
        _, rep = validate_tune(tune('c4- c4 | z8 |'))
        self.assertTrue(rep.ok, [str(e) for e in rep.errors])

    def test_tie_different_pitch_fails(self):
        _, rep = validate_tune(tune('c4- d4 | z8 |'))
        self.assertTrue(any(e.code == "tie" for e in rep.errors))

    def test_accidental_carries_over_tie(self):
        # house rule: tie target written plain, same letter/octave -> ok
        _, rep = validate_tune(tune('_B8- | B8 |'))
        self.assertTrue(rep.ok, [str(e) for e in rep.errors])


class TestSlurs(unittest.TestCase):
    def test_slur_parses_and_sums(self):
        _, rep = validate_tune(tune('(_d8) | c4 c4 | z8 |'))
        self.assertTrue(rep.ok, [str(e) for e in rep.errors])

    def test_slur_across_bars(self):
        _, rep = validate_tune(tune('(F2B2) (E2A2) | z8 |'))
        self.assertTrue(rep.ok, [str(e) for e in rep.errors])

    def test_unclosed_slur_fails(self):
        _, rep = validate_tune(tune('(c8 | c8 |'))
        self.assertFalse(rep.ok)


class TestPlan(unittest.TestCase):
    def test_section_count_mismatch(self):
        _, rep = validate_tune(
            tune('"^A" c8 | c8 |'), plan=[("A", 1), ("B", 1)])
        self.assertTrue(any(e.code == "sections" for e in rep.errors))

    def test_bar_count_mismatch(self):
        _, rep = validate_tune(
            tune('"^A" c8 | c8 |'), plan=[("A", 3)])
        self.assertTrue(any(e.code == "barcount" for e in rep.errors))


class TestBeamingWarning(unittest.TestCase):
    def test_flat_spacing_warned(self):
        # every eighth separated -> v2-style flat spacing
        _, rep = validate_tune(tune('c c c c c c c c | z8 |'))
        self.assertTrue(any(w.code == "beaming" for w in rep.warnings))

    def test_beamed_groups_not_warned(self):
        _, rep = validate_tune(tune('cccc cccc | z8 |'))
        self.assertFalse(any(w.code == "beaming" for w in rep.warnings))


if __name__ == "__main__":
    unittest.main()
