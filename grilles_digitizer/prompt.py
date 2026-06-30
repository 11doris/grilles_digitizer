"""The VLM prompt: a static, cacheable system block plus a tiny per-unit message."""

from __future__ import annotations

from .config import SOURCE_CONSTANT
from .examples import EXAMPLES
from .manifest import WorkUnit

# The system block is byte-identical across every call so it caches once and is
# billed ~once instead of per tune. Keep the per-call variable part (page) out of
# here — it goes in the user message. The block is deliberately large: the rulebook
# plus the worked examples (appended below) push it comfortably past the 4,096-token
# cache minimum so it caches on every platform (spec §5.1 / §18.3).
_BASE_PROMPT = f"""\
You are transcribing ONE handwritten jazz chord grid (one tune) from a scanned
French jazz "grilles" book ("{SOURCE_CONSTANT}"). The image shows a large
hand-lettered title, smaller style/tempo/form labels, and a grid of chord boxes
organized into rows (sections). Recording credits may run vertically in the left
and/or right margins — IGNORE them entirely.

Produce ONE bare JSON object (no array, no prose, no markdown fence). Transcribe
only what the image shows. Expand every repeat/shorthand into explicit chords.

=== OUTPUT SCHEMA ===
{{
  "composer": str,              // OMIT if absent; names joined " – " (space en-dash space)
  "year": str,                  // OMIT if absent; e.g. "1931"
  "style": str,                 // always present; upper-left genre label, as printed
  "tempo": str,                 // OMIT if absent
  "form": str,                  // always present; exactly as printed, KEEP primes
  "time_signature": str,        // always present; default "4/4"
  "sections": {{ ... }},          // always present; see SECTIONS
  "notation_notes": {{ ... }}     // OMIT if empty
}}
DO NOT output a "title" field — the title is supplied separately and added by the
runner. Likewise do NOT output "page" or "source"; the runner sets those.
OPTIONAL-FIELD POLICY: when an optional field has no content, OMIT THE KEY ENTIRELY.
Never emit null or "" for it. Do NOT emit a "fingerprints" field.

=== FIELD RULES ===
- style: the upper-left genre label exactly as printed (DIXIELAND, NEW ORLEANS,
  SWING, STANDARD, ELLINGTONIA, ...).
- tempo: the tempo label (MEDIUM, MEDIUM FAST, MEDIUM SLOW, FAST, ...). Omit if absent.
- form: exactly as printed, preserving primes (e.g. "32 A B C A'").
- time_signature: default "4/4"; override only if the score indicates otherwise.

=== SECTIONS ===
"sections" maps a section id to a list of bar objects.
Section ids:
- Letter sections keep uppercase letters: A, B, C.
- Repeats of the same letter get a numeric counter in order of appearance:
  A, A1, A2, ... DO NOT use primes in section keys (primes live only in "form").
- Named sections are lowercase words: intro, interlude, coda.
- Prefixed/compound sections: lowercase prefix + uppercase letter:
  verse_A, verse_B, clarinet_A, clarinet_A1.
- One section id = one printed row. Sections are NOT forced to 8 bars — use whatever
  the row actually contains.

FORM EXPANSION: the printed grid often shows fewer rows than "form" implies. Expand
the form by COPYING the printed rows into the full set of sections. Example: a printed
A-row + B-row with form "32 A A B A" becomes sections A, A1 (copy of A), B, A2 (copy
of A). Any explicitly written bar in a repeated row overrides the copied value.

MULTI-STRAIN PIECES (rare — multi-strain rags/stride/marches): when the page shows
TWO OR MORE separate grids ("strains") stacked vertically, EACH with its own form
label (e.g. "16 A A'", then "24 A B A", then "16 A A"):
- Number the printed strains s1, s2, s3, ... top to bottom (or use a lowercase word
  if the score names a strain, e.g. "trio").
- Prefix EVERY section key of a strain with its id + underscore: s1_A, s1_A1, s2_A,
  s2_B, s2_A1, s3_A, s3_A1. Letter/counter rules apply independently within each strain
  (counters restart at A in each strain; never use primes in keys).
- A connecting passage between strains that is not a lettered strain (modulation,
  interlude, intro, coda) is a BARE named section (no strain prefix), in playing order.
- Set "form" to the per-strain printed labels joined with " | " in printed order,
  e.g. "16 A A' | 24 A B A | 16 A A".
- Keep everything in the ONE flat "sections" map; do not nest.
ONLY use this when there really are multiple labelled strains. A normal AABA tune is
single-strain: plain keys (A, A1, B, ...) and one form string — never wrap it in s1_.

=== BARS AND BEATS ===
Every bar is an object: {{ "bar": 3, "beats": {{ "1": "Ab", "3": "A°" }} }}
- "bar": 1-indexed within its section, restarting at 1 each section.
- "beats": keys are beat-number strings "1".."4", values are chord strings.
- A whole-bar chord is STILL an object: {{ "bar": 1, "beats": {{ "1": "Db" }} }}.
  Never emit a bare string for a bar.
- Encode a beat ONLY where a chord visibly begins in that beat's region. Do not pad
  held chords onto later beats. EXCEPTION: if a bar visibly re-writes a chord in a
  later region (even the same chord again), preserve that repeat — transcribe what is
  written, not what theory would collapse.

=== BAR SUBDIVISION LAYOUTS (the only cases) ===
Read each chord and ALL its alteration suffixes only from within its own region.
1. Undivided, one chord: whole bar -> {{ "1": "Cm7" }}
2. Diagonal split (top-right to bottom-left) OR horizontal-half split: BOTH encode
   identically. upper/upper-left -> "1"; lower/lower-right -> "3":
   {{ "1": "Eb", "3": "Eb7" }}
3. Bottom-right inset square only (no full horizontal divider): the large area is
   "1"; the small framed corner square is beat 4 -> "4": {{ "1": "Em7", "4": "Eb°" }}
   AMBIGUITY FALLBACK: if scan quality makes the inset corner indistinguishable from
   a plain diagonal, treat it as a diagonal (Case 2) -> beat 3, and add a
   notation_notes entry for that bar.
4. Upper half + lower-left + lower-right: upper -> "1"; bottom-left -> "3";
   bottom-right -> "4": {{ "1": "A", "3": "B", "4": "C" }}
5. Upper-left + upper-right + lower half: top-left -> "1"; top-right -> "2";
   lower -> "3": {{ "1": "A", "2": "B", "3": "C" }}
6. Four squares (2x2): top-left "1", top-right "2", bottom-left "3", bottom-right "4".

BOUNDARY BOX RULE: when a bar is subdivided, a chord symbol AND all its alteration
suffixes (b5, #5, b9, m, °, etc.) must be read only from within that beat's own
region. Never reach across a subdivision line to attach an alteration to a neighbor.

=== REPEAT AND SHORTHAND EXPANSION ===
ALWAYS expand fully. NEVER output -, %, •/•, ->, or any shorthand. Write the chords.
- Arrow + vertical line between rows (full section repeat): copy all bars from the
  most recent section with the same letter; explicit written bars override.
- Plain -> at the start of a row: copy the previous row of the same section verbatim;
  explicit bars override.
- Diagonal spanning TWO adjacent boxes (two-bar repeat): copy the immediately
  preceding two bars into those two bars.
- •/• or similar within one box (bar repeat): copy the immediately preceding bar.
- - (dash) in a box (bar repeat): copy the immediately preceding bar.
DASH EXCEPTION: if a - is the very first bar of a tune with nothing preceding it, it
is a genuine empty bar — encode {{ "1": "N.C." }} and note it in notation_notes.

=== CHORD NOTATION (canonical vocabulary) ===
Major triad -> root only (C). Minor -> m (Cm). Dominant 7th -> 7 (G7).
Major 7th -> maj7 (Cmaj7). Minor 7th -> m7 (Dm7). Half-diminished -> m7b5 (Am7b5).
Diminished -> ° (C°). Augmented triad -> + (Eb+). Augmented dominant -> 7#5 (Eb7#5).
Minor-major 7th -> m(maj7) (Dm(maj7)). Sixth/ninth etc. -> 6, 9, m6, 9#11 (Ab6, Db9).
CONVERSIONS from the book to canonical:
- 7M, M7, Δ (major 7) -> maj7  (Eb7M -> Ebmaj7)
- ø (half-dim) -> m7b5  (Aø -> Am7b5)
- superscript 5+ (aug 5th) -> #5  (Bb7 with 5+ -> Bb7#5)
- suffix t (means +, i.e. raise) -> # on that degree  (Eb9t -> Eb#9; F75t -> F7#5)
- .../14 (French "14th") -> #11  (E9/14 -> E9#11, since 7+7=14)
- alteration in parentheses (...) -> OMIT entirely  (Bb9(b9) -> Bb9; D9(b5) -> D9)
  (The ONLY parentheses allowed in output are in m(maj7).)
OTHER: Watch B vs Bb carefully — different chords. If a chord is uncertain due to scan
quality, append ? to that chord string (e.g. Bbmaj7?) and add a notation_notes entry.

=== RECORDINGS & VARIANTS (do not digitize) ===
Omit margin performer/year credits. Omit any * / VARIANTE / STATEMENT markers and
their footnotes.

=== NOTATION NOTES ===
"notation_notes" is a free-form object mapping a short key to an explanation. OMIT if
empty. Record when applicable: the French "14"=#11 convention, the 5+ convention, the
t=+ convention; any omitted parenthesised alterations; any chord marked ? (and why);
any Case-3 inset/diagonal ambiguity and which bar; enharmonic/ambiguous readings;
truncation; composer/performance annotations printed on the score; a missing grid
(key "no_chord_grid").

=== MISSING CHORD GRID / CROSS-REFERENCES ===
Some tunes print no grid (they point to another tune's changes).
- Set "sections": {{}}.
- Add a notation_notes entry under key "no_chord_grid", e.g. "No chord grid printed.
  Form indicated as <label>. Chords must be inferred from the standard form."
- DO NOT invent chord content. If the crop cross-references another tune, record that
  target in the no_chord_grid note.

Return ONE bare JSON object only. No prose, no markdown fence, valid JSON, minified."""


def _examples_block() -> str:
    """The Appendix D worked examples, embedded as few-shot guidance (spec §5.1)."""
    parts = [
        "=== WORKED EXAMPLES ===",
        "Real pages' correct outputs, in the MODEL's shape (title/page/source are added "
        "by the runner, so they are absent here). Match this structure and notation "
        "exactly. The comment before each shows what it demonstrates.",
    ]
    for ex in EXAMPLES:
        parts.append(f"\n# {ex['title']} — {ex['demonstrates']}\n{ex['tune_json']}")
    return "\n".join(parts)


SYSTEM_PROMPT = _BASE_PROMPT + "\n\n" + _examples_block()


def build_user_content(unit: WorkUnit, image_b64: str, media_type: str) -> list[dict]:
    """The per-tune message: the cleaned crop image plus the page anchor.

    The title is NOT given to the model (spec §5) — the runner fills it from the
    manifest. Only `page` is provided as context.
    """
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": image_b64,
            },
        },
        {"type": "text", "text": f"The printed page is {unit.page}."},
    ]


STRICTER_REMINDER = (
    " Return one bare JSON object only; no prose; no markdown fence; valid JSON."
)

# Forced tool use guarantees structured JSON with no prose preamble, on every model
# (current Claude models reject assistant-message prefill). The model is forced to
# call this tool exactly once; its `input` IS the tune object. title/page/source are
# deliberately absent — the runner injects them.
TOOL_NAME = "record_tune"
TUNE_TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Record the transcribed tune as structured data. Call exactly once. Follow "
        "all schema, notation, and section rules given in the system instructions. Do "
        "NOT include title, page, or source — those are filled in separately."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "composer": {"type": "string"},
            "year": {"type": "string"},
            "style": {"type": "string"},
            "tempo": {"type": "string"},
            "form": {"type": "string"},
            "time_signature": {"type": "string"},
            "sections": {
                "type": "object",
                "description": (
                    "Map of section id -> list of bar objects. Each bar is "
                    '{"bar": int, "beats": {"1".."4": "<chord>"}}. See the system '
                    "instructions for section ids, layouts, repeat expansion, and the "
                    "canonical chord vocabulary."
                ),
            },
            "notation_notes": {"type": "object"},
        },
        "required": ["style", "form", "time_signature", "sections"],
    },
}
