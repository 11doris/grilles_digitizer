"""Tests for the structural self-check (spec §17), currently check 14 only:
variant targets must name a verbatim sections key and fit inside it. Added
after 319_03_PANAMA shipped a bare-label target ("A" on a multi-strain piece
whose keys are s1_A, s1_A1, …), which no downstream consumer can resolve.

Run from the repo root:
    python -m unittest pipelines.chords.digitizer.test_validation
"""

import unittest

from pipelines.chords.digitizer.validation import (
    ValidationError, _check_variant_targets)


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

    def test_spill_past_section_end_rejected(self):
        with self.assertRaisesRegex(ValidationError, "spills past"):
            _check_variant_targets(
                _tune({"section": "s1_A", "bar": 8}, n_boxes=2))

    def test_missing_targets_rejected(self):
        tune = _tune({"section": "s1_A", "bar": 1})
        tune["variants"][0]["targets"] = []
        with self.assertRaisesRegex(ValidationError, "no targets"):
            _check_variant_targets(tune)

    def test_no_variants_is_fine(self):
        _check_variant_targets({"sections": {"A": _bars(8)}})


if __name__ == "__main__":
    unittest.main()
