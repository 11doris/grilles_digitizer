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
  "same_chord_changes": str,      // OMIT if absent; verbatim cross-reference line; see SAME CHORD CHANGES
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
- form: exactly as printed, preserving primes (e.g. "32 A B C A'"). A standalone
  verse-length label such as "12 VERSE" / "4 VERSE" / "10 VERSE" (the tune's verse,
  usually not itself transcribed) is NOT part of the form — do NOT append it to the
  form string; record it in a notation_notes entry (key "verse").
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
the form by COPYING the printed rows into the full set of sections. Map each printed
row to the NEXT DISTINCT letter of the form, in top-to-bottom order (1st printed row =
1st letter; 2nd printed row = the next NEW letter; ...). Repeated letters (A1, A2, ...)
are COPIES, never their own printed row. Example: a printed A-row + B-row with form
"32 A A B A" is exactly TWO rows — the A-row and the B-row — and becomes sections A,
A1 (copy of A), B, A2 (copy of A). So the SECOND printed row here is B, NOT A1. Any
explicitly written bar in a repeated row overrides the copied value.

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
Every bar is an object: {{ "bar": 3, "beats": {{ "1": "Ab", "3": "Ao7" }} }}
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
1. Undivided, one chord: whole bar -> {{ "1": "Cm7" }}. An undivided box is exactly ONE
   whole-bar chord on beat 1 — do NOT split it into two beats. Only subdivide when a
   visible divider line or separate framed region is actually drawn; never invent a
   second chord (e.g. a phantom "1":"C","3":"C7" or "1":"Bbmaj7","3":"Bbm6") in a box
   that shows a single symbol.
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
   "1"; the small framed corner square is beat 4 -> "4": {{ "1": "Em7", "4": "Ebo7" }}
   AMBIGUITY FALLBACK: if scan quality makes the inset corner indistinguishable from
   a plain diagonal, treat it as a diagonal (Case 2) -> beat 3, and add a
   notation_notes entry for that bar.
   TOP-LEFT inset (mirror of case 3): a small framed square inset in the TOP-LEFT
   corner + a large remaining area (no full divider). The corner square is beat "1";
   the large area is the NEXT beat "2": {{ "1": "Fm7", "2": "Bb7" }}. (If the top is
   instead split into TWO framed squares side by side above a lower area, that is
   case 5, not this.)
4. Upper half + lower-left + lower-right: upper -> "1"; bottom-left -> "3";
   bottom-right -> "4": {{ "1": "A", "3": "B", "4": "C" }}
5. Upper-left + upper-right + lower half: top-left -> "1"; top-right -> "2";
   lower -> "3": {{ "1": "A", "2": "B", "3": "C" }}
6. Four squares (2x2): top-left "1", top-right "2", bottom-left "3", bottom-right "4".

BOUNDARY BOX RULE: when a bar is subdivided, a chord symbol AND all its alteration
suffixes (b5, #5, b9, m, o7, etc.) must be read only from within that beat's own
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
  - EXCEPTION — trailing dash: once the arrow row has started writing EXPLICIT bars, a
    dash that sits to the RIGHT of an explicit box (i.e. the row's copied span is
    already over) reverts to an ordinary bar-repeat: copy the immediately preceding
    (explicit) bar, NOT the positional same-letter reference. Typical case: a final
    dash after an explicit turnaround copies that turnaround's last bar.
  - The referenced row is that same-letter row AS ALREADY RESOLVED — its own copies and
    overrides carried through — NOT necessarily the original first A. So if A1 itself
    overrode, say, bars 7-8, then A2's empty bars 7-8 inherit A1's OVERRIDDEN bars 7-8.
    Only the boxes A2 writes explicitly differ from A1.
- Diagonal spanning TWO adjacent boxes (two-bar repeat): copy the immediately
  preceding two bars into those two bars.
- TWO-BAR REPEAT SIGN (a bold double slash / double line with two dots — one dot above
  and one below, or one on each side — drawn STRADDLING the barline between a PAIR of
  bars; the multi-measure repeat mark): it fills BOTH of those bars by copying the
  immediately preceding TWO bars — bar n-2 into the first, bar n-1 into the second, NOT
  the same single bar twice. Typical case: bars 3-4 restate bars 1-2. The sign occupies
  two whole bars, so never mistake it for a single-box bar-repeat.
- •/• or similar within one box (bar repeat): copy the immediately preceding bar.
- - (dash) in a box (bar repeat): copy the immediately preceding bar. THIS APPLIES
  ONLY OUTSIDE a left-arrow row; inside a left-arrow row a dash is the positional
  placeholder described above, never a repeat of the preceding bar.
DASH EXCEPTION: if a - is the very first bar of a tune with nothing preceding it, it
is a genuine empty bar — encode {{ "1": "N.C." }}.

=== CHORD NOTATION (canonical vocabulary) ===
Major triad -> root only (C). Minor -> m (Cm). Dominant 7th -> 7 (G7).
Major 7th -> maj7 (Cmaj7). Minor 7th -> m7 (Dm7). Half-diminished -> m7b5 (Am7b5).
Diminished -> o7 (Co7); o7 covers BOTH the diminished triad and the diminished 7th — always
write just o7. A small hand-drawn circle "°" (often a raised, unfilled loop after the
root) is DIMINISHED, not a 7: read "G#" followed by a little circle as G#o7, NEVER G#7.
Augmented triad -> (#5) (Eb(#5)). Augmented dominant -> 7#5 (Eb7#5).
Minor-major 7th -> m(maj7) (Dm(maj7)).

SYMBOL ORDER: build every chord in this fixed order, no spaces:
  ROOT -> quality (m / maj7 / o7 / m7b5 / sus4 ...) -> highest extension number
  (6, 7, 9, 11, 13) -> alteration suffixes in ascending-degree order (b5 #5 b9 #9 #11 b13).
  Examples: Dm7, Ebmaj7, C13, Ab7(13), F7#5, B7#11, C9b5, Eb7#5b9, Gm11.
  A number NEVER sits between the root and the quality: a minor 7th is Gm7, never
  "G6m7"; if you seem to see a digit wedged there it is a misread — drop it. Every
  chord string you emit must follow this order; malformed spellings are always wrong.

EXTENSIONS (bare numbers): 6, 9, 11, 13 and their minor/major forms m6, m9, m11, m13,
maj9. A single bare extension number implies the chord tones below it, so write the
HIGHEST number only: a dominant simply marked as a 13th is C13; an 11th is C11; a ninth
is C9. 6/9 chord -> 6/9 (C6/9, Cm6/9). This collapse applies ONLY to a plain bare number:
a 7 or 9 printed WITH a parenthesised superscript extension is the exception below — keep
it literal (Ab7(13), D9(13)), NEVER collapse it to Ab13 / D13.
PARENTHESISED SUPERSCRIPT EXTENSION — preserve as printed: when the book writes an
explicit 7 or 9 with a FURTHER extension drawn as a parenthesised superscript beside it
(e.g. a "7" with a superscript "(13)", or a "9" with "(13)"), keep that literal form:
Ab7(13), D9(13). Do NOT collapse these to Ab13 / D13 — the parentheses are part of the
printed symbol. (This applies only to a parenthesised superscript number; a plain bare
13 still collapses per the rule above.)

ALTERATIONS & PARENTHESES: an alteration is an accidental degree (b5, #5, b9, #9, #11,
b13, ...). Attach it BARE, to the right of the chord, WHENEVER a 7th or an extension
number is present: F7#5, Ab7b9, C9b5, C9#5, C13#5, B7#11, Eb7#5b9. Wrap it in parentheses ONLY on a
bare triad that has NO 7th/extension: F(#5) [= augmented triad], Ab(b9), A(#5#9). Rule of
thumb: 7th or number present -> no parens; bare triad -> parens. NEVER add a 7th or
extension the box does not print, even when one is theoretically implied by the
alteration: a bare root carrying only a b9 is F(b9), NOT F7b9; a bare root with a #5 is
F(#5), NOT F7#5. Transcribe the quality actually written, not the chord it suggests.

READING SUPERSCRIPT ACCIDENTALS (the (b9) traps — read these carefully):
Alterations and extensions are hand-drawn as small SUPERSCRIPTS to the upper-right of
the chord root (e.g. a bare "A" with a tiny "b9" above-right of it). A hand-drawn FLAT
sign (b / ♭) in such a superscript is a tall loop EASILY CONFUSED with the digit 6, a
lowercase b, or a lowercase g; and a hand-drawn 9 is a loop with a tail EASILY CONFUSED
with a lowercase g. This causes recurring mistakes — DO NOT make them:
- DO NOT drop the flat: a triad with a superscript flat-9 is (b9), NEVER a plain 9.
  ("A" with superscript ♭9 -> A(b9), not A9.)
- DO NOT read the flat as a 6 (nor read a genuine 6 with a stray flat): a superscript
  that reads like "b9", "9b", or "96" beside a triad is almost always a FLAT-NINE ->
  (b9), NOT a 6. ("D" ♭9 -> D(b9), not Db6 / D6; "C" ♭9 -> C(b9), not C6b.)
- DO NOT read a 9 (or a flat) as the letter "g": a superscript scrawl that looks like
  "g" is a digit 9 or a flat, NEVER a literal letter g. "Eb" with a superscript loop is
  Eb9, not "Ebg"; "C" with a ♭9 scrawl is C(b9), not "Cgb".
- DO NOT invent a b5: a superscript flat-nine on a triad reads as "(b9)", NOT "9b5". Do
  not add a b5 the box does not print. "Bb" ♭9 -> Bb(b9), never Bb9b5.
- DO NOT migrate a superscript accidental onto the ROOT. The root's own accidental (the
  flat in Bb, Eb; the sharp in F#) is written INLINE, at the SAME large size as the root
  letter, immediately after it. A small RAISED flat OR sharp is an ALTERATION of the
  chord, not part of the root name. So "G" with a superscript ♭9 is G(b9), NEVER Gb9;
  "D" with a superscript ♭9 is D(b9), NEVER Db; a "G" with a superscript ♯ (e.g. 9#11)
  is G9#11, NEVER G#9#11 — do not sharpen the root from a raised alteration.
Net rule: a bare triad carrying a superscript flat-nine is the parenthesised form
C(b9), D(b9), G(b9), A(b9), F#(b9). The ONLY correct spelling is "(b9)" — never "9b",
"9b5", "b9" fused to the root, "gb", "cgb", "g", or "6". Distinguish a real 6 chord
(C6, Bb6 — a plain 6 at normal size, NO flat stroke) from a (b9) (a 9 preceded/
accompanied by a flat stroke). If such a bare-triad (b9) chord is ITSELF wholly enclosed
in parentheses as an optional chord, keep both sets: "(A(b9))".
Apply this same superscript-reading care inside VARIANTE boxes, not just the main grid.

SUS / SLASH / NO-CHORD:
- Suspended: sus4, sus2, 7sus4 (Gsus4, D7sus4). A printed bare "sus" with no number
  means sus4 — keep any printed extension: C9 with "sus" -> C9sus4, never "C9sus".
- Slash / bass note: root then "/" then bass note, as printed (C/E, Fm7/Bb).
- An empty / no-chord bar is N.C.
- A WHOLE chord printed in parentheses — e.g. "(G7)" — is an optional/passing chord:
  KEEP it exactly as printed, parentheses included ("(G7)"), as that bar's chord. You
  may also record it in a notation_notes entry. (Bare-triad alterations also use
  parentheses — F(#5), D(b9), Bb(#5b9) — so parentheses appear in output both ways.)

CONVERSIONS from the book to canonical:
- 7M, M7, Δ (major 7) -> maj7  (Eb7M -> Ebmaj7)
- ø (half-dim) -> m7b5  (Aø -> Am7b5)
- superscript 5+ (aug 5th) -> #5  (Bb7 with a 5+ -> Bb7#5; a Bb triad with a 5+ -> Bb(#5))
- suffix t (means +, i.e. raise that degree) -> # on that degree  (Eb9t -> Eb#9; an F7 with a 5t -> F7#5)
- .../14 (French "14th") -> #11  (E9/14 -> E9#11, since 7+7=14)
- APPLY every conversion above INSIDE the chord string, even when the whole chord is
  parenthesised (an optional/substitute chord): a parenthesised Gm7 carrying a 5+ (aug
  5th) superscript is (Gm7#5), NOT (Gm7) with the alteration dropped into a note. A
  superscript alteration is part of the chord — convert and attach it, never defer it.
- These conversions apply EVERYWHERE a chord is written — the main grid, optional/
  parenthesised chords, AND variant/statement boxes alike. In particular 7M / M7 / Δ
  ALWAYS becomes maj7 inside a VARIANTE box too (Bb7M -> Bbmaj7, C7M -> Cmaj7); never
  leave 7M unconverted just because it sits in a variant.
OTHER: Transcribe the root exactly as printed — do NOT normalise enharmonics (if the box
says F#, write F#; if it says Gb, write Gb). Watch B vs Bb carefully — different chords.
If a chord is uncertain due to scan quality, append ? to that chord string (e.g. Bbmaj7?).

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
drawn BOTH next to the target grid bar AND next to the alternate — use it to read WHICH
bars the alternate applies to, but do NOT output the symbol itself. Transcribe these into
"variants", a list of objects — ONE object per VARIANTE label:
  {{
    "applies_to": "Bar 1, 9, 25",  // the printed bar reference, verbatim
    "bars": [ {{ "bar": 1, "beats": {{ "1": "Fm7", "3": "Gm7" }} }}, ... ]
  }}
- "bars" uses the SAME shape and the SAME subdivision/notation/expansion rules as a
  section: one object per printed variant box, "bar" 1-indexed in printed left-to-right
  order, beats read from each box's own regions. These are the replacement chords for
  the referenced grid bar(s); downstream code maps them using "applies_to".
- Keep the main grid UNCHANGED: the original chords stay in "sections", and the marker
  symbol (*, ①, ...) is NEVER written into a chord string nor emitted as a field — it
  only tells you which grid bar an alternate belongs to.
- A page may carry SEVERAL variants (each its own bar reference) — emit one object
  for each, in printed order.
- Some variant boxes may be cut off at the crop edge: transcribe what you can, append
  "?" to any uncertain chord, and add a notation_notes entry. Do not invent chords.
- OMIT "variants" entirely when the page has none.

=== NOTATION NOTES ===
"notation_notes" is a free-form object mapping a short key to an explanation. OMIT if
empty. Record when applicable: enharmonic/ambiguous readings;
truncation; composer/performance annotations printed on the score; any other
stray printed text; a missing grid (key "no_chord_grid"). Do NOT put the SAME CHORD
CHANGES cross-reference here — it has its own top-level "same_chord_changes" field (see below).

=== SAME CHORD CHANGES ===
Below (or beside) the main grid a tune may carry a free-text note relating its
changes to ANOTHER tune. Capture it verbatim — do not drop it. Two printed forms:
- Labelled: "SAME CHORD CHANGES :" followed by the referenced tune title and often a
  parenthesised attribution, e.g. "SAME CHORD CHANGES : PRINCE ALBERT (K.Dorham)".
- Unlabelled prose: the same idea written out, e.g. 'Almost the same chord changes as
  "I can't believe that you're in love with me"'.
DO NOT repeat the "SAME CHORD CHANGES :" label inside the value — the field name already
says that. For the LABELLED form, strip the leading "SAME CHORD CHANGES :" (and any
surrounding whitespace/colon) and record ONLY what follows: the referenced title plus
any parenthesised attribution, e.g. value "PRINCE ALBERT (K.Dorham)" or
"BALLADE (C.Parker, C.Hawkins )" — NOT "SAME CHORD CHANGES : PRINCE ALBERT (K.Dorham)".
For the UNLABELLED prose form (which has no such label) record the whole sentence
verbatim. Either way keep the referenced title, any parentheses, and the quote marks.
Put it in the top-level "same_chord_changes" string field (NOT inside notation_notes).
OMIT the field entirely when there is no such line. This is NOT a missing grid — the
tune still has its own printed changes; transcribe those normally.
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
            "same_chord_changes": {
                "type": "string",
                "description": (
                    "Cross-reference relating this tune's changes to another tune. Do "
                    "NOT repeat the 'SAME CHORD CHANGES :' label in the value: strip that "
                    "label and record only the referenced title plus any parenthesised "
                    "attribution (e.g. 'PRINCE ALBERT (K.Dorham)'). For an unlabelled prose "
                    "note, record the whole sentence verbatim. Keep the referenced title, "
                    "parentheses, and quote marks. Omit if absent."
                ),
            },
            "notation_notes": {"type": "object"},
        },
        "required": ["style", "form", "time_signature", "sections"],
    },
}
