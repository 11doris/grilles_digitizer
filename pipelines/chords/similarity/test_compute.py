"""Engine unit + regression tests (spec §6 / Phase 3 acceptance).

The output-based tests read data/chords/06_similarity and are skipped when
the engine has not been run yet (`python -m pipelines.chords.similarity.compute`).
"""
from __future__ import annotations

import json
import unittest

import numpy as np

from pipelines.chords.similarity import corpus
from pipelines.chords.similarity.compute import (
    _displayer_cut, _rescore_with_path, _section_base, build_entries,
    ngram_matrix, top_candidates,
)

_SIM = corpus.SIMILARITY_DIR


def _bars(chords: list[str]) -> list[dict]:
    return [{"bar": i + 1, "beats": {"1": c}} for i, c in enumerate(chords)]


class TestVerseExclusion(unittest.TestCase):
    """Verses never enter comparisons; bar mappings stay chart-accurate."""

    CHORUS = ["C", "Am7", "Dm7", "G7", "Em7", "A7", "Dm7", "G7"]

    def _docs(self):
        with_verse = {
            "key": {"tonic": "C", "mode": "major"}, "time_signature": "4/4",
            "form": "4 A | 8 A",
            "sections": {
                "verse_A": _bars(["Fm7", "Bb7", "Ebmaj7", "Ab7"]),
                "A": _bars(self.CHORUS),
            },
        }
        chorus_only = {
            "key": {"tonic": "C", "mode": "major"}, "time_signature": "4/4",
            "form": "8 A",
            "sections": {"A": _bars(self.CHORUS)},
        }
        return {"with_verse": with_verse, "chorus_only": chorus_only}

    def test_verse_dropped_from_both_levels(self):
        tunes, sections, _ = build_entries(self._docs())
        by_stem = {e.stem: e for e in tunes}
        self.assertEqual(by_stem["with_verse"].tokens,
                         by_stem["chorus_only"].tokens)
        self.assertNotIn("verse_A", {e.section for e in sections})
        # slot_map points at the chart slots after the 4-bar verse
        self.assertEqual(list(by_stem["with_verse"].slot_map[:2]), [8, 9])

    def test_bar_mapping_references_full_chart(self):
        tunes, _, table = build_entries(self._docs())
        by_stem = {e.stem: e for e in tunes}
        r = _rescore_with_path(by_stem["with_verse"], by_stem["chorus_only"],
                               1.0, table)
        self.assertEqual(r["score"], 1.0)
        # with_verse's chorus starts at chart bar 5 (after the 4-bar verse)
        self.assertEqual(r["bars"][0], [5, 1])
        self.assertEqual(r["bars"][-1], [12, 8])


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

    def test_displayer_cut(self):
        mk = lambda *scores: [{"score": s} for s in scores]
        # strong matches kept up to the cap
        self.assertEqual(len(_displayer_cut(mk(*[0.9] * 8), 10)), 8)
        self.assertEqual(len(_displayer_cut(mk(*[0.9] * 20), 10)), 10)
        # a weak tail is dropped once 5 clear the threshold
        out = _displayer_cut(mk(0.9, 0.8, 0.7, 0.6, 0.5, 0.2, 0.1), 10)
        self.assertEqual([m["score"] for m in out], [0.9, 0.8, 0.7, 0.6, 0.5])
        # fewer than 5 above 0.25: the top 5 stay whatever they score
        out = _displayer_cut(mk(0.3, 0.2, 0.15, 0.1, 0.05, 0.01), 10)
        self.assertEqual([m["score"] for m in out], [0.3, 0.2, 0.15, 0.1, 0.05])

    def test_bundle_respects_min_score_cut(self):
        bundle = json.loads(
            (_SIM / "displayer_similar.json").read_text("utf-8"))
        for stem, data in bundle.items():
            for lst in (data["similar"], data["sections"]):
                self.assertLessEqual(len(lst), 10, stem)
                for m in lst[5:]:  # beyond the always-keep-5 window
                    self.assertGreaterEqual(m["score"], 0.25, stem)

    def test_section_base(self):
        for name, base in (("A", "A"), ("A1", "A"), ("A2", "A"), ("A'", "A"),
                           ("B1", "B"), ("verse_A1", "verse_A")):
            self.assertEqual(_section_base(name), base)

    def test_displayer_sections_dedupe_repeats(self):
        """One row per tune-pair section relationship: an AABA matching
        another AABA must not list its A x A cross product (I Got Rhythm vs
        Rhythm In My Nursery Rhymes was 5 identical rows)."""
        bundle = json.loads(
            (_SIM / "displayer_similar.json").read_text("utf-8"))
        for stem, data in bundle.items():
            keys = [(_section_base(m["section"]), m["other"],
                     _section_base(m["other_section"]))
                    for m in data["sections"]]
            self.assertEqual(len(keys), len(set(keys)), stem)

    def test_scores_are_spectrum_values(self):
        for path in sorted((_SIM / "tunes").glob("*.json"))[:10]:
            doc = json.loads(path.read_text("utf-8"))
            for s in doc["similar"] + doc["section_matches"]:
                self.assertGreaterEqual(s["score"], 0.0, path.name)
                self.assertLessEqual(s["score"], 1.0, path.name)
                self.assertTrue(s["bars"], path.name)  # traceback present


if __name__ == "__main__":
    unittest.main()
