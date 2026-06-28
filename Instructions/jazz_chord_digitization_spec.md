# Jazz Chord Grid Digitization — Implementation Spec

**Source book:** *Anthologie des grilles de jazz* (handwritten chord grids, ~342 scanned pages)
**Goal:** Convert each tune's handwritten chord grid into a structured JSON file, one file per tune.
**Audience:** An implementing agent that can render PDFs, run image code, and call a vision-language model.

This document is the single source of truth. Where it conflicts with any earlier prompt or the
sample JSON, **this document wins.** (The reference sample was produced by an older model and is
internally inconsistent in its notation; this spec deliberately canonicalizes those inconsistencies.)

---

## 1. Pipeline overview

Tune boundaries cannot be found reliably with simple image heuristics on these pages: the
handwritten chords are as large as the titles, and bold strokes / box borders defeat
whitespace-, size-, and stroke-weight-based detection. Locating a page's 2–3 large titles,
however, is an easy and robust task for the vision model that already has to read the page.

The pipeline therefore has three passes:

```
PASS 1  LOCATE      vision model   page image  ->  title positions per page
PASS 2  PREPROCESS  this codebase  PDF + positions  ->  one cleaned image per tune
PASS 3  TRANSCRIBE  vision model   per-tune crop  ->  one JSON file per tune
```

The interface between Pass 1 and Pass 2 is **fractions of page height** (0.0 = top,
1.0 = bottom). Fractions are scale-invariant, so the DPI used to *locate* and the DPI used to
*crop* do not need to match.

---

## 2. Source material characteristics (read before implementing)

* Each PDF page is a single **1-bit (pure black/white) bitmap**, roughly **200 DPI**
  (~1654 × 2338 px on a US-Letter page). There is no grayscale and no antialiasing.
* **Implication:** upscaling cannot recover detail the scan never captured. The realistic goal
  of preprocessing is to make the *existing* marks easier for the model to read.
* **Do NOT** use AI super-resolution / GAN upscalers on chord content. On thin handwriting they
  hallucinate strokes (7↔9, `b5`↔blob), which silently corrupts data — the worst failure mode here.
* Each tune occupies a full-width horizontal band. Recording credits run vertically in the left
  and right page margins, alongside the tune they belong to. **Omit the recordings**.

---

## 3. PASS 1 — Locate titles (vision model)

### 3.1 Input
One rendered page image per call. ~150–200 DPI is sufficient (titles are large; the only small
text the model must read is the printed page number in the header/footer).

### 3.2 Task given to the model
> You are looking at one scanned page from a book of jazz chord grids. Each page contains one or
> more tunes. Every tune begins with a  title in large capital letters (hand-lettered), followed by smaller
> style/tempo labels and a grid of chord boxes.
>
> Return the printed page number and, for every tune on the page, its title text and the vertical
> position of the **top of the title**, expressed as a fraction of the full page height
> (0.0 = very top, 1.0 = very bottom). List tunes top-to-bottom. Output JSON only.

### 3.3 Output contract (per page)
```json
{
  "pdf_page": 1,
  "page": 340,
  "tunes": [
    { "title": "Riverboat Shuffle", "title_top": 0.05 },
    { "title": "Riverside Blues",   "title_top": 0.40 },
    { "title": "Robbins Nest",      "title_top": 0.78 }
  ]
}
```
* `pdf_page` — 1-based index of the page within the PDF (the agent supplies/derives this).
* `page` — the printed page number read from the scan (used later for file naming).
* `title_top` — fraction of page height at the top edge of the title text.

The agent collects these per-page objects into a single `locations.json` array for Pass 2.

### 3.4 Why this is reliable
Finding 2–3 large titles is far easier than reading chords and is robust even at 200 DPI.
If the model ever returns an implausible result (e.g. zero tunes, or titles out of order),
the agent should flag that page for human review rather than crop it blindly.

---

## 4. PASS 2 — Preprocess & crop (agent implements this)

Implement a script (e.g. `preprocess.py`) with **two deterministic modes**. Suggested dependencies:
`pymupdf` (render), `pillow` (image I/O), `scipy` (morphology; provide a pure-numpy fallback).

### 4.1 Mode A — emit page images for Pass 1
* Render each selected PDF page to a grayscale PNG.
* Apply the cleaning step (§4.3).
* Default render resolution **200 DPI** (small files; model only needs to find titles).
* Name `pdf_page_{index:04d}.png` (1-based). The printed page number is *not* needed here — the
  model reads it in Pass 1.

### 4.2 Mode B — crop per tune from `locations.json`
For each page object in `locations.json`:
1. Render the page at the **crop resolution** (default **600 DPI**) and apply cleaning (§4.3).
2. Sort the tunes by `title_top`.
3. Place a horizontal cut **just above each title except the first**, at
   `y = (title_top − title_margin) × page_height_px`, with `title_margin` default **0.012**
   (≈1.2% of page height). The first tune's band starts at `y = 0`; the last band ends at the
   page bottom. Clamp each cut to be strictly below the previous one.
4. Crop **full page width** for every band (keeps margin credits with their tune).
5. Save each band as `page{PAGE}_tune{N}.png`, where `PAGE` is the printed page number and `N`
   numbers the tunes top-to-bottom starting at 1.
6. Append to a `manifest.json` describing every crop:
   `{ "file", "page", "tune", "title", "slug", "y0", "y1" }` where `slug` is the lowercased,
   hyphenated title.

### 4.3 Cleaning step (both modes)
* Convert to grayscale.
* **Thicken ink** with one iteration of binary dilation of the black pixels (makes 1-bit
  handwriting connect and read better). Make the iteration count a parameter (default 1, allow 0
  to disable or 2 for very thin scans).
* Nothing else. No deskew unless a page is visibly rotated; no super-resolution.

### 4.4 Fallback (manual cuts)
Also provide a manual override that accepts cut fractions per page
(e.g. `"340:0.40,0.77; 341:0.27,0.62"`) and a `--debug` overlay that draws a 0.05 fractional ruler
plus the chosen cut lines. This is only for correcting the rare page the locate pass gets wrong;
it is not the primary path.

---

## 5. PASS 3 — Transcribe (vision model) — output schema

Pass 3 reads **one per-tune crop** and emits **one JSON file** containing a **single bare object**
(not wrapped in an array). Filename: `page{PAGE}_{slug}.json` (e.g. `page342_rockin-in-rhythm.json`),
taken from the manifest.

### 5.1 Object shape
```json
{
  "title": "River Stay Way from My Door",
  "title_uncertain": false,
  "composer": "Harry Woods – Mort Dixon",
  "year": "1931",
  "style": "STANDARD",
  "tempo": "MEDIUM",
  "form": "32 A A B A",
  "time_signature": "4/4",
  "page": 341,
  "source": "Anthologie des grilles de jazz",
  "sections": { "...": [ /* bar objects */ ] },
  "notation_notes": { /* optional */ },
}
```

### 5.2 Field rules
| Field | Rule |
|---|---|
| `title` | As printed. |
| `title_uncertain` | `true` if the title is partly cut off or unreadable; else `false`. Always present. |
| `composer` | Names joined with `" – "` (space–en dash–space). **Omit the field entirely if absent.** |
| `year` | Composition year as a string. **Omit if absent.** |
| `style` | The upper-left genre label exactly as printed (`DIXIELAND`, `NEW ORLEANS`, `SWING`, `STANDARD`, `ELLINGTONIA`, …). Always present. |
| `tempo` | The tempo label (`MEDIUM`, `MEDIUM FAST`, `MEDIUM SLOW`, `FAST`, …). **Omit if absent.** |
| `form` | The form string exactly as printed, **preserving primes** (e.g. `32 A B C A'`). Always present. |
| `time_signature` | Default `"4/4"`; override only if the score indicates otherwise. Always present. |
| `page` | Printed page number (integer). Always present. |
| `source` | Constant `"Anthologie des grilles de jazz"`. Always present. |
| `sections` | See §6. Always present (may be `{}` only for the missing-grid case, §10). |
| `notation_notes` | See §9. **Omit if none.** |

**Optional-field policy:** when an optional field has no content, **omit the key entirely** (do not
emit `null` or `""`). The only constant-but-present field is `source`.

> Note: `fingerprints` (harmonic analysis) are **not** part of the default output — they are the
> least reproducible part of the old pipeline. They are available as an optional module; see
> Appendix A.

---

## 6. Sections, bars, and beats

### 6.1 Section IDs
* Use the labels printed on the score.
* **Letter sections** keep uppercase letters: `A`, `B`, `C`.
* **Repeats of the same letter** get a numeric counter, in order of appearance:
  `A`, `A1`, `A2`, … **Do not use primes in section keys** (primes live only in the `form` string).
* **Named sections** are lowercase words: `intro`, `interlude`, `coda`.
* **Prefixed/compound sections** use a lowercase prefix + uppercase letter:
  `verse_A`, `verse_B`, `clarinet_A`, `clarinet_A1`.
* **One section ID = one printed row.** Sections are **not** forced to 8 bars — an intro,
  interlude, or coda may have fewer (or more). Use whatever the row actually contains.

### 6.2 Form expansion
The printed grid often shows fewer rows than the `form` implies (e.g. `form = "32 A A B A"` but only
an A-row and a B-row are drawn). Expand the form by **copying** the printed rows into the full set
of sections. Example: a printed A-row + B-row with form `32 A A B A` becomes sections
`A`, `A1` (copy of A), `B`, `A2` (copy of A).

### 6.3 Bar representation
Every bar is an object:
```json
{ "bar": 3, "beats": { "1": "Ab", "3": "A°" } }
```
* `bar` — 1-indexed within its section, restarting at 1 each section.
* `beats` — keys are beat-number strings (`"1"`–`"4"`), values are chord strings.
* **A whole-bar chord is still an object:** `{ "bar": 1, "beats": { "1": "Db" } }`.
  (Never emit a bare string for a bar.)

### 6.4 Which beats to encode
Encode a beat **only where a chord visibly begins in that beat's region.** Do not pad held chords
onto later beats. **Exception (important):** if a bar visibly **re-writes** a chord in a later
region — even the same chord again — preserve that repeat. For example a 2×2 box showing `C`
top-left and `C` top-right is encoded `{ "1": "C", "3": "C" }` (or with beat `"2"`/`"4"` per the
layout), because the score restruck it. Transcribe what is written, not what theory would collapse.

---

## 6.5 Bar subdivision layouts

A bar box may be divided. The cases below are the **only** ones; each maps box geometry to beats.
Read each chord (and all its alteration suffixes) **only from within its own region** — see §6.6.

**Case 1 — Undivided, one chord.**
Whole bar. → `{ "1": "Cm7" }`

**Case 2 — Diagonal split OR horizontal-half split.**
A single diagonal line (top-right→bottom-left) *or* a horizontal line splitting the box into an
upper and lower half. **Both encode identically** (upper/upper-left = beats 1+2 → `"1"`;
lower/lower-right = beats 3+4 → `"3"`):
→ `{ "1": "Eb", "3": "Eb7" }`
*(The agent does not need to distinguish diagonal from horizontal-half — they produce the same JSON.)*

**Case 3 — Bottom-right inset square only (no full horizontal divider).**
One chord fills the large undivided area; a small framed square sits in the bottom-right corner.
Large area = beats 1–3 → `"1"`; inset square = beat 4 → `"4"`:
→ `{ "1": "Em7", "4": "Eb°" }`
**Ambiguity fallback:** if scan quality makes the inset-corner indistinguishable from a plain
diagonal, treat it as a **diagonal (Case 2) → beat 3**, and add a `notation_notes` entry recording
the ambiguity for that bar.

**Case 4 — Upper half + lower-left + lower-right.**
Upper = `"1"`; bottom-left = `"3"`; bottom-right = `"4"`:
→ `{ "1": "A", "3": "B", "4": "C" }`

**Case 5 — Upper-left + upper-right + lower half.**
Top-left = `"1"`; top-right = `"2"`; lower = `"3"`:
→ `{ "1": "A", "2": "B", "3": "C" }`

**Case 6 — Four squares (2×2).**
Top-left = `"1"`; top-right = `"2"`; bottom-left = `"3"`; bottom-right = `"4"`:
→ `{ "1": "A", "2": "B", "3": "C", "4": "D" }`

### 6.6 Boundary box rule
When a bar is subdivided, a chord symbol **and all its alteration suffixes** (`b5`, `#5`, `b9`,
`m`, `°`, etc.) must be read only from within that beat's own region. An alteration printed in a
neighboring region belongs to that neighbor, even if it sits visually close to the boundary.
**Never reach across a subdivision line** to attach an alteration to an adjacent beat's chord.
Example: diagonal box with `Am7` upper-left and `b5` lower-right → beat 1 is `Am7`, and the `b5`
belongs to the lower-right chord, not to `Am7`.

---

## 6.7 Repeat and shorthand expansion

The score uses shorthand for repetition. **Always expand fully in the JSON — never output `-`,
`%`, `•/•`, `→`, or any shorthand.** Write the actual chord(s).

| Score mark | Meaning | Action |
|---|---|---|
| Arrow + vertical line between rows (full section repeat) | Repeat the whole section | Copy all bars from the most recent section with the same letter. Any explicitly written bar in the repeated row overrides the copied value at that position. |
| Plain `→` at the start of a row | Repeat the previous row | Copy the previous row of the same section verbatim; explicit bars override. |
| Diagonal spanning **two** adjacent boxes | Two-bar repeat | Copy the immediately preceding two bars into those two bars. (Distinct from a single-box diagonal split, which is Case 2.) |
| `•/•` or similar within one box | Bar repeat | Copy the immediately preceding bar. |
| `-` (dash) in a box | Bar repeat | Copy the immediately preceding bar. |

**Dash exception:** if a `-` is the very first bar of a tune with nothing preceding it, it is a
genuine empty bar — encode `{ "1": "N.C." }` and note it in `notation_notes`. (This is rare.)

---

## 6.8 Chord notation (canonical profile)

All chords are normalized to **one** canonical vocabulary below. This section is the single point of
change if a different house style is later preferred.

**Canonical symbols**
| Quality | Canonical | Example |
|---|---|---|
| Major triad | (root only) | `C` |
| Minor | `m` | `Cm` |
| Dominant 7th | `7` | `G7` |
| Major 7th | `maj7` | `Cmaj7` |
| Minor 7th | `m7` | `Dm7` |
| Half-diminished | `m7b5` | `Am7b5` |
| Diminished | `°` | `C°` |
| Augmented triad | `+` | `Eb+` |
| Augmented dominant | `7#5` | `Eb7#5` |
| Minor–major 7th | `m(maj7)` | `Dm(maj7)` |
| Sixth / ninth etc. | `6`, `9`, `m6`, `9#11`, … | `Ab6`, `Db9` |

**Conversions from the book's notation → canonical**
| In the book | Encode as | Note |
|---|---|---|
| `7M`, `M7`, `Δ` (major 7) | `maj7` | e.g. `Eb7M` → `Ebmaj7` |
| `ø` (half-dim) | `m7b5` | e.g. `Aø` → `Am7b5` |
| superscript `5+` (aug 5th) | `#5` | e.g. `Bb7` with `5+` → `Bb7#5` |
| suffix `t` (means `+`, i.e. raise) | `#` on that degree | `Eb9t` → `Eb#9`; `F75t` → `F7#5` |
| `…/14` (French “14th”) | `#11` | `E9/14` → `E9#11` (7+7=14) |
| alteration in parentheses `(…)` | **omit entirely** | `Bb9(b9)` → `Bb9`; `D9(b5)` → `D9` |

**Other rules**
* Watch `B` vs `Bb` carefully — they are different chords.
* If a chord is uncertain due to scan quality, append `?` to that chord string, e.g. `Bbmaj7?`,
  and add a `notation_notes` entry.

---

## 7. Recordings

The margins list performers and 2-digit years. Omit them. Do not digitize.

---

## 8. Variants (asterisk / VARIANTE footnotes)

Some bars are marked with `*` (or labeled VARIANTE / STATEMENT). Omit it and the corresponding footnote. Do not digitize.

---

## 9. Notation notes

A free-form object mapping a short key to an explanation. Use it to record anything a downstream
reader needs to interpret the data. **Omit the field if empty.** Always note, when applicable:
* the French “14” = `#11` convention, the `5+` convention, the `t` = `+` convention;
* any omitted parenthesised alterations;
* any chord marked `?` (and why);
* any Case-3 inset/diagonal **ambiguity** (§6.5) and which bar it affects;
* enharmonic spellings or other ambiguous readings;
* truncation (e.g. only part of a tune visible on the page);
* composer/performance annotations printed on the score;
* a missing chord grid (key `no_chord_grid`, see §10).

---

## 10. Missing chord grid / cross-references

Some tunes print no grid (e.g. they point to another tune's changes).
* Set `"sections": {}`.
* Add a `notation_notes` entry under key `no_chord_grid`, e.g.
  `"No chord grid printed. Form indicated as <label>. Chords must be inferred from the standard form."`
* **Do not invent chord content.**
* If the page cross-references another tune (`→ IT'S EASY TO REMEMBER` or “same changes as X”),
  record that target in the `no_chord_grid` note.

---

## 11. Output layout

```
pages/        pdf_page_0001.png …            (Pass 1 input images)
locations.json                               (Pass 1 output, all pages)
crops/        page340_tune1.png …            (Pass 2 output)
              manifest.json
tunes/        page340_riverboat-shuffle.json (Pass 3 output, one per tune)
              page340_riverside-blues.json
              …
```

---

## 12. Worked example (abbreviated)

Crop `page341_tune1.png` shows **River Stay Way from My Door**, `STANDARD / MEDIUM`,
form `32 A A B A`, with an A-row, a B-row, and second-ending markings. Expected output
`page341_river-stay-way-from-my-door.json` (sections abbreviated):

```json
{
  "title": "River Stay Way from My Door",
  "title_uncertain": false,
  "composer": "Harry Woods – Mort Dixon",
  "year": "1931",
  "style": "STANDARD",
  "tempo": "MEDIUM",
  "form": "32 A A B A",
  "time_signature": "4/4",
  "page": 341,
  "source": "Anthologie des grilles de jazz",
  "sections": {
    "A": [
      { "bar": 1, "beats": { "1": "Eb" } },
      { "bar": 2, "beats": { "1": "Eb", "3": "Eb7" } },
      { "bar": 3, "beats": { "1": "Ab", "3": "A°" } },
      { "bar": 4, "beats": { "1": "Eb" } },
      { "bar": 5, "beats": { "1": "F7" } },
      { "bar": 6, "beats": { "1": "B7", "3": "Bb7" } },
      { "bar": 7, "beats": { "1": "Eb" } },
      { "bar": 8, "beats": { "1": "Fm7", "3": "Bb7" } }
    ],
    "A1": [ "...copy of A, with the printed second-ending bars overriding bars 7-8..." ],
    "B":  [ "..." ],
    "A2": [ "...copy of A..." ]
  }
}
```
Note: bar 2 is a Case-2 diagonal (`Eb` / `Eb7`); bar 3 is a Case-2 split (`Ab` / `A°`); bar 6's
`B7`/`Bb7` must be read with care (§6.8 — `B` vs `Bb`).

---

## Appendix A — Optional fingerprints module (off by default)

If harmonic analysis is explicitly enabled for a run, an optional `fingerprints` array may be
added. Keep it small (≤3 per tune) and ensure every referenced chord actually appears in the
transcription (fingerprints describe transcribed data; they must never introduce new chords).

```json
{
  "id": "river-stay-passing-dim",
  "label": "Chromatic passing diminished",
  "location": { "section": "A", "bars": [3] },
  "chords": ["Ab", "A°"],
  "description": "Ab → A° passing diminished connecting IV to the returning tonic."
}
```
Patterns to look for: ii–V–I, minor ii–V–i, tritone subs, chromatic passing diminished,
backdoor dominants (bVII7), secondary dominants, turnarounds, pedal/modal vamps,
cycle-of-fifths dominant chains.

Because these are interpretive and reduce run-to-run reproducibility, they are **excluded from the
default schema.** Do not emit `fingerprints` unless the run is explicitly configured for analysis.

---

## Appendix B — Self-check before writing each tune file

1. Is the output a single bare object (not an array)?
2. Are all always-present fields there (`title`, `title_uncertain`, `style`, `form`,
   `time_signature`, `page`, `source`, `sections`)?
3. Are absent optional fields **omitted** (not `null`)?
4. Is every bar an object with a `bar` index and a `beats` map (no bare strings)?
5. Are all repeats/dashes/`%`/arrows **expanded** to explicit chords?
6. Do section keys use counters (`A`, `A1`, `A2`), never primes?
7. Are all chords in the canonical vocabulary (§6.8), with `?` on uncertain ones?
8. Were alterations read within their own beat region only (§6.6)?
9. Are Case-3 inset (beat 4) vs diagonal (beat 3) calls correct, with ambiguities noted?
10. Is `fingerprints` absent (unless analysis was explicitly enabled)?
