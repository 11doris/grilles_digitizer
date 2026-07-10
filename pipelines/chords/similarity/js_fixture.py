#!/usr/bin/env python3
"""Generate the shared JS/Python chord fixture (tune_similarity_spec §8.3).

The displayer's chords.js ports the §4.1 quality reduction, the degree
naming and the transposition tables. This script derives the expected
values from the Python library for every distinct chord symbol in the
annotated corpus and writes apps/displayer/tests/chords_fixture.json;
`node apps/displayer/tests/test_chords.mjs` asserts the JS side matches,
so the two implementations cannot drift silently.

    python -m pipelines.chords.similarity.js_fixture
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

from pipelines.chords.similarity import corpus  # noqa: E402
from pipelines.chords.similarity.normalize import (  # noqa: E402
    degree_name, parse_chord, pitch_class,
)

OUT = _REPO / "apps" / "displayer" / "tests" / "chords_fixture.json"

# The displayer's spelling tables (chords.js FLAT_SPELL / SHARP_SPELL).
FLAT_SPELL = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
SHARP_SPELL = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def transpose_symbol(raw: str, shift: int, spell: list[str]) -> str:
    """Python mirror of chords.js transposeChordSymbol: rewrite the root and
    the slash bass, keep the rest of the printed symbol verbatim."""
    s = str(raw)
    if not shift:
        return s
    m = re.match(r"^(\(?)([A-G])(#|b)?", s)
    if not m:
        return s
    pc = (pitch_class(m.group(2) + (m.group(3) or "")) + shift) % 12
    s = m.group(1) + spell[pc] + s[m.end():]

    def bass(mm: re.Match) -> str:
        bpc = (pitch_class(mm.group(1) + (mm.group(2) or "")) + shift) % 12
        return "/" + spell[bpc]

    return re.sub(r"/([A-G])(#|b)?(?=\)?\??$)", bass, s)


def corpus_symbols() -> list[str]:
    symbols: set[str] = set()
    for doc in corpus.load_corpus().values():
        for bars in (doc.get("sections") or {}).values():
            for bar in bars:
                symbols.update((bar.get("beats") or {}).values())
        for variant in doc.get("variants") or []:
            for bar in variant.get("bars") or []:
                symbols.update((bar.get("beats") or {}).values())
    return sorted(symbols)


def main() -> int:
    cases = []
    for symbol in corpus_symbols():
        ch = parse_chord(symbol)
        cases.append({
            "symbol": symbol,
            # chords.js chordClass returns null for N.C.
            "quality": None if ch.quality == "nc" else ch.quality,
            "degree_from_c": (degree_name(ch.root_pc, 0, ch.quality)
                              if ch.is_sounding else None),
            "up3_flat": transpose_symbol(symbol, 3, FLAT_SPELL),
            "up7_sharp": transpose_symbol(symbol, 7, SHARP_SPELL),
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"cases": cases}, indent=1, ensure_ascii=False)
                   + "\n", "utf-8")
    print(f"{len(cases)} symbols -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
