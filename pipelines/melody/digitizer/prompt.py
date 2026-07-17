"""The read-pass prompt: a static cacheable system rulebook + a small per-tune
message (plan §4). The model answers exactly one question — "what notes are
printed on this page" — as ABC in the house dialect, via a forced tool call.
"""

from __future__ import annotations

from .examples import EXAMPLE_ABC, EXAMPLE_INPUT_SUMMARY
from .skeleton import Skeleton

TOOL_NAME = "transcribe_melody"
REPAIR_TOOL_NAME = "resolve_bars"


def _body_after_key(abc: str) -> str:
    lines = abc.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("K:"):
            return "\n".join(lines[i + 1:])
    return abc


TRANSCRIBE_TOOL = {
    "name": TOOL_NAME,
    "description": "Return the printed melody as ABC in the house dialect.",
    "input_schema": {
        "type": "object",
        "properties": {
            "printed_key": {
                "type": "string",
                "description": "Key signature printed at the clef, e.g. F, Eb, "
                               "Ab, C. Report what is drawn, not the analyzed key.",
            },
            "abc_body": {
                "type": "string",
                "description": "Melody body only (no header lines). Pickup first "
                               "if any, then each section with its \"^label\", "
                               "bars in order matching the section plan; one "
                               "source line per staff system; || at section "
                               "ends, |] at the very end.",
            },
            "uncertain_bars": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "bar": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["bar", "reason"],
                },
                "description": "Full bars (numbered from 1; pickup is 0) you are "
                               "unsure of, with a short reason.",
            },
        },
        "required": ["printed_key", "abc_body", "uncertain_bars"],
    },
}

REPAIR_TOOL = {
    "name": REPAIR_TOOL_NAME,
    "description": "Return the resolved ABC for each flagged bar.",
    "input_schema": {
        "type": "object",
        "properties": {
            "bars": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "bar": {"type": "integer",
                                "description": "the flagged bar number"},
                        "abc": {"type": "string",
                                "description": "the corrected ABC for this bar "
                                "only (no barlines, no label), summing to the "
                                "meter"},
                        "confident": {"type": "boolean",
                                      "description": "false if still unsure"},
                    },
                    "required": ["bar", "abc", "confident"],
                },
            },
        },
        "required": ["bars"],
    },
}

# Byte-identical across every call so it caches once (like the chords digitizer).
SYSTEM_PROMPT = f"""\
You transcribe ONE handwritten jazz melody (a single-line lead sheet, no
chords) from a scanned French "grilles" book. The page shows a hand-lettered
title, a few staff systems of a treble-clef melody, section letters boxed
above the staff (A, A1, B ...), and hand-written chord symbols BELOW the staff
(ignore them for pitches — they are given to you separately as anchors).

Your ONLY job: read the printed noteheads, rests, durations, ties/slurs, and
beaming, and emit them as ABC notation in the exact house dialect below. You
are given the tune's identity, key, section plan, and per-bar chord anchors —
use them to resolve ambiguous noteheads, but transcribe the PRINTED page: it
may differ from versions of the tune you know.

=== READING DURATIONS FROM THE MANUSCRIPT (critical) ===
Read each note's duration from its DRAWN SHAPE, do not default to short notes.
Many of these tunes are slow/medium ballads written mostly in HALF and WHOLE
notes — under-reading rhythm (writing quarters where the page shows halves) is
the most common mistake. For every notehead decide:
- HOLLOW (open) oval, NO stem  => whole note  => `C8` (8 units)
- HOLLOW oval WITH a stem       => half note   => `C4` (4 units)
- FILLED (solid) head with stem, NO flag/beam => quarter => `C2` (2 units)
- FILLED head with ONE flag, or beamed to neighbors => eighth => `C` (1 unit)
- TWO flags / double beam       => sixteenth   => `C/2`
- a DOT to the right of the head adds half again: dotted half `C6`, dotted
  quarter `C3`, dotted eighth `C3/2`.
Cross-check every bar against the meter: a 4/4 bar is 8 units, 3/4 is 6. If a
bar looks short you probably read a half as a quarter or missed a dot.

=== ABC HOUSE DIALECT (follow EXACTLY) ===
- `L:1/8` is the unit. A plain letter = one eighth; `C2` a quarter, `C4` a
  half, `C8` a whole; `C3` a dotted quarter, `C6` a dotted half.
- OCTAVE by case and marks: `C`=C4 (below middle line), `c`=C5, `c'`=C6,
  `C,`=C3. Middle C is `C`. The treble staff lines bottom->top are E4 G4 B4 D5
  F5; spaces are F4 A4 C5 E5. This melody lives roughly F3-C6.
- BEAMING = ADJACENCY. Notes written with NO space between them are beamed
  under one beam; a space breaks the beam. Reproduce the manuscript's beam
  groups EXACTLY: four eighths beamed together are `GABc`, NOT `G A B c`.
  NEVER space every note — that is wrong. Half/whole notes and quarters that
  stand alone get spaces around them; runs of eighths/sixteenths that share a
  beam are written adjacent.
- ACCIDENTALS: `^`=sharp, `_`=flat, `=`=natural, written immediately before
  the letter (`_B`, `^F`, `=E`). They persist to the end of the bar. Write
  them EXACTLY as printed, including courtesy/redundant ones.
- TIES: `-` after a note ties it to the next note of the SAME pitch
  (`_B8- | B2`). The tie target is written PLAIN (do not repeat the
  accidental). A tie may cross a barline or a system break.
- SLURS: `(` ... `)` group notes of DIFFERENT pitch under a phrase slur; used
  where the manuscript draws a curved line over notes that are not a tie.
- TRIPLETS: `(3xyz` = three eighths in the time of two (2 units total).
  `(3X2Y2Z2` = three quarters in the time of two (4 units total, a quarter
  triplet — usually drawn as a bracket with NO beam). Beamed triplets are
  eighth triplets; beamless bracketed triplets are quarter triplets.
- RESTS: `z` = rest (an eighth), `z2` quarter rest, `z4` half, `z8` whole.
  A quarter rest is a tall zig-zag; an eighth rest is a small `7` near C5.
- BARLINES: `|` between bars, `||` at the end of a section, `|]` at the very
  end. Put ONE ABC source line per staff system, ending sections with `||`.
- SECTION LABELS: write the given label before its first bar as `"^A"` (an
  annotation), e.g. `"^A" B8- | ...`. Use the labels you are given, in order.
- PICKUP/ANACRUSIS: if the tune starts with a partial bar before the first
  full bar, write those notes once before the first section label and the
  first `||`, e.g. `c3 G || "^A" ...`. On repeats the same pickup notes
  usually reappear inside the last shared bar — include them there too.
- Every FULL bar's durations must sum to the meter (8 units in 4/4, 6 in 3/4).
  The pickup bar is the only short one.

=== WRITER PROFILE (this book's hand) ===
- Noteheads often hang LOW in their space or sit ambiguously between a line
  and the space below it. When a notehead is between two positions, prefer the
  reading consistent with the tune and the chord anchor.
- Slash-shaped heads sitting on a line tend to read about one step high.
- The chord-text `+` (as in "C7+") sits below the staff and can mimic a
  ledger-line notehead — it is NOT a note.
- Accidentals may be drawn slightly ABOVE the note they modify.
- Courtesy/redundant flats are common; transcribe what is printed.

=== OUTPUT ===
Call the `{TOOL_NAME}` tool. `printed_key` is the signature drawn at the clef
(reduced-signature minor tunes print fewer flats than the analyzed key — report
what you SEE, accidentals inline). `abc_body` is the body ONLY (no X/T/C/O/R/M/
L/K lines). `uncertain_bars` lists full bars (numbered from 1; pickup is 0) you
are unsure of — be honest, flags are cheap to review and silent errors are not.

=== WORKED EXAMPLE ===
Given this input summary:
{EXAMPLE_INPUT_SUMMARY}

the correct `abc_body` is:
{_body_after_key(EXAMPLE_ABC)}
"""

STRICTER_REMINDER = (
    "\n\nReminder: bars must match the section plan and each full bar must sum "
    "to the meter; reproduce the manuscript's beam groups (adjacency), do not "
    "space every note; report the PRINTED key signature."
)


def _chord_anchor_lines(skeleton: Skeleton) -> str:
    out = []
    for sec in skeleton.sections:
        anchors = " | ".join(c or "-" for c in sec.chords)
        out.append(f'  "^{sec.label}" ({sec.bars} bars): {anchors}')
    return "\n".join(out)


def _tune_context(skeleton: Skeleton) -> str:
    composer = skeleton.composer or "unknown"
    year = f", {skeleton.year}" if skeleton.year else ""
    plan_lines = "\n".join(
        f'  "^{s.label}": {s.bars} bars' for s in skeleton.sections)
    key_hint = skeleton.printed_key
    if skeleton.needs_printed_key:
        key_hint += (" (analyzed key is minor; the page likely prints a reduced "
                     "signature — report what you see)")
    return f"""\
This is "{skeleton.title}" ({composer}{year}), the jazz standard. Meter
{skeleton.meter}, L:1/8. Analyzed key {skeleton.key_tonic} {skeleton.key_mode};
printed key signature to confirm: {key_hint}.

Section plan (labels and bar counts — your abc_body MUST match these exactly):
{plan_lines}

Per-bar chord anchors (for locating bars and as a harmonic tiebreak ONLY —
never write chords into the melody):
{_chord_anchor_lines(skeleton)}"""


def build_user_content(skeleton: Skeleton, image_b64: str,
                       media_type: str) -> list[dict]:
    """Pass A message: the full-page crop + identity + plan + chord anchors."""
    text = (_tune_context(skeleton) + f"""

Transcribe the melody now. Reproduce the manuscript's beaming (adjacency),
octaves (case), durations, ties/slurs, and rests. Include the pickup if the
tune has one. Call {TOOL_NAME}.""")
    return [
        {"type": "image",
         "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
        {"type": "text", "text": text},
    ]


def build_pass_b_content(skeleton: Skeleton,
                         tiles: list[tuple[str, str, str]]) -> list[dict]:
    """Pass B message: per-system overlay strips with the pitch ruler.

    `tiles` = [(label, base64_png, media_type)] in reading order (top system
    first, left half then right). The ruler legend tells the model how to read
    pitch from the colored dashes — this is the decorrelated evidence that the
    full-page Pass A lacks."""
    from .strips import OVERLAY_LEGEND

    content: list[dict] = [{"type": "text", "text": _tune_context(skeleton)}]
    content.append({"type": "text", "text": OVERLAY_LEGEND})
    for label, b64, media_type in tiles:
        content.append({"type": "text", "text": f"--- {label} ---"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": media_type, "data": b64}})
    content.append({"type": "text", "text": f"""
Read the melody from these ruler-annotated strips (they are the SAME tune, top
system to bottom, left half then right half). Use the RED/GREEN/BLUE ruler to
fix each notehead's pitch and octave precisely. Reproduce beaming (adjacency),
durations, ties/slurs, rests; include the pickup if any. Your abc_body must
match the section plan exactly. Call {TOOL_NAME}."""})
    return content


def build_repair_content(skeleton: Skeleton,
                         items: list[dict],
                         tiles_by_system: dict[int, tuple[str, str]]
                         ) -> list[dict]:
    """Repair message: for each flagged bar, the two candidate readings, the
    calibrated head-centroid measurements, the chord, and the zoomed strip.

    `items` = [{"bar", "system", "chord", "read_a", "read_b", "measure",
                "reason"}]. `tiles_by_system` = {system_index: (b64, media)}.
    Returns content that asks the model to decide each bar via the repair tool.
    """
    lines = [_tune_context(skeleton), "", "Resolve each flagged bar below. For "
             "each, you are given two candidate readings (Pass A full-page, "
             "Pass B ruler strips), a deterministic pixel measurement of the "
             "noteheads, the bar's chord, and the meter constraint. Decide the "
             "correct ABC for each bar (it MUST sum to the meter)."]
    seen_systems: list[int] = []
    for it in items:
        lines.append(
            f"\nBAR {it['bar']} (system {it['system']}, chord {it['chord']}): "
            f"reason {it.get('reason','')}\n"
            f"  Pass A: {it['read_a']}\n"
            f"  Pass B: {it['read_b']}\n"
            f"  pixel measurement: {it['measure']}")
        if it["system"] not in seen_systems:
            seen_systems.append(it["system"])
    content: list[dict] = [{"type": "text", "text": "\n".join(lines)}]
    for sysno in seen_systems:
        tile = tiles_by_system.get(sysno)
        if tile is None:
            continue
        b64, media = tile
        content.append({"type": "text", "text": f"--- system {sysno} (ruler) ---"})
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": media, "data": b64}})
    content.append({"type": "text", "text":
                    "Return the corrected bars. Call " + REPAIR_TOOL_NAME + "."})
    return content
