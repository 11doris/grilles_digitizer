"""Engine unit + regression tests (spec §6 / Phase 3 acceptance).

The output-based tests read data/chords/06_similarity and are skipped when
the engine has not been run yet (`python -m pipelines.chords.similarity.compute`).
"""
from __future__ import annotations

import json
import unittest

import numpy as np

from pipelines.chords.similarity import corpus
from pipelines.chords.similarity.compute import ngram_matrix, top_candidates

_SIM = corpus.SIMILARITY_DIR


class TestRetrieval(unittest.TestCase):
    def test_identical_sequences_have_cosine_one(self):
        a = ((0, "maj"), (2, "min"), (7, "dom"), (0, "maj"), (5, "maj"), (0, "maj"))
        b = ((4, "min"), (9, "dom"), (2, "min"), (7, "dom"), (1, "dim"), (0, "maj"))
        mat = ngram_matrix([a, a, b])
        cands = top_candidates(mat, 2)
        best_id, best_cos = cands[0][0]
        self.assertEqual(best_id, 1)
        self.assertAlmostEqual(best_cos, 1.0, places=6)

    def test_disjoint_sequences_share_nothing(self):
        a = tuple((i % 12, "maj") for i in range(8))
        b = tuple((i % 12, "dim") for i in range(8))
        mat = ngram_matrix([a, b])
        self.assertAlmostEqual((mat @ mat.T)[0, 1], 0.0, places=9)


@unittest.skipUnless((_SIM / "tunes").exists(), "similarity output not built")
class TestPhase3Acceptance(unittest.TestCase):
    def _similar_ids(self, stem):
        doc = json.loads((_SIM / "tunes" / f"{stem}.json").read_text("utf-8"))
        return [s["id"] for s in doc["similar"]]

    def test_au_privave_cheryl_mutual_top3(self):
        """Phase 3 acceptance (spec §9)."""
        self.assertIn("72_03_CHERYL",
                      self._similar_ids("23_04_AU_PRIVAVE")[:3])
        self.assertIn("23_04_AU_PRIVAVE",
                      self._similar_ids("72_03_CHERYL")[:3])

    def test_scores_are_spectrum_values(self):
        for path in sorted((_SIM / "tunes").glob("*.json"))[:10]:
            doc = json.loads(path.read_text("utf-8"))
            for s in doc["similar"] + doc["section_matches"]:
                self.assertGreaterEqual(s["score"], 0.0, path.name)
                self.assertLessEqual(s["score"], 1.0, path.name)
                self.assertTrue(s["bars"], path.name)  # traceback present


if __name__ == "__main__":
    unittest.main()
