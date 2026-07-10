"""Alignment unit tests: the vectorized scorer and the traceback DP must
agree, and the music-aware substitutions behave per spec §6.3."""
from __future__ import annotations

import random
import unittest

import numpy as np

from pipelines.chords.similarity.align import (
    MATCH, RELATED, SUBSTITUTE, TokenTable, sw_score, sw_traceback,
    token_score,
)

QUALITIES = ["maj", "min", "dom", "m7b5", "dim", "aug", "sus"]


def random_seq(rng, n):
    return [(rng.randrange(12), rng.choice(QUALITIES)) for _ in range(n)]


class TestTokenScore(unittest.TestCase):
    def test_spec_table(self):
        self.assertEqual(token_score((0, "maj"), (0, "maj")), MATCH)
        self.assertEqual(token_score((0, "maj"), (0, "dom")), RELATED)   # blues I
        self.assertEqual(token_score((2, "min"), (2, "m7b5")), RELATED)  # ii shapes
        self.assertEqual(token_score((7, "dom"), (1, "dom")), SUBSTITUTE)  # tritone
        self.assertEqual(token_score((0, "maj"), (9, "min")), SUBSTITUTE)  # relative
        self.assertEqual(token_score((9, "min"), (0, "maj")), SUBSTITUTE)
        self.assertLess(token_score((0, "maj"), (5, "maj")), 0)  # same q, wrong deg
        self.assertLess(token_score((0, "maj"), (3, "dim")), 0)

    def test_nc(self):
        self.assertGreater(token_score((None, "nc"), (None, "nc")), 0)
        self.assertLess(token_score((None, "nc"), (0, "maj")), 0)


class TestAlignment(unittest.TestCase):
    def setUp(self):
        self.table = TokenTable()

    def _score_both(self, a, b):
        ea, eb = self.table.encode(a), self.table.encode(b)
        sub = self.table.matrix
        return sw_score(ea, eb, sub), sw_traceback(ea, eb, sub)

    def test_identical_sequences_score_self_alignment(self):
        seq = [(0, "maj"), (9, "min"), (2, "min"), (7, "dom")] * 4
        enc = self.table.encode(seq)
        fast, (slow, path) = self._score_both(seq, seq)
        self.assertAlmostEqual(fast, self.table.self_score(enc), places=4)
        self.assertAlmostEqual(slow, fast, places=4)
        self.assertEqual(path, [(i, i) for i in range(len(seq))])

    def test_gap_alignment(self):
        a = [(0, "maj"), (2, "min"), (7, "dom"), (0, "maj"), (5, "maj"), (0, "maj")]
        b = a[:3] + a[4:]  # one slot deleted
        fast, (slow, path) = self._score_both(a, b)
        self.assertAlmostEqual(fast, slow, places=4)
        # all five shared tokens still align
        self.assertEqual(len(path), 5)

    def test_fuzz_scorer_equals_traceback(self):
        rng = random.Random(42)
        for _ in range(50):
            a = random_seq(rng, rng.randrange(1, 40))
            b = random_seq(rng, rng.randrange(1, 40))
            fast, (slow, _) = self._score_both(a, b)
            self.assertTrue(np.isclose(fast, slow, atol=1e-3),
                            f"{fast} != {slow} for {a} / {b}")

    def test_local_alignment_finds_contained_motif(self):
        motif = [(2, "min"), (7, "dom"), (0, "maj"), (0, "maj")]
        noise = [(4, "dim"), (8, "aug"), (11, "m7b5"), (1, "sus")]
        long = noise + motif + noise
        fast, (slow, path) = self._score_both(motif, long)
        self.assertAlmostEqual(fast, 4 * MATCH, places=4)
        self.assertEqual(path, [(i, i + 4) for i in range(4)])


if __name__ == "__main__":
    unittest.main()
