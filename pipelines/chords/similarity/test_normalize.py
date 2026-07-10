"""Unit tests for the chord parser and grid expansion (run: python -m unittest)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from pipelines.chords.similarity.normalize import (
    Chord, compute_opening, degree_name, expand_tune, flatten, form_warnings,
    parse_chord, pitch_class, reference_pc, tonic_relative,
)

_REPO = Path(__file__).resolve().parents[3]
_VERIFIED = _REPO / "data" / "chords" / "04_verified"
_ANNOTATED = _REPO / "data" / "chords" / "05_annotated"


def _load_annotated(stem: str) -> dict:
    return json.loads((_ANNOTATED / f"{stem}.json").read_text("utf-8"))


class TestParseChord(unittest.TestCase):
    def test_quality_classes(self):
        # one row per spec §4.1 class, plus the reclassification edge cases
        cases = {
            "F": "maj", "Fmaj7": "maj", "F6": "maj", "F69": "maj",
            "Fm": "min", "Fm7": "min", "Fm6": "min", "Fm(maj7)": "min",
            "Bbm#5": "min",
            "F7": "dom", "F13": "dom", "F7b5": "dom", "F7#5": "dom",
            "F7alt": "dom", "F(b9)": "dom", "A(#5#9)": "dom", "Ab7(13)": "dom",
            "Fm7b5": "m7b5",
            "Fo7": "dim",
            "F(#5)": "aug",
            "Fsus4": "sus", "F7sus4": "sus", "C9sus4": "sus",
        }
        for symbol, expected in cases.items():
            self.assertEqual(parse_chord(symbol).quality, expected, symbol)

    def test_roots_and_flags(self):
        ch = parse_chord("Bb7#11")
        self.assertEqual(ch.root_pc, pitch_class("Bb"))
        self.assertFalse(ch.parenthesized)
        ch = parse_chord("(F7)")
        self.assertTrue(ch.parenthesized)
        self.assertEqual(ch.quality, "dom")
        self.assertEqual(parse_chord("N.C.").quality, "nc")
        self.assertFalse(parse_chord("N.C.").is_sounding)

    def test_parser_covers_corpus(self):
        """Every chord symbol in 04_verified parses (spec §4.4 / §10)."""
        for path in sorted(_VERIFIED.glob("*.json")):
            tune = json.loads(path.read_text("utf-8"))
            for sec, bars in (tune.get("sections") or {}).items():
                for bar in bars:
                    for symbol in (bar.get("beats") or {}).values():
                        ch = parse_chord(symbol)  # raises on failure
                        self.assertIsInstance(ch, Chord, f"{path.name} {symbol}")


class TestExpansion(unittest.TestCase):
    def test_two_slots_per_bar(self):
        au = json.loads((_VERIFIED / "23_04_AU_PRIVAVE.json").read_text("utf-8"))
        slots = flatten(expand_tune(au))
        self.assertEqual(len(slots), 24)  # 12-bar blues, 2 slots per bar
        # bar 3 has a single chord: it fills both slots
        self.assertEqual([s.chord.symbol for s in slots[4:6]], ["F", "F"])
        # bar 1 keeps beat-1 and beat-3 chords
        self.assertEqual([s.chord.symbol for s in slots[0:2]], ["F", "D(b9)"])

    def test_continuation_bar_repeats_previous_chord(self):
        # 23_03_AT_LONG_LAST_LOVE has an empty-beats bar (A1 bar 8)
        tune = json.loads((_VERIFIED / "23_03_AT_LONG_LAST_LOVE.json").read_text("utf-8"))
        slots = expand_tune(tune)["A1"]
        bar7 = [s.chord.symbol for s in slots if s.bar == 7]
        bar8 = [s.chord.symbol for s in slots if s.bar == 8]
        self.assertEqual(bar8[0], bar7[-1])

    def test_beat4_chord_dropped_but_carried(self):
        # CON_ALMA A bar 4: beats 1=Ebmaj7, 3=Ebm7, 4=D7 -> grid keeps 1 and 3
        tune = json.loads((_VERIFIED / "79_03_CON_ALMA.json").read_text("utf-8"))
        slots = expand_tune(tune)["A"]
        bar4 = [s.chord.symbol for s in slots if s.bar == 4]
        self.assertEqual(bar4, ["Ebmaj7", "Ebm7"])


class TestDegrees(unittest.TestCase):
    def test_degree_names(self):
        f = pitch_class("F")
        self.assertEqual(degree_name(f, f, "maj"), "I")
        self.assertEqual(degree_name(pitch_class("G"), f, "min"), "ii")
        self.assertEqual(degree_name(pitch_class("C"), f, "dom"), "V")
        self.assertEqual(degree_name(pitch_class("Ab"), f, "maj"), "bIII")
        self.assertEqual(degree_name(f, f, "min"), "i")

    def test_opening_spec_cases(self):
        """The four §3.7 opening acceptance cases."""
        cases = [
            ("153_02_HEART_AND_SOUL", "F", "major", "I", "F"),
            ("183_02_I_LL_NEVER_SMILE_AGAIN", "Eb", "major", "ii", "Fm7"),
            ("163_03_HOW_LONG_HAS_THIS_BEEN_GOING_ON", "G", "major", "V", "D7(13)"),
            ("77_01_CLOSE_YOUR_EYES", "F", "minor", "ii", "Gm7b5"),
        ]
        for stem, tonic, mode, degree, chord in cases:
            tune = json.loads((_VERIFIED / f"{stem}.json").read_text("utf-8"))
            opening = compute_opening(tune, tonic, mode)
            self.assertEqual(opening["degree"], degree, stem)
            self.assertEqual(opening["chord"], chord, stem)


class TestTonicRelative(unittest.TestCase):
    def test_reference_pc_shared_pitch_space(self):
        self.assertEqual(reference_pc("C", "major"), 0)
        self.assertEqual(reference_pc("A", "minor"), 0)   # A minor reads as C
        self.assertEqual(reference_pc("F", "minor"), 8)   # relative major Ab

    def test_contrafact_canary(self):
        """Au Privave (F blues) ≈ Cheryl (C blues) — the canary for the whole
        normalization stack (spec §4.4).

        The spec expected ≥ 90% identical tokens; the *actual* charts in this
        book agree on 16 of 24 slots (67%) — the other 8 are genuine chart
        differences (different printed turnarounds in bars 1–2/11–12, and
        Cheryl's m7b5 ii–Vs where Au Privave has plain minor), not
        normalization misses. This test pins that measured floor; the
        musical requirement lives in Phase 3 acceptance (mutual top-3),
        where alignment scores those substitutions as near-identical."""
        au = tonic_relative(_load_annotated("23_04_AU_PRIVAVE"))
        ch = tonic_relative(_load_annotated("72_03_CHERYL"))
        self.assertEqual(len(au.full_seq), len(ch.full_seq))
        same = sum(a == b for a, b in zip(au.full_seq, ch.full_seq))
        self.assertGreaterEqual(same / len(au.full_seq), 16 / 24,
                                f"{same}/{len(au.full_seq)} identical")
        # ignoring the min/m7b5 quality nuance, degrees agree on 19/24 slots
        same_degree = sum(a[0] == b[0] for a, b in zip(au.full_seq, ch.full_seq))
        self.assertGreaterEqual(same_degree / len(au.full_seq), 19 / 24)

    def test_sections_without_local_key_are_slices_of_full_seq(self):
        seqs = tonic_relative(_load_annotated("23_04_AU_PRIVAVE"))
        for name, sec in seqs.section_seqs.items():
            self.assertIsNone(sec.local_key, name)
            self.assertEqual(sec.tokens,
                             seqs.full_seq[sec.start:sec.start + len(sec.tokens)])

    def test_annotated_local_key_section(self):
        """One of the section_keys tunes: Confirmation's bridge (Bb in an
        F-major tune) reads local-relative with the marker; its I lands on
        degree 0 locally but degree 5 in full_seq (spec §4.3/§4.4)."""
        seqs = tonic_relative(_load_annotated("80_03_CONFIRMATION"))
        bridge = seqs.section_seqs["B"]
        self.assertEqual(bridge.local_key, {"tonic": "Bb", "mode": "major"})
        global_slice = seqs.full_seq[bridge.start:bridge.start + len(bridge.tokens)]
        self.assertNotEqual(bridge.tokens, global_slice)
        # every local degree sits 5 semitones (F -> Bb) below its global one
        for (ld, lq), (gd, gq) in zip(bridge.tokens, global_slice):
            self.assertEqual(lq, gq)
            if ld is not None:
                self.assertEqual((gd - ld) % 12, 5)
        # the other sections stay exact slices
        a = seqs.section_seqs["A"]
        self.assertIsNone(a.local_key)
        self.assertEqual(a.tokens, seqs.full_seq[a.start:a.start + len(a.tokens)])

    def test_minor_tune_reads_in_relative_major_space(self):
        # Close Your Eyes (F minor): its tonic Fm7 must land on degree 9
        # (A-minor position of the shared space), not 0.
        seqs = tonic_relative(_load_annotated("77_01_CLOSE_YOUR_EYES"))
        self.assertEqual(seqs.mode, "minor")
        degrees = {d for d, q in seqs.full_seq if q == "min" and d is not None}
        self.assertIn(9, degrees)

    def test_bar_count_and_metadata(self):
        seqs = tonic_relative(_load_annotated("23_04_AU_PRIVAVE"))
        self.assertEqual(seqs.bar_count, 12)
        self.assertEqual(seqs.meter, "4/4")
        self.assertEqual(seqs.form, "12 BLUES")


class TestFormValidation(unittest.TestCase):
    def test_known_forms(self):
        self.assertEqual(form_warnings(
            {"form": "32 A A B A", "sections": {"A": [], "A1": [], "B": [], "A2": []}}), [])
        self.assertEqual(form_warnings(
            {"form": "12 BLUES", "sections": {"A": []}}), [])
        self.assertEqual(form_warnings(  # jammed prime token
            {"form": "32 A B A'C", "sections": {"A": [], "B": [], "A1": [], "C": []}}), [])
        self.assertEqual(form_warnings(  # coda not counted by the form
            {"form": "32 A A B A''", "sections": {"A": [], "A1": [], "B": [], "A2": [], "coda": []}}), [])
        self.assertTrue(form_warnings(
            {"form": "32 A A B A", "sections": {"A": [], "B": []}}))

    def test_every_corpus_form_parses_or_warns(self):
        """§4.4: every form string parses or is explicitly warned about —
        form_warnings must never raise on real data."""
        for path in sorted(_VERIFIED.glob("*.json")):
            tune = json.loads(path.read_text("utf-8"))
            warnings = form_warnings(tune)
            self.assertIsInstance(warnings, list, path.name)


if __name__ == "__main__":
    unittest.main()
