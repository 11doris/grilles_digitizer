"""Scorer hard cases (spec §3.2/§3.7) and update-routine tests.

This file is the Phase 0 regression set: rerun it whenever scorer weights
change (run: python -m unittest).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from pipelines.chords.key_annotation import core
from pipelines.chords.key_annotation.llm import LLMVoteError
from pipelines.chords.key_annotation.scorer import (
    TUNE_MARGIN_THRESHOLD, score_tune,
)

_REPO = Path(__file__).resolve().parents[3]
_VERIFIED = _REPO / "data" / "chords" / "04_verified"


def _load(stem: str) -> dict:
    return json.loads((_VERIFIED / f"{stem}.json").read_text("utf-8"))


class TestScorerHardCases(unittest.TestCase):
    """The named §3.2 hard cases, all present in the current corpus."""

    def assert_key(self, stem, tonic, mode, confident=True):
        vote = score_tune(_load(stem))
        self.assertEqual((vote.tonic, vote.mode), (tonic, mode), stem)
        if confident:
            self.assertGreaterEqual(vote.margin, TUNE_MARGIN_THRESHOLD, stem)

    def test_turnaround_ending_on_v7(self):
        self.assert_key("23_04_AU_PRIVAVE", "F", "major")   # ends on C7
        self.assert_key("169_03_IDAHO", "F", "major")       # ends on C7

    def test_turnaround_ending_on_ii(self):
        self.assert_key("72_03_CHERYL", "C", "major")       # ends Em7 Dm7

    def test_picardy_third_final_chord(self):
        # ends on "(F)" but is F minor
        self.assert_key("77_01_CLOSE_YOUR_EYES", "F", "minor")

    def test_genuinely_modulating_tune(self):
        # Con Alma: the chart opens in E (the spec's reading) but its A
        # sections resolve to Cmaj7 and the owner verified the key as
        # C major — both are defensible tonal centers of this chromatic
        # chart. The scorer must at least not confidently pick anything
        # *else* (the human/LLM path settles which of the two it is).
        vote = score_tune(_load("79_03_CON_ALMA"))
        if vote.margin >= TUNE_MARGIN_THRESHOLD:
            self.assertIn((vote.tonic, vote.mode),
                          [("E", "major"), ("C", "major")])

    def test_blues_head_with_dominant_tonic(self):
        self.assert_key("101_01_DIRTY_DOZENS", "F", "major")


class TestSectionPass(unittest.TestCase):
    def test_dominant_cycle_bridge_is_not_a_modulation(self):
        # I Got Rhythm's bridge (D7 G7 C7 F7 in Bb) never arrives anywhere.
        vote = score_tune(_load("178_01_I_GOT_RHYTHM"))
        self.assertEqual(vote.section_keys, {})

    def test_blues_has_no_section_keys(self):
        for stem in ("23_04_AU_PRIVAVE", "72_03_CHERYL"):
            self.assertEqual(score_tune(_load(stem)).section_keys, {}, stem)


class TestAdjudicationAndUpdate(unittest.TestCase):
    def _fake_llm(self, tonic="F", mode="major", sections=("BLUES",), local=None):
        return {
            "tonic": tonic, "mode": mode, "confidence": "high",
            "modulation_note": None,
            "fingerprint": {
                "family": "12-bar blues", "tags": ["blues-form"],
                "sections": [{"name": n, "summary": "blues chorus",
                              "local_key": (local or {}).get(n)}
                             for n in sections],
                "modulates": False,
            },
        }

    def _annotate_au_privave(self, llm):
        source = _load("23_04_AU_PRIVAVE")
        scorer = score_tune(source)
        return core.build_annotation(source, "deadbeef", scorer, llm)

    def test_agreement(self):
        ann = self._annotate_au_privave(self._fake_llm())
        self.assertEqual(ann["key_annotation"]["status"], "agreed")
        self.assertEqual(ann["key"], {"tonic": "F", "mode": "major"})
        self.assertEqual(ann["opening"]["degree"], "I")
        self.assertNotIn("section_keys", ann)
        self.assertEqual(ann["harmonic_fingerprint"]["sections"],
                         {"BLUES": "blues chorus"})

    def test_disagreement_goes_to_review(self):
        ann = self._annotate_au_privave(self._fake_llm(tonic="Bb"))
        self.assertEqual(ann["key_annotation"]["status"], "needs_review")
        self.assertTrue(any("disagreement" in r for r in
                            ann["key_annotation"]["review_reasons"]))

    def test_section_key_disagreement_goes_to_review(self):
        llm = self._fake_llm(local={"BLUES": {"tonic": "Bb", "mode": "major"}})
        ann = self._annotate_au_privave(llm)
        self.assertEqual(ann["key_annotation"]["status"], "needs_review")
        self.assertTrue(any("section 'BLUES'" in r for r in
                            ann["key_annotation"]["review_reasons"]))

    def test_llm_failure_goes_to_review(self):
        ann = self._annotate_au_privave(LLMVoteError("boom"))
        self.assertEqual(ann["key_annotation"]["status"], "needs_review")
        self.assertEqual(ann["key_annotation"]["llm"], {"error": "boom"})
        # No LLM prose without a vote — but the derived tags still exist
        # (the displayer filters on them, LLM or not).
        self.assertEqual(list(ann["harmonic_fingerprint"]), ["tags"])
        self.assertIn("blues-form", ann["harmonic_fingerprint"]["tags"])

    def test_update_corrects_key_and_recomputes_opening(self):
        ann = self._annotate_au_privave(self._fake_llm())
        scorer_before = json.dumps(ann["key_annotation"]["scorer"])
        llm_before = json.dumps(ann["key_annotation"]["llm"])
        core.update_annotation(ann, tonic="Bb", mode="major")
        self.assertEqual(ann["key"], {"tonic": "Bb", "mode": "major"})
        self.assertEqual(ann["opening"]["degree"], "V")  # F over a Bb tonic
        self.assertEqual(ann["key_annotation"]["status"], "verified")
        self.assertEqual(ann["key_annotation"]["human"],
                         {"tonic": "Bb", "mode": "major", "corrected": True})
        # voter votes stay untouched for the record (spec §3.1)
        self.assertEqual(json.dumps(ann["key_annotation"]["scorer"]), scorer_before)
        self.assertEqual(json.dumps(ann["key_annotation"]["llm"]), llm_before)

    def test_update_plain_verify_is_not_a_correction(self):
        ann = self._annotate_au_privave(self._fake_llm())
        core.update_annotation(ann)
        self.assertEqual(ann["key_annotation"]["human"]["corrected"], False)
        self.assertEqual(ann["key_annotation"]["status"], "verified")

    def test_tags_are_derived_never_typed(self):
        ann = self._annotate_au_privave(self._fake_llm())
        self.assertIn("ii-V-chains", ann["harmonic_fingerprint"]["tags"])
        core.update_annotation(ann, fingerprint={"tags": ["hand-typed"]})
        tags = ann["harmonic_fingerprint"]["tags"]
        self.assertNotIn("hand-typed", tags)
        self.assertIn("blues-form", tags)

    def test_update_drops_section_key_equal_to_new_global(self):
        ann = self._annotate_au_privave(self._fake_llm())
        core.update_annotation(
            ann, section_keys={"BLUES": {"tonic": "F", "mode": "major"}})
        self.assertNotIn("section_keys", ann)  # equal to global key -> dropped

    # --- §3.5 staleness handling -------------------------------------------

    def test_key_correction_flags_fingerprint_stale(self):
        ann = self._annotate_au_privave(self._fake_llm())
        core.update_annotation(ann, tonic="Bb", mode="major")
        self.assertTrue(ann["harmonic_fingerprint"]["stale"])

    def test_plain_verify_does_not_flag_stale(self):
        ann = self._annotate_au_privave(self._fake_llm())
        core.update_annotation(ann)
        self.assertNotIn("stale", ann["harmonic_fingerprint"])

    def test_fingerprint_edit_in_same_save_clears_stale(self):
        ann = self._annotate_au_privave(self._fake_llm())
        core.update_annotation(
            ann, tonic="Bb", mode="major",
            fingerprint={"family": "12-bar blues", "tags": ["blues-form"],
                         "sections": {"BLUES": "Bb blues chorus"}})
        self.assertNotIn("stale", ann["harmonic_fingerprint"])

    def test_key_change_proposes_rescanned_section_keys(self):
        # Forcing Au Privave to B major makes its A section read as F major
        # locally; that must surface as a proposal, never as section_keys.
        ann = self._annotate_au_privave(self._fake_llm())
        core.update_annotation(ann, tonic="B", mode="major")
        proposals = ann["key_annotation"]["section_key_proposals"]
        self.assertEqual(proposals["BLUES"]["tonic"], "F")
        self.assertEqual(proposals["BLUES"]["mode"], "major")
        self.assertNotIn("section_keys", ann)

    def test_next_save_clears_pending_proposals(self):
        ann = self._annotate_au_privave(self._fake_llm())
        core.update_annotation(ann, tonic="B", mode="major")
        self.assertIn("section_key_proposals", ann["key_annotation"])
        core.update_annotation(ann)  # human dismisses (verify without changes)
        self.assertNotIn("section_key_proposals", ann["key_annotation"])

    def test_accepting_a_proposal_via_section_keys(self):
        ann = self._annotate_au_privave(self._fake_llm())
        core.update_annotation(ann, tonic="B", mode="major")
        core.update_annotation(
            ann, section_keys={"BLUES": {"tonic": "F", "mode": "major"}})
        self.assertEqual(ann["section_keys"]["BLUES"],
                         {"tonic": "F", "mode": "major"})
        self.assertNotIn("section_key_proposals", ann["key_annotation"])


class TestIdempotence(unittest.TestCase):
    def test_pending_detection(self):
        import tempfile
        src = _VERIFIED / "23_04_AU_PRIVAVE.json"
        with tempfile.TemporaryDirectory() as td:
            ann_path = Path(td) / src.name
            self.assertTrue(core.is_pending(src, ann_path))  # missing

            source = core.read_json(src)
            scorer = score_tune(source)
            fake_llm = {"tonic": "F", "mode": "major", "confidence": "high",
                        "modulation_note": None,
                        "fingerprint": {"family": "12-bar blues", "tags": [],
                                        "sections": [{"name": "A", "summary": "",
                                                      "local_key": None}],
                                        "modulates": False}}
            ann = core.build_annotation(source, core.source_sha256(src),
                                        scorer, fake_llm)
            core.write_annotated(ann_path, ann)
            self.assertFalse(core.is_pending(src, ann_path))  # hash matches

            ann["key_annotation"]["source_sha256"] = "stale"
            core.write_annotated(ann_path, ann)
            self.assertTrue(core.is_pending(src, ann_path))  # source changed

    def test_llm_failure_is_retried_unless_verified(self):
        import tempfile
        src = _VERIFIED / "23_04_AU_PRIVAVE.json"
        with tempfile.TemporaryDirectory() as td:
            ann_path = Path(td) / src.name
            source = core.read_json(src)
            ann = core.build_annotation(source, core.source_sha256(src),
                                        score_tune(source), LLMVoteError("boom"))
            core.write_annotated(ann_path, ann)
            self.assertTrue(core.is_pending(src, ann_path))  # retry the LLM

            core.update_annotation(ann)  # human verifies despite LLM failure
            core.write_annotated(ann_path, ann)
            self.assertFalse(core.is_pending(src, ann_path))


class TestCarryAnnotation(unittest.TestCase):
    """--reuse-annotation: rebuild 05 from an edited 04 without re-voting."""

    def _annotated(self):
        source = _load("23_04_AU_PRIVAVE")
        llm = {"tonic": "F", "mode": "major", "confidence": "high",
               "modulation_note": None,
               "fingerprint": {"family": "bebop blues", "tags": ["blues-form"],
                               "sections": [{"name": "A", "summary": "blues",
                                             "local_key": None}],
                               "modulates": False}}
        return source, core.build_annotation(
            source, "oldsha", score_tune(source), llm)

    def test_source_fields_flow_through_annotation_reused(self):
        source, old = self._annotated()
        # An edit made in 04_verified — a new source field and a changed one.
        edited = dict(source)
        edited["form"] = "12 BLUES"
        edited["section_labels"] = {"A": "BLUES"}

        carried = core.carry_annotation(edited, old, "newsha")

        # edited source content wins
        self.assertEqual(carried["form"], "12 BLUES")
        self.assertEqual(carried["section_labels"], {"A": "BLUES"})
        # key decision carried verbatim, only the hash advances
        self.assertEqual(carried["key"], old["key"])
        self.assertEqual(carried["harmonic_fingerprint"],
                         old["harmonic_fingerprint"])
        self.assertEqual(carried["key_annotation"]["scorer"],
                         old["key_annotation"]["scorer"])
        self.assertEqual(carried["key_annotation"]["llm"],
                         old["key_annotation"]["llm"])
        self.assertEqual(carried["key_annotation"]["status"],
                         old["key_annotation"]["status"])
        self.assertEqual(carried["key_annotation"]["source_sha256"], "newsha")

    def test_carried_file_is_no_longer_pending(self):
        import tempfile
        source, old = self._annotated()
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "23_04_AU_PRIVAVE.json"
            core.write_annotated(src, source)  # stand-in for 04_verified
            ann_path = Path(td) / "ann.json"
            carried = core.carry_annotation(
                source, old, core.source_sha256(src))
            core.write_annotated(ann_path, carried)
            self.assertFalse(core.is_pending(src, ann_path))


if __name__ == "__main__":
    unittest.main()
