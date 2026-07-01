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
organized into rows (sections). Recording credits (performer + 2-digit year) may run
vertically in the left and/or right margins — TRANSCRIBE them into "recordings". Below
the grid there may be alternate "VARIANTE" bars — transcribe them into "variants".
Both are covered in RECORDINGS & VARIANTS below.

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
  "recordings": [ ... ],          // OMIT if none; margin performer/year credits (list of strings)
  "variants": [ ... ],            // OMIT if none; alternate bars; see RECORDINGS & VARIANTS
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
   BRIEF-EXTENSION SPLIT: sometimes only one region names a full chord and the other
   shows just an added degree with NO root of its own (a bare 7, 6, 9, m7, ... written
   for brevity). Carry the root over and expand: a box with Am upper and a bare 7 lower
   means {{ "1": "Am", "3": "Am7" }} (two beats), NOT a single Am7. Never collapse it.
   (Borrowing the missing ROOT this way is the one exception to the BOUNDARY BOX RULE;
   a bare alteration like b5/#5 is not a chord and stays with its own region.)
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
ONE EXCEPTION: a region showing only a bare added degree (7, 6, m7, ...) with no root
of its own borrows the ROOT from the adjacent named chord — the brief-extension split
above (Am | 7 -> {{ "1": "Am", "3": "Am7" }}). This borrows only the missing root.

=== REPEAT AND SHORTHAND EXPANSION ===
ALWAYS expand fully. NEVER output -, %, •/•, ->, or any shorthand. Write the chords.
- LEFT-ARROW ROW (a -> drawn at the FAR LEFT of a row, sometimes joined by a vertical
  line or bracket to an earlier row): this whole row is a POSITIONAL copy of an EARLIER
  row that carries the SAME LETTER. To find which row it copies, work in this order:
  1. USE THE "form" STRING to label every printed grid row with its letter, top to
     bottom, in order. Example: form "32 A A B A" labels the four rows A, A, B, A;
     so the sections are A, A1, B, A2.
  2. A left-arrow row copies from the NEAREST ROW ABOVE IT THAT HAS THE SAME LETTER in
     that labelling — which is usually NOT the row physically just above it. In
     "A A B A" the 4th row (A2) copies the 2nd row (A1), JUMPING OVER the 3rd row (B).
     NEVER copy a B row into an A row (or any letter into a different letter) just
     because it is physically adjacent — the letters MUST match.
  3. Fill EACH bar of the arrow row from the bar in the SAME position (same bar number)
     of that same-letter referenced row; a bar written explicitly in the arrow row
     OVERRIDES the copied value at that position.
  - Inside a left-arrow row, a dash (-) or blank box is NOT a bar-repeat — it is an
    EMPTY PLACEHOLDER meaning "take this bar, unchanged, from the same position of the
    referenced same-letter row". Do NOT copy the preceding bar into it.
  - The referenced row is that same-letter row AS ALREADY RESOLVED — its own copies and
    overrides carried through — NOT necessarily the original first A. So if A1 itself
    overrode, say, bars 7-8, then A2's empty bars 7-8 inherit A1's OVERRIDDEN bars 7-8.
    Only the boxes A2 writes explicitly differ from A1.
- Diagonal spanning TWO adjacent boxes (two-bar repeat): copy the immediately
  preceding two bars into those two bars.
- •/• or similar within one box (bar repeat): copy the immediately preceding bar.
- - (dash) in a box (bar repeat): copy the immediately preceding bar. THIS APPLIES
  ONLY OUTSIDE a left-arrow row; inside a left-arrow row a dash is the positional
  placeholder described above, never a repeat of the preceding bar.
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
quality, append ? to that chord string (e.g. Bbmaj7?).

=== RECORDINGS & VARIANTS ===
RECORDINGS: the left and/or right margins list performers, each with a 2-digit
recording year (e.g. "L.Armstrong 29.38.44- C.Hopkins 34"). Transcribe them as best
you can into "recordings", a list of strings — ONE string per printed margin line, in
top-to-bottom order, exactly as read (keep the names, the years, and separators like
"-" and "/"; a performer may carry several years, e.g. "F.Waller 29.35.38"). If a
name or year is cut off at the crop edge, transcribe the visible part and move on — do
NOT try to reconstruct it (the missing part appears on an adjacent overlapping crop;
that is fine and expected). OMIT "recordings" only when there are truly no credits.

VARIANTS: some tunes print one or more ALTERNATE bars below the grid, labelled VARIANTE
(or STATEMENT) with a bar reference, e.g. "VARIANTE  Bar 1, 9, 25". Each alternate is
tied to specific bar(s) of the main grid, usually via a marker symbol (*, ①, ②, ...)
drawn BOTH next to the target grid bar AND next to the alternate. Transcribe these into
"variants", a list of objects — ONE object per VARIANTE label:
  {{
    "marker": "*",                 // OMIT if none; the symbol tying this to the grid bar(s)
    "applies_to": "Bar 1, 9, 25",  // the printed bar reference, verbatim
    "bars": [ {{ "bar": 1, "beats": {{ "1": "Fm7", "3": "Gm7" }} }}, ... ]
  }}
- "bars" uses the SAME shape and the SAME subdivision/notation/expansion rules as a
  section: one object per printed variant box, "bar" 1-indexed in printed left-to-right
  order, beats read from each box's own regions. These are the replacement chords for
  the referenced grid bar(s); downstream code maps them using "applies_to".
- Keep the main grid UNCHANGED: the original chords stay in "sections", and the marker
  symbol (*, ①, ...) is NEVER written into a chord string — it only links the variant.
- A page may carry SEVERAL variants (each its own marker + reference) — emit one object
  for each, in printed order.
- Some variant boxes may be cut off at the crop edge: transcribe what you can, append
  "?" to any uncertain chord, and add a notation_notes entry. Do not invent chords.
- OMIT "variants" entirely when the page has none.

=== NOTATION NOTES ===
"notation_notes" is a free-form object mapping a short key to an explanation. OMIT if
empty. Record when applicable: the French "14"=#11 convention, the 5+ convention, the
t=+ convention; any omitted parenthesised alterations; any chord marked ? (and why);
any Case-3 inset/diagonal ambiguity and which bar; enharmonic/ambiguous readings;
truncation; composer/performance annotations printed on the score; a "same chord
changes" / cross-reference note (key "same_chord_changes", see below); any other
stray printed text; a missing grid (key "no_chord_grid").

=== SAME CHORD CHANGES ===
Below (or beside) the main grid a tune may carry a free-text note relating its
changes to ANOTHER tune. Capture it verbatim — do not drop it. Two printed forms:
- Labelled: "SAME CHORD CHANGES :" followed by the referenced tune title and often a
  parenthesised attribution, e.g. "SAME CHORD CHANGES : PRINCE ALBERT (K.Dorham)".
- Unlabelled prose: the same idea written out, e.g. 'Almost the same chord changes as
  "I can't believe that you're in love with me"'.
Record the WHOLE line verbatim (keep the referenced title, any parentheses, and the
quote marks) in a notation_notes entry under key "same_chord_changes". This is NOT a
missing grid — the tune still has its own printed changes; transcribe those normally.
GENERAL RULE: any other stray explanatory text printed on the score that is not a
chord, a margin performer/year credit, or an omitted variant/statement marker should
likewise be captured verbatim in a notation_notes entry (pick a short descriptive key).

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
            "recordings": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Margin performer/year recording credits, one string per printed "
                    "line, top-to-bottom, transcribed verbatim. Omit if none."
                ),
            },
            "variants": {
                "type": "array",
                "description": (
                    "Alternate bars printed below the grid (VARIANTE/STATEMENT). One "
                    "object per label. See the system instructions for the full rules."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "marker": {
                            "type": "string",
                            "description": (
                                "Symbol (e.g. *, ①) linking this variant to the grid "
                                "bar(s). Omit if none."
                            ),
                        },
                        "applies_to": {
                            "type": "string",
                            "description": (
                                "The printed bar reference, verbatim (e.g. "
                                '"Bar 1, 9, 25").'
                            ),
                        },
                        "bars": {
                            "type": "array",
                            "description": (
                                "Variant bars in the same shape as a section: "
                                '{"bar": int, "beats": {"1".."4": "<chord>"}}, in '
                                "printed left-to-right order."
                            ),
                        },
                    },
                    "required": ["applies_to", "bars"],
                },
            },
            "notation_notes": {"type": "object"},
        },
        "required": ["style", "form", "time_signature", "sections"],
    },
}
