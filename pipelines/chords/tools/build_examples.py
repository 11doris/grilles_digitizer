#!/usr/bin/env python3
"""Regenerate pipelines/chords/digitizer/examples.py from the tunes in data/chords/04_verified/.

The worked examples are embedded verbatim in the cached system prompt (spec §5.1):
they serve as few-shot guidance and as the bulk that pushes the cached prefix past
the 4,096-token cache minimum (spec §18.3). Sourcing them from the hand-verified
tunes in data/chords/04_verified/ keeps the few-shot in sync with the corrected ground
truth instead of a separate hand-copied spec appendix.

Each example is one verified tune, stripped to the MODEL's output shape (runner
fields removed, empty optional fields omitted), paired with a short `demonstrates`
blurb kept here in DEMONSTRATES (keyed by tune file stem). The examples are emitted
in the order DEMONSTRATES lists them.

Usage:  python pipelines/chords/tools/build_examples.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # repo root
VERIFIED = ROOT / "data" / "chords" / "04_verified"
OUT = ROOT / "pipelines" / "chords" / "digitizer" / "examples.py"

# Fields the runner injects (spec §5/§6) — strip them so each example mirrors the
# MODEL's output shape, which is what the few-shot should teach.
RUNNER_FIELDS = ("title", "page", "source")

# One short blurb per verified tune (keyed by file stem) describing what few-shot
# lesson it carries. Kept in sync by hand with the chords actually in data/chords/04_verified/.
# Insertion order here is the order examples appear in the output.
DEMONSTRATES = {
    "20_01_ANNIE_LAURIE": (
        "Demonstrates the **recordings** and **variants** fields (§13). Right-margin "
        "credits become `recordings`. Two alternates below the grid: one at Bar 27 "
        "(two Case-2 diagonal-split bars) and one at Bar 6 (three bars `Am D7 G7`), "
        "while `sections` keeps the original chords. Also a "
        "**brief-extension split** (`B` bar 5 `Am`→`Am7`, root carried over — two "
        "beats, never collapsed), an **augmented triad** (`C(#5)`), a full-section "
        "arrow/dash repeat (`A'`), and `notation_notes` for a parenthesised optional "
        "`(G7)` and an unknown composer date. Style `OLD SCOTCH SONG`."
    ),
    "21_04_ASK_ME_NOW": (
        "Demonstrates **dense four-chord bars** — several bars fill beats `\"1\"`–"
        "`\"4\"` (e.g. `A` bar 1 `Gm7 C7 F#m7 B7`). Shows `#11` (`B7#11`, `Bb7#11`), "
        "parenthesised **`(13)` extensions** (`Ab7(13)`, `D9(13)`), and a "
        "`32 A A B A` head. A `year_uncertain` note flags the header's `1951 ?`. "
        "Style `MODERN JAZZ`; one short `recordings` line."
    ),
    "22_02_AS_TIME_GOES_BY": (
        "Demonstrates **primes in `form` (`32 A A B A'`) vs counters in section keys** "
        "(`A`, `A1`, `B`, `A2`). A six-bar **variant** (Bar 1, 9, 25) with four-chord "
        "bars and the book's `7M` normalised to canonical (`Abmaj7`) — including inside "
        "the variant box. A top-level **`same_chord_changes`** field holds the "
        "**SAME CHORD CHANGES** cross-reference with the label stripped (value is just "
        "the referenced tune); a `notation_notes` entry records a `12 VERSE` header note."
    ),
    "114_01_EASY_LIVING": (
        "Demonstrates a plain **`32 A A B A`** head with a very long **recordings** "
        "list. Shows a **triad + parenthesised alteration** (`D(b9)` — a bare triad "
        "carrying a b9, so the accidental is parenthesised), `7#5` (`A7#5`, `G7#5`), "
        "`m7b5` (`Bm7b5`), a `7b5` (`B7b5`), and a beat-`\"4\"` chord inside a split "
        "bar (`A` bar 2 `Gm7`/`Bm7b5`/`E7`)."
    ),
    "119_03_EVERYTHING_HAPPENS_TO_ME": (
        "Demonstrates a **`VERSE` header note** (`form` `32 A A B A`, with "
        "`notation_notes.verse` recording the `4 VERSE` header) and "
        "**extended/altered chords**: `m11` (`Fm11`, `Em11`), stacked alterations on "
        "bare triads (`Bb(#9#5)`, `A(#9#5)`, `E(b5b9)`), a triad + b9 (`F(b9)`), and "
        "`9b5` (`E9b5`). One **variant** (Bar 2, 10 and 26) plus a full `recordings` "
        "list."
    ),
    "120_02_EVERYTIME_WE_SAY_GOODBYE": (
        "Demonstrates **`32 A B A C`** (the repeated A is keyed `A1`, no prime). **Two "
        "variants** (Bar 27 and Bar 14). Shows `9b5` "
        "(`C9b5`), triads + parenthesised alterations (`Bb(b9#5)`, `Eb(b9)`), and "
        "beat-`\"2\"` chords in split bars (`B` bar 2 `Fm7`/`Bb7`)."
    ),
    "340_03_ROBBINS_NEST": (
        "Demonstrates **form expansion under truncation** — only 16 bars (`A`, `A1`) "
        "are printed; `B` and the final `A2` are reconstructed from `form` "
        '"32 A A B A", recorded in `notation_notes.truncated`. Whole-bar chords stay '
        'objects (`{"1":"Db"}`). Style `SWING`.'
    ),
    "9_04_AIN_T_MISBEHAVIN": (
        "Demonstrates a **multi-strain verse+chorus** tune: the 16-bar verse (`form` "
        '"16 A A\'") becomes `verse_A`, `verse_A1`; the 32-bar chorus ("32 A A B A") '
        "becomes `A`, `A1`, `B`, `A2`; the two strains are joined in `form` with "
        '" | ". Also shows a single-bar **variant** (BAR 1), a `same_chord_changes` '
        "cross-reference, a long **recordings** list, an aug-dominant `G7#5`, and a "
        "beat-`\"4\"` chord (`verse_A1` bar 5 `Cm`→`Cm7`)."
    ),
}


def main() -> None:
    examples = []
    for stem, blurb in DEMONSTRATES.items():
        path = VERIFIED / f"{stem}.json"
        if not path.exists():
            raise SystemExit(f"verified tune not found: {path}")
        tune = json.loads(path.read_text(encoding="utf-8"))
        title = tune.get("title", stem)
        for key in RUNNER_FIELDS:
            tune.pop(key, None)
        # Optional fields are OMITTED when empty (spec optional-field policy) — the
        # verifier may leave an empty "" / {} behind, so drop it here.
        if not tune.get("notation_notes"):
            tune.pop("notation_notes", None)
        # The variant marker symbol is not meaningful output — only which bars the
        # variant applies to and its chords matter. Drop it.
        for variant in tune.get("variants", []):
            variant.pop("marker", None)

        examples.append(
            {
                "title": title,
                "demonstrates": blurb,
                "tune_json": json.dumps(tune, ensure_ascii=False, separators=(",", ":")),
            }
        )

    if not examples:
        raise SystemExit("no examples extracted — check DEMONSTRATES")

    header = (
        '"""Worked examples for the system prompt (few-shot + cacheable bulk).\n\n'
        "Auto-generated from the verified tunes in data/chords/04_verified/ by "
        "pipelines/chords/tools/build_examples.py.\n"
        "Each tune_json is the MODEL's output shape (no title/page/source). Run the "
        "tool\n"
        "to regenerate when the verified tunes change.\n"
        '"""\n\n'
    )
    OUT.write_text(
        header + "EXAMPLES = " + json.dumps(examples, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT} with {len(examples)} examples: {[e['title'] for e in examples]}")


if __name__ == "__main__":
    main()
