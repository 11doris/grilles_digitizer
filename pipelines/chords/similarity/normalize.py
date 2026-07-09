"""Chord parsing, quality reduction and grid expansion (tune_similarity_spec §4).

Pure functions, no I/O. The chord grammar mirrors
pipelines/chords/tools/check_chord_syntax.py — that file is the authoritative
vocabulary; this module only *interprets* symbols the checker accepts.
Parsing failure raises ChordParseError so pipelines can fail loudly (spec §10:
never a silent skip).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Pitch classes
# ---------------------------------------------------------------------------

_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# pc -> spelling in this book's vocabulary (spec §3.1: F, Bb, Eb, Db, F#, ...)
PC_NAME = {0: "C", 1: "Db", 2: "D", 3: "Eb", 4: "E", 5: "F",
           6: "F#", 7: "G", 8: "Ab", 9: "A", 10: "Bb", 11: "B"}


def pitch_class(name: str) -> int:
    """'Bb' -> 10, 'F#' -> 6. Raises KeyError on garbage."""
    pc = _LETTER_PC[name[0]]
    for acc in name[1:]:
        pc += {"#": 1, "b": -1}[acc]
    return pc % 12


# ---------------------------------------------------------------------------
# Chord parser
# ---------------------------------------------------------------------------

class ChordParseError(ValueError):
    pass


# Quality classes (spec §4.1): maj, min, dom, m7b5, dim, aug, sus.
# "nc" is the internal class for N.C. slots.

_ROOT = r"[A-G](?:#|b)?"
_ALT = r"(?:b5|#5|b9|#9|#11|b13)"
_STEMS = [
    "", "m", "6", "7", "9", "11", "13", "69",
    "maj7", "maj9",
    "m6", "m7", "m9", "m11", "m13", "m69", "m7b5",
    "o7", "m(maj7)",
    "sus4", "sus2", "7sus4", "9sus4",
]
_CORE = re.compile(
    rf"^(?P<root>{_ROOT})"
    rf"(?P<stem>{'|'.join(sorted(map(re.escape, _STEMS), key=len, reverse=True))})"
    rf"(?P<pext>\((?:6|7|9|11|13)\))?"
    rf"(?P<altw>alt)?"
    rf"(?P<balts>{_ALT}*)"
    rf"(?P<palts>\({_ALT}+\))?"
    rf"(?P<slash>/{_ROOT})?"
    rf"(?P<unc>\?)?$"
)

_STEM_CLASS = {
    "": "maj", "6": "maj", "maj7": "maj", "maj9": "maj", "69": "maj",
    "m": "min", "m6": "min", "m7": "min", "m9": "min", "m11": "min",
    "m13": "min", "m69": "min", "m(maj7)": "min",
    "7": "dom", "9": "dom", "11": "dom", "13": "dom",
    "m7b5": "m7b5",
    "o7": "dim",
    "sus4": "sus", "sus2": "sus", "7sus4": "sus", "9sus4": "sus",
}


@dataclass(frozen=True)
class Chord:
    symbol: str            # original printed symbol, parens/uncertainty intact
    root_pc: int | None    # None only for N.C.
    quality: str           # maj | min | dom | m7b5 | dim | aug | sus | nc
    extensions: str        # everything after root+stem, informational
    bass_pc: int | None    # slash bass, if any
    parenthesized: bool    # printed as an optional chord, e.g. (F)
    uncertain: bool        # printed with a trailing '?'

    @property
    def is_sounding(self) -> bool:
        return self.root_pc is not None


def parse_chord(symbol: str) -> Chord:
    """Parse one printed chord symbol into (root pc, quality class, ...)."""
    if symbol == "N.C.":
        return Chord(symbol, None, "nc", "", None, False, False)

    core, parenthesized = symbol, False
    if symbol.startswith("(") and symbol.endswith(")"):
        inner = symbol[1:-1]
        depth = 0
        for c in inner:
            depth += c == "("
            depth -= c == ")"
            if depth < 0:  # not one wrapping pair, e.g. would be malformed
                break
        else:
            core, parenthesized = inner, True

    m = _CORE.match(core)
    if not m:
        raise ChordParseError(f"unparseable chord symbol: {symbol!r}")

    stem = m.group("stem")
    quality = _STEM_CLASS[stem]
    alts = (m.group("balts") or "") + (m.group("palts") or "").strip("()")

    # Alteration-driven reclassification of bare triads:
    #   F(#5)/F+ style  -> aug; F(b9), D(b9), A(#5#9) -> implied dominant.
    if quality == "maj" and stem == "" and not m.group("pext"):
        if re.search(r"b9|#9|#11|b13", alts):
            quality = "dom"
        elif "#5" in alts:
            quality = "aug"
    # A parenthesised extension on a bare triad, e.g. F(13) -> dominant colour;
    # F(6) stays major.
    if quality == "maj" and stem == "" and m.group("pext") and m.group("pext") != "(6)":
        quality = "dom"
    # 'alt' always implies a dominant.
    if m.group("altw"):
        quality = "dom"

    bass = m.group("slash")
    return Chord(
        symbol=symbol,
        root_pc=pitch_class(m.group("root")),
        quality=quality,
        extensions=core[len(m.group("root")):],
        bass_pc=pitch_class(bass[1:]) if bass else None,
        parenthesized=parenthesized,
        uncertain=bool(m.group("unc")),
    )


# ---------------------------------------------------------------------------
# Grid expansion and form flattening (spec §4.2)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Slot:
    chord: Chord
    section: str
    bar: int      # 1-based bar number within the section
    half: int     # 0 = beat 1, 1 = beat 3 (mid-bar in 3/4)


def expand_section(name: str, bars: list[dict], prev: Chord | None = None
                   ) -> tuple[list[Slot], Chord | None]:
    """Expand one section to the fixed 2-slots-per-bar matching grid.

    `prev` is the chord carried in from the previous bar (continuation bars
    repeat it). Returns the slots and the chord carried out of the section.
    """
    slots: list[Slot] = []
    for i, bar in enumerate(bars):
        beats = bar.get("beats") or {}
        current = prev
        # Beat-by-beat sweep: slot 0 is whatever sounds at beat 1,
        # slot 1 whatever sounds at beat 3 (a beat-2 chord still sounds there).
        # A beat-4 chord is dropped from the grid but carries into the next bar.
        by_beat = {int(k): v for k, v in beats.items()}
        if 1 in by_beat:
            current = parse_chord(by_beat[1])
        if current is None:
            raise ChordParseError(
                f"section {name!r} bar {bar.get('bar', i + 1)} has no chord and "
                "nothing to carry over")
        slot0 = current
        for beat in (2, 3):
            if beat in by_beat:
                current = parse_chord(by_beat[beat])
        slot1 = current
        if 4 in by_beat:
            current = parse_chord(by_beat[4])
        barno = bar.get("bar", i + 1)
        slots.append(Slot(slot0, name, barno, 0))
        slots.append(Slot(slot1, name, barno, 1))
        prev = current
    return slots, prev


def expand_tune(tune: dict) -> dict[str, list[Slot]]:
    """Expand every section of a tune dict, in document (= printed form) order.

    Variants are ignored (main text only). The carried chord flows across
    section boundaries in form order, so a continuation bar at the top of a
    section repeats the previous section's last chord.
    """
    out: dict[str, list[Slot]] = {}
    prev: Chord | None = None
    for name, bars in (tune.get("sections") or {}).items():
        slots, prev = expand_section(name, bars, prev)
        out[name] = slots
    return out


def flatten(section_slots: dict[str, list[Slot]]) -> list[Slot]:
    """Concatenate section slot lists in form order (dict document order)."""
    return [s for slots in section_slots.values() for s in slots]


# ---------------------------------------------------------------------------
# Scale-degree naming (used for the `opening` field, spec §3.1)
# ---------------------------------------------------------------------------

_DEGREE_NAME = {0: "I", 1: "bII", 2: "II", 3: "bIII", 4: "III", 5: "IV",
                6: "#IV", 7: "V", 8: "bVI", 9: "VI", 10: "bVII", 11: "VII"}
_LOWERCASE_QUALITIES = {"min", "m7b5", "dim"}


def degree_name(root_pc: int, tonic_pc: int, quality: str) -> str:
    """Roman numeral of `root_pc` relative to the tune's own tonic.

    Uppercase for maj/dom/aug/sus quality classes, lowercase for
    min/m7b5/dim; accidental prefix for non-diatonic roots (single shared
    major-scale-based table for both modes, so F minor's ii reads 'ii' and
    its relative major reads 'bIII').
    """
    name = _DEGREE_NAME[(root_pc - tonic_pc) % 12]
    if quality in _LOWERCASE_QUALITIES:
        # lowercase the letters, keep the accidental prefix as-is
        name = "".join(c.lower() if c in "IV" else c for c in name)
    return name


def compute_opening(tune: dict, tonic: str, mode: str) -> dict | None:
    """The `opening` field: first sounding chord of the flattened form,
    expressed relative to the resolved key. Purely computed — no voter, no
    LLM. Returns None when the tune has no sounding chord at all.
    """
    for slot in flatten(expand_tune(tune)):
        ch = slot.chord
        if ch.is_sounding:
            return {
                "degree": degree_name(ch.root_pc, pitch_class(tonic), ch.quality),
                "quality": ch.quality,
                "chord": ch.symbol,
            }
    return None
