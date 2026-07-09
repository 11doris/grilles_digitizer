"""Unit tests for the chord parser and grid expansion (run: python -m unittest)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from pipelines.chords.similarity.normalize import (
    Chord, compute_opening, degree_name, expand_tune, flatten, parse_chord,
    pitch_class,
)

_REPO = Path(__file__).resolve().parents[3]
_VERIFIED = _REPO / "data" / "chords" / "04_verified"


class TestParseChord(unittest.TestCase):
    def test_quality_classes(self):
        # one row per spec §4.1 class, plus the reclassification edge cases
        cases = {
            "F": "maj", "Fmaj7": "maj", "F6": "maj", "F6/9": "maj",
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


if __name__ == "__main__":
    unittest.main()
