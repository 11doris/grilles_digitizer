#!/usr/bin/env python3
"""Validate chord syntax in manually edited tunes against the prompt.py vocabulary.

Usage
-----
    python pipelines/chords/tools/check_chord_syntax.py [dir ...]
    # default dirs: data/chords/04_verified data/chords/03_wip

Grammar derived from pipelines/chords/digitizer/prompt.py (=== CHORD NOTATION ===,
ALTERATIONS & PARENTHESES, EXTENSIONS, SUS/SLASH/NO-CHORD):

  chord      := "N.C." | "(" core ")" | core        # outer parens = optional chord
  core       := root stem paren_ext? ("alt" | alts) slash? "?"?
  root       := [A-G] ("#"|"b")?                    # as printed, no enharmonic change
  stem       := "" | m | 6 | 7 | 9 | 11 | 13 | 69 | maj7 | maj9
              | m6 | m7 | m9 | m11 | m13 | m69 | m7b5 | o7 | m(maj7)
              | sus4 | sus2 | 7sus4 | 9sus4         # bare printed "sus" -> sus4
  paren_ext  := "(13)" etc. — parenthesised superscript extension, kept literal
  alts       := (b5|#5|b9|#9|#11|b13)+ in ascending-degree order
                - bare when a 7th/extension number is present (F7#5, C9b5)
                - parenthesised on a bare triad: F(#5), D(b9), A(#5#9)
                - EXCEPTION: #5 on a bare minor triad is bare: Bbm#5, never Bbm(#5)
                - never both, never "9b" — flat-nine is always spelled "(b9)"
  slash      := "/" root                            # slash bass, e.g. Fm7/Bb
  o7         := lowercase "o" + "7" (the only allowed diminished spelling)
  alt        := printed "alt" -> literal suffix "alt" (F7alt); requires a
                7th/extension and excludes explicit alterations

Exit code 1 if any chord fails, 0 otherwise.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

IGNORED_STEMS = {"verification_state", "run_report", "run_state"}

ROOT = r"[A-G](?:#|b)?"
ALT = r"(?:b5|#5|b9|#9|#11|b13)"

STEMS = [
    r"",
    r"m",
    r"6", r"7", r"9", r"11", r"13",
    r"69",
    r"maj7", r"maj9",
    r"m6", r"m7", r"m9", r"m11", r"m13",
    r"m69",
    r"m7b5",
    r"o7",
    r"m\(maj7\)",
    r"sus4", r"sus2", r"7sus4", r"9sus4",
]

# stems that contain a 7th or extension number -> alterations attach BARE
HAS_EXT = re.compile(r"(?:6|7|9|11|13)")

CORE = re.compile(
    rf"^(?P<root>{ROOT})"
    rf"(?P<stem>{'|'.join(sorted(STEMS, key=len, reverse=True))})"
    rf"(?P<pext>\((?:6|7|9|11|13)\))?"
    rf"(?P<altw>alt)?"
    rf"(?P<balts>{ALT}*)"
    rf"(?P<palts>\({ALT}+\))?"
    rf"(?P<slash>/{ROOT})?"
    rf"(?P<unc>\?)?$"
)

DEGREE = {"b5": 5.0, "#5": 5.5, "b9": 9.0, "#9": 9.5, "#11": 11.0, "b13": 13.0}


def check_core(s: str) -> list[str]:
    m = CORE.match(s)
    if not m:
        return ["does not parse against canonical grammar"]
    errs = []
    stem = m.group("stem")
    balts = m.group("balts") or ""
    palts = m.group("palts") or ""
    has_ext = bool(HAS_EXT.search(stem)) or bool(m.group("pext"))
    if m.group("altw"):
        if not has_ext:
            errs.append("'alt' on a triad without 7th/extension")
        if balts or palts:
            errs.append("'alt' combined with explicit alterations")
    minor_aug = stem == "m" and not has_ext and not m.group("altw")
    if balts and palts:
        errs.append("mixes bare and parenthesised alterations")
    if balts and not has_ext and not (minor_aug and balts == "#5"):
        errs.append(
            f"bare alteration '{balts}' on a triad without 7th/extension -> "
            f"should be parenthesised, e.g. {m.group('root')}{stem}({balts})"
        )
    if palts and has_ext:
        errs.append(f"parenthesised alteration '{palts}' but a 7th/extension is present -> should be bare")
    if palts == "(#5)" and minor_aug:
        errs.append(f"minor triad #5 attaches bare -> {m.group('root')}m#5, not {m.group('root')}m(#5)")
    for group in (balts, palts.strip("()")):
        alts = re.findall(ALT, group)
        degs = [DEGREE[a] for a in alts]
        if degs != sorted(degs):
            errs.append(f"alterations not in ascending-degree order: {alts}")
        if len(alts) != len(set(alts)):
            errs.append(f"duplicate alteration: {alts}")
    return errs


def check_chord(s: str) -> list[str]:
    if s == "N.C.":
        return []
    if s.startswith("(") and s.endswith(")"):
        inner = s[1:-1]
        depth = 0
        for c in inner:
            depth += c == "("
            depth -= c == ")"
            if depth < 0:  # outer parens are not one wrapping pair
                return check_core(s)
        return check_core(inner)
    return check_core(s)


def walk(bars, label: str, fname: str, out: list) -> None:
    for b in bars:
        if not isinstance(b, dict):
            out.append((fname, label, "?", "?", repr(b), ["bar is not an object"]))
            continue
        for beat, ch in (b.get("beats") or {}).items():
            errs = check_chord(ch)
            if errs:
                out.append((fname, label, b.get("bar"), beat, ch, errs))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    _repo = Path(__file__).resolve().parents[3]  # repo root
    dirs = sys.argv[1:] or [str(_repo / "data" / "chords" / "04_verified"),
                            str(_repo / "data" / "chords" / "03_wip")]
    failed = False
    for dirname in dirs:
        print(f"===== {dirname} =====")
        out: list = []
        n_files = 0
        for p in sorted(Path(dirname).glob("*.json")):
            if p.stem in IGNORED_STEMS or p.stem.endswith("_opus"):
                continue
            d = json.loads(p.read_text("utf-8"))
            n_files += 1
            for sec, bars in (d.get("sections") or {}).items():
                walk(bars, sec, p.name, out)
            for v in d.get("variants") or []:
                walk(v.get("bars") or [], f"variant[{v.get('applies_to', '')}]", p.name, out)
        for fname, label, bar, beat, ch, errs in out:
            print(f"{fname}  {label} bar {bar} beat {beat}:  {ch!r}")
            for e in errs:
                print(f"    - {e}")
        if out:
            failed = True
        else:
            print("  all chords conform")
        print(f"  ({n_files} files scanned)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
