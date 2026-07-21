"""Tests for the structural self-check (spec §17), currently check 14 only:
variant targets must name a verbatim sections key, anchor within it, and not
run off the end of the chart (they may cross a section boundary — see §13.2 and
the variant_spills double-check guard). Added after 319_03_PANAMA shipped a
bare-label target ("A" on a multi-strain piece whose keys are s1_A, s1_A1, …),
which no downstream consumer can resolve.

Run from the repo root:
    python -m unittest pipelines.chords.digitizer.test_validation
"""

import unittest

from pipelines.chords.digitizer.validation import (
    ValidationError, _check_variant_targets, variant_spills)


def _bars(n):
    return [{"bar": i + 1, "beats": {"1": "C7"}} for i in range(n)]


def _tune(target, n_boxes=2):
    return {
        "sections": {"s1_A": _bars(8), "s1_A1": _bars(8), "coda": _bars(4)},
        "variants": [{"applies_to": "Bar 13",
                      "targets": [target],
                      "bars": _bars(n_boxes)}],
    }


class TestVariantTargets(unittest.TestCase):
    def test_verbatim_key_accepted(self):
        _check_variant_targets(_tune({"section": "s1_A1", "bar": 5}))

    def test_bare_label_rejected(self):
        # The PANAMA failure: bare printed label instead of the strain key.
        with self.assertRaisesRegex(ValidationError, "'A' is not a sections"):
            _check_variant_targets(_tune({"section": "A", "bar": 5}))

    def test_bar_out_of_range_rejected(self):
        with self.assertRaisesRegex(ValidationError, "outside section"):
            _check_variant_targets(_tune({"section": "s1_A", "bar": 9}))
        with self.assertRaisesRegex(ValidationError, "outside section"):
            _check_variant_targets(_tune({"section": "s1_A", "bar": 0}))

    def test_spill_into_next_section_allowed(self):
        # A 2-box alternate anchored at s1_A bar 8 runs one box into s1_A1.
        # Legal now (there is a following section) — the transcriber's
        # double-check guard confirms the reading; validation must not reject it.
        _check_variant_targets(_tune({"section": "s1_A", "bar": 8}, n_boxes=2))

    def test_spill_past_chart_end_rejected(self):
        # coda is the last section (4 bars); 2 boxes at bar 4 run off the end.
        with self.assertRaisesRegex(ValidationError, "past the end of the chart"):
            _check_variant_targets(_tune({"section": "coda", "bar": 4}, n_boxes=2))

    def test_variant_spills_detects_cross_section(self):
        # The detector reports a spill (drives the one-shot re-verify)...
        spills = variant_spills(_tune({"section": "s1_A", "bar": 8}, n_boxes=2))
        self.assertEqual(len(spills), 1)
        self.assertIn("s1_A bar 8", spills[0])
        # ...but stays quiet when every box fits inside its anchor section.
        self.assertEqual(
            variant_spills(_tune({"section": "s1_A", "bar": 5}, n_boxes=2)), [])

    def test_missing_targets_rejected(self):
        tune = _tune({"section": "s1_A", "bar": 1})
        tune["variants"][0]["targets"] = []
        with self.assertRaisesRegex(ValidationError, "no targets"):
            _check_variant_targets(tune)

    def test_no_variants_is_fine(self):
        _check_variant_targets({"sections": {"A": _bars(8)}})


if __name__ == "__main__":
    unittest.main()
