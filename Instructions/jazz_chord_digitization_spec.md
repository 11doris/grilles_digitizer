# Jazz Chord Grid Transcription — Implementation Spec (transcription only)

**Source book:** *Anthologie des grilles de jazz* (handwritten chord grids)
**Goal:** Convert each **already-cropped per-tune image** into one structured JSON file.
**Scale:** ~**1600 tunes**, each exported as a PNG. The run must be batchable, resumable, and
parallelizable.
**Audience:** An implementing agent that can run image code and call a vision-language model (VLM).

This document is the single source of truth. Where it conflicts with any earlier prompt or the
sample JSON, **this document wins.** (The reference sample was produced by an older model and is
internally inconsistent in its notation; this spec deliberately canonicalizes those inconsistencies.)

> **Scope change vs. the previous spec.** Tune **locating and cropping are already done** and are
> out of scope here. The earlier "Pass 1 LOCATE" and "Pass 2 PREPROCESS/CROP" passes have been
> **removed**. The input to this spec is the finished set of per-tune PNGs plus their manifest.
> What remains is a single transcription pass wrapped in a batch orchestrator.

---

## 1. Pipeline overview

```
INPUT   crops/*.png  +  manifest.csv         (produced upstream; not our concern)
   |
   v
TRANSCRIBE   VLM, one call per crop   ->   one JSON file per tune
   |
   v
VALIDATE     schema + self-check      ->   accept | retry | flag for review
   |
   v
OUTPUT   tunes/*.json  +  run_report.json
```

One **work unit = one cropped PNG → one JSON file.** Units are fully independent, which is what
makes the run trivially parallelizable and resumable.

---

## 2. Input contract

The upstream crop step delivers two things into a working directory:

### 2.1 The crops
A directory `crops/` of per-tune PNGs. Each image is **one tune**, **full page width**, at the
scan's native resolution. The filename stem encodes the printed page number and the title slug,
e.g. `341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.png`.

### 2.2 The manifest
A `manifest.csv` with one row per crop. Relevant columns (others may be present and ignored):

| Column | Use |
|---|---|
| `current_file` | The PNG filename inside `crops/`. **This is the work-unit key.** |
| `page` | Printed page number (integer). Used for the JSON `page` field and naming. |
| `title` | Canonical title (from the book index). Seeds the JSON `title` field. |
| `review` | `yes` if the upstream step was unsure about the title. |
| `conf` | Upstream title-match confidence (0–1). Low = treat title as tentative. |

**Title handling.** The manifest `title` is authoritative for spelling. The VLM must still read the
image and set `title_uncertain` (see §6.2): if `review == yes` **or** `conf < 0.5`, default
`title_uncertain` to `true` unless the crop clearly shows the title.

The agent must **not** re-crop, re-locate, or modify the PNGs. If a crop is obviously wrong (e.g.
two titles visible, or no tune), do not fix it — record it in the run report (§4.7) for the human.

---

## 3. Source material characteristics (read before implementing)

* Each crop comes from a **1-bit (pure black/white) bitmap** scan, rendered to a grayscale PNG.
  Width is commonly **~1654 px (≈200 DPI)** but may be larger (e.g. **~2481 px**) for other scans.
  There is no true grayscale and no antialiasing in the source marks.
* **Implication:** upscaling cannot recover detail the scan never captured.
* **Do NOT** use AI super-resolution / GAN upscalers on chord content. On thin handwriting they
  hallucinate strokes (7↔9, `b5`↔blob), which silently corrupts data — the worst failure mode here.
* **Optional pre-read cleaning (per crop, deterministic):** the agent **may** thicken ink with a
  single binary dilation of the black pixels before sending the image to the VLM (handwriting
  connects and reads better). Make it a parameter: default **1** iteration, allow **0** to disable
  or **2** for very thin scans. Nothing else — no deskew unless a crop is visibly rotated, no
  super-resolution.
* Recording credits run vertically in the left and/or right margins of each crop. **Omit the
  recordings** (§13).

---

## 4. Batch orchestration (this is what makes 1600 tunes tractable)

Implement a runner (e.g. `transcribe.py`) around the per-tune VLM call. It must satisfy all of
the following.

### 4.1 Work discovery
Read `manifest.csv`; the work list is its rows (one per `current_file`). Process in a stable order
(manifest order). Support selecting a subset (§4.4).

### 4.2 Idempotency / resume (required)
For each unit, the output path is deterministic (§7). **Skip a unit if its JSON already exists and
passes validation (§9).** This makes the run safely re-entrant: re-running the same command
continues from where it stopped after a crash, cancellation, or partial run. Never depend on
in-memory state to know what is done — the presence of a valid output file is the only source of
truth.

### 4.3 Concurrency (required)
Process units through a bounded worker pool (default **8** concurrent VLM calls; configurable, e.g.
`--workers N`). Units are independent, so this is embarrassingly parallel. Respect the model
provider's rate limits: on HTTP 429 / 5xx, retry with **exponential backoff + jitter** (e.g.
1s, 2s, 4s, 8s, capped), and cap total in-flight requests.

### 4.4 Sharding (required for multi-machine runs)
Support splitting the work so several processes/machines can run disjoint subsets:
* `--page-range A:B` — only tunes whose `page` is in `[A, B]`.
* `--shard k/N` — process unit *i* iff `i mod N == k`.
Because units are idempotent and write distinct files, shards can run fully independently and even
overlap without corruption (last writer wins on an identical result).

### 4.5 Per-unit retries (required)
The VLM may return invalid JSON or fail validation. For each unit, retry up to **R** times
(default **3**) with a progressively stricter reminder appended to the prompt
("Return one bare JSON object only; no prose; valid JSON."). If still failing after R attempts,
**do not abort the run** — write a stub `tunes/<stem>.error.json`
(`{ "current_file", "page", "title", "attempts", "last_error", "raw_excerpt" }`) and continue.

### 4.6 Error isolation & checkpointing (required)
A single failing unit must never stop the batch. Wrap each unit in try/except; on unexpected
exceptions, log and move on. Write progress incrementally: append one line per finished unit to
`run_state.jsonl` (`{ "current_file", "status", "attempts", "ts" }`) so the run is auditable and
resumable even if the process is killed mid-batch. Flush after each unit.

### 4.7 Determinism & reproducibility
Call the VLM with **temperature 0** (or the provider's most deterministic setting). VLMs are still
not bit-reproducible; that is acceptable. Do not enable the optional fingerprints module (Appendix
A) for production runs — it reduces reproducibility.

### 4.8 Progress logging (required)
Emit a timestamped, flushed status line per unit:
`[ 42% | 672/1600 ] page 341  ROCK-A-BYE...  -> ok (1 attempt)`.
Include a startup banner (counts, options, model name) and a final summary.

### 4.9 Final report (required)
On completion write `run_report.json` summarizing the run and listing everything a human should look
at, **without re-reading every file**:
```json
{
  "total": 1600, "succeeded": 1588, "failed": 12,
  "elapsed_s": 5123, "model": "<model-id>",
  "flagged": {
    "title_uncertain": ["page9_...png", "..."],
    "no_chord_grid":   ["page7_after-hours.png", "..."],
    "low_conf_title":  ["..."],
    "errors":          ["page123_....png", "..."]
  }
}
```
Flag a tune for human review when any of: `title_uncertain == true`; a `notation_notes.no_chord_grid`
is present; the manifest `review == yes` or `conf < 0.5`; or any chord carries a `?`.

### 4.10 Throughput note
1600 independent calls at 8 workers is routine. Plan for retries and rate limits, not raw call
count. Cost/time scale linearly with tunes; sharding (§4.4) scales horizontally.

---

## 5. Transcription task (given to the VLM, once per crop)

Send the (optionally ink-thickened) crop plus this instruction. Provide the manifest `title` and
`page` as context so the model anchors on the correct spelling/number, but instruct it to transcribe
only what the image shows.

> You are transcribing **one** handwritten jazz chord grid (one tune) from a scanned French jazz
> "grilles" book. The image shows a large hand-lettered **title**, smaller style/tempo/form labels,
> and a grid of **chord boxes** organized into rows (sections). Recording credits may run vertically
> in the margins — **ignore them**.
>
> Produce **one bare JSON object** (no array, no prose, no markdown fence) following the schema and
> rules you have been given. Expand every repeat/shorthand into explicit chords. Use the canonical
> chord vocabulary. Read each chord and its alterations only from within its own box region. If a
> mark is ambiguous due to scan quality, make your best reading, append `?` to that chord, and add a
> `notation_notes` entry. The provided title is `"<manifest.title>"` and printed page is
> `<manifest.page>`; use them unless the image clearly contradicts them.

---

## 6. Output schema

Each unit emits **one JSON file** containing a **single bare object** (not wrapped in an array).

### 6.1 Object shape
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
  "notation_notes": { /* optional */ }
}
```

### 6.2 Field rules
| Field | Rule |
|---|---|
| `title` | As printed (seed from manifest `title`; correct only if the image clearly differs). |
| `title_uncertain` | `true` if the title is partly cut off/unreadable, **or** manifest `review == yes` / `conf < 0.5` and the crop does not clearly show it; else `false`. Always present. |
| `composer` | Names joined with `" – "` (space–en dash–space). **Omit the field entirely if absent.** |
| `year` | Composition year as a string. **Omit if absent.** |
| `style` | The upper-left genre label exactly as printed (`DIXIELAND`, `NEW ORLEANS`, `SWING`, `STANDARD`, `ELLINGTONIA`, …). Always present. |
| `tempo` | The tempo label (`MEDIUM`, `MEDIUM FAST`, `MEDIUM SLOW`, `FAST`, …). **Omit if absent.** |
| `form` | The form string exactly as printed, **preserving primes** (e.g. `32 A B C A'`). Always present. |
| `time_signature` | Default `"4/4"`; override only if the score indicates otherwise. Always present. |
| `page` | Printed page number (integer; from manifest `page`). Always present. |
| `source` | Constant `"Anthologie des grilles de jazz"`. Always present. |
| `sections` | See §7. Always present (may be `{}` only for the missing-grid case, §14). |
| `notation_notes` | See §13. **Omit if none.** |

**Optional-field policy:** when an optional field has no content, **omit the key entirely** (do not
emit `null` or `""`). The only constant-but-present field is `source`.

> `fingerprints` (harmonic analysis) are **not** part of the default output. They are an optional
> module; see Appendix A. Do not emit them in production runs.

---

## 7. Output naming & layout

* Output filename mirrors the input PNG stem with a `.json` extension:
  `crops/341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.png` → `tunes/341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.json`.
  This guarantees a 1:1 mapping, avoids slug recomputation, and inherits the upstream de-duplication
  (no collisions).
* Error stubs: `tunes/<stem>.error.json` (§4.5).

```
crops/        341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.png …   (input)
manifest.csv                                                       (input)
tunes/        341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.json …  (output, one per tune)
run_state.jsonl                                                    (resume log)
run_report.json                                                    (final summary)
```

---

## 8. Sections, bars, and beats

### 8.1 Section IDs
* Use the labels printed on the score.
* **Letter sections** keep uppercase letters: `A`, `B`, `C`.
* **Repeats of the same letter** get a numeric counter, in order of appearance: `A`, `A1`, `A2`, …
  **Do not use primes in section keys** (primes live only in the `form` string).
* **Named sections** are lowercase words: `intro`, `interlude`, `coda`.
* **Prefixed/compound sections** use a lowercase prefix + uppercase letter:
  `verse_A`, `verse_B`, `clarinet_A`, `clarinet_A1`.
* **One section ID = one printed row.** Sections are **not** forced to 8 bars — an intro,
  interlude, or coda may have fewer (or more). Use whatever the row actually contains.

### 8.2 Form expansion
The printed grid often shows fewer rows than the `form` implies (e.g. `form = "32 A A B A"` but only
an A-row and a B-row are drawn). Expand the form by **copying** the printed rows into the full set
of sections. Example: a printed A-row + B-row with form `32 A A B A` becomes sections
`A`, `A1` (copy of A), `B`, `A2` (copy of A).

### 8.3 Bar representation
Every bar is an object:
```json
{ "bar": 3, "beats": { "1": "Ab", "3": "A°" } }
```
* `bar` — 1-indexed within its section, restarting at 1 each section.
* `beats` — keys are beat-number strings (`"1"`–`"4"`), values are chord strings.
* **A whole-bar chord is still an object:** `{ "bar": 1, "beats": { "1": "Db" } }`.
  (Never emit a bare string for a bar.)

### 8.4 Which beats to encode
Encode a beat **only where a chord visibly begins in that beat's region.** Do not pad held chords
onto later beats. **Exception (important):** if a bar visibly **re-writes** a chord in a later
region — even the same chord again — preserve that repeat. For example a 2×2 box showing `C`
top-left and `C` top-right is encoded `{ "1": "C", "3": "C" }` (or with beat `"2"`/`"4"` per the
layout), because the score restruck it. Transcribe what is written, not what theory would collapse.

---

## 9. Bar subdivision layouts

A bar box may be divided. The cases below are the **only** ones; each maps box geometry to beats.
Read each chord (and all its alteration suffixes) **only from within its own region** — see §10.

**Case 1 — Undivided, one chord.** Whole bar → `{ "1": "Cm7" }`

**Case 2 — Diagonal split OR horizontal-half split.**
A single diagonal line (top-right→bottom-left) *or* a horizontal line splitting the box into an
upper and lower half. **Both encode identically** (upper/upper-left = beats 1+2 → `"1"`;
lower/lower-right = beats 3+4 → `"3"`): → `{ "1": "Eb", "3": "Eb7" }`
*(No need to distinguish diagonal from horizontal-half — they produce the same JSON.)*

**Case 3 — Bottom-right inset square only (no full horizontal divider).**
One chord fills the large undivided area; a small framed square sits in the bottom-right corner.
Large area = beats 1–3 → `"1"`; inset square = beat 4 → `"4"`: → `{ "1": "Em7", "4": "Eb°" }`
**Ambiguity fallback:** if scan quality makes the inset-corner indistinguishable from a plain
diagonal, treat it as a **diagonal (Case 2) → beat 3**, and add a `notation_notes` entry recording
the ambiguity for that bar.

**Case 4 — Upper half + lower-left + lower-right.**
Upper = `"1"`; bottom-left = `"3"`; bottom-right = `"4"`: → `{ "1": "A", "3": "B", "4": "C" }`

**Case 5 — Upper-left + upper-right + lower half.**
Top-left = `"1"`; top-right = `"2"`; lower = `"3"`: → `{ "1": "A", "2": "B", "3": "C" }`

**Case 6 — Four squares (2×2).**
Top-left = `"1"`; top-right = `"2"`; bottom-left = `"3"`; bottom-right = `"4"`:
→ `{ "1": "A", "2": "B", "3": "C", "4": "D" }`

---

## 10. Boundary box rule

When a bar is subdivided, a chord symbol **and all its alteration suffixes** (`b5`, `#5`, `b9`, `m`,
`°`, etc.) must be read only from within that beat's own region. An alteration printed in a
neighboring region belongs to that neighbor, even if it sits visually close to the boundary.
**Never reach across a subdivision line** to attach an alteration to an adjacent beat's chord.
Example: diagonal box with `Am7` upper-left and `b5` lower-right → beat 1 is `Am7`, and the `b5`
belongs to the lower-right chord, not to `Am7`.

---

## 11. Repeat and shorthand expansion

The score uses shorthand for repetition. **Always expand fully in the JSON — never output `-`, `%`,
`•/•`, `→`, or any shorthand.** Write the actual chord(s).

| Score mark | Meaning | Action |
|---|---|---|
| Arrow + vertical line between rows (full section repeat) | Repeat the whole section | Copy all bars from the most recent section with the same letter. Any explicitly written bar in the repeated row overrides the copied value at that position. |
| Plain `→` at the start of a row | Repeat the previous row | Copy the previous row of the same section verbatim; explicit bars override. |
| Diagonal spanning **two** adjacent boxes | Two-bar repeat | Copy the immediately preceding two bars into those two bars. (Distinct from a single-box diagonal split, which is Case 2.) |
| `•/•` or similar within one box | Bar repeat | Copy the immediately preceding bar. |
| `-` (dash) in a box | Bar repeat | Copy the immediately preceding bar. |

**Dash exception:** if a `-` is the very first bar of a tune with nothing preceding it, it is a
genuine empty bar — encode `{ "1": "N.C." }` and note it in `notation_notes`. (Rare.)

---

## 12. Chord notation (canonical profile)

All chords are normalized to **one** canonical vocabulary. This section is the single point of change
if a different house style is later preferred.

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
| `…/14` (French "14th") | `#11` | `E9/14` → `E9#11` (7+7=14) |
| alteration in parentheses `(…)` | **omit entirely** | `Bb9(b9)` → `Bb9`; `D9(b5)` → `D9` |

**Other rules**
* Watch `B` vs `Bb` carefully — they are different chords.
* If a chord is uncertain due to scan quality, append `?` to that chord string, e.g. `Bbmaj7?`, and
  add a `notation_notes` entry.

---

## 13. Recordings & variants (do not digitize)

* **Recordings.** The margins list performers and 2-digit years. **Omit them.**
* **Variants.** Some bars are marked `*` (or labeled VARIANTE / STATEMENT) with a footnote. **Omit**
  the marker and its footnote.

---

## 14. Notation notes

A free-form object mapping a short key to an explanation. Record anything a downstream reader needs.
**Omit the field if empty.** Always note, when applicable:
* the French "14" = `#11` convention, the `5+` convention, the `t` = `+` convention;
* any omitted parenthesised alterations;
* any chord marked `?` (and why);
* any Case-3 inset/diagonal **ambiguity** (§9) and which bar it affects;
* enharmonic spellings or other ambiguous readings;
* truncation (e.g. only part of a tune visible in the crop);
* composer/performance annotations printed on the score;
* a missing chord grid (key `no_chord_grid`, see §15).

---

## 15. Missing chord grid / cross-references

Some tunes print no grid (e.g. they point to another tune's changes).
* Set `"sections": {}`.
* Add a `notation_notes` entry under key `no_chord_grid`, e.g.
  `"No chord grid printed. Form indicated as <label>. Chords must be inferred from the standard form."`
* **Do not invent chord content.**
* If the crop cross-references another tune (`→ IT'S EASY TO REMEMBER` or "same changes as X"),
  record that target in the `no_chord_grid` note.

---

## 16. Worked example (abbreviated)

Crop `341_RIVER_STAY_WAY_FROM_MY_DOOR.png` shows **River Stay Way from My Door**,
`STANDARD / MEDIUM`, form `32 A A B A`. Expected output
`tunes/341_RIVER_STAY_WAY_FROM_MY_DOOR.json` (sections abbreviated):

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
`B7`/`Bb7` must be read with care (§12 — `B` vs `Bb`).

---

## 17. Validation & per-tune self-check (run before accepting each file)

A unit is **accepted** only if it parses as JSON and passes every check below; otherwise retry
(§4.5).

1. Output is a **single bare object** (not an array), valid JSON, no surrounding prose/markdown.
2. All always-present fields are there: `title`, `title_uncertain`, `style`, `form`,
   `time_signature`, `page`, `source`, `sections`.
3. Absent optional fields are **omitted** (not `null`/`""`).
4. `source` equals `"Anthologie des grilles de jazz"`; `page` is an integer matching the manifest.
5. Every bar is an object with a `bar` index and a `beats` map (no bare strings); beat keys are
   `"1"`–`"4"`.
6. All repeats/dashes/`%`/arrows are **expanded** to explicit chords (no `-`, `%`, `•/•`, `→` in
   any value).
7. Section keys use counters (`A`, `A1`, `A2`), never primes; primes appear only in `form`.
8. All chords are in the canonical vocabulary (§12); uncertain chords carry `?` and a note.
9. Alterations were read within their own beat region only (§10).
10. Case-3 inset (beat 4) vs diagonal (beat 3) calls are made, with ambiguities noted.
11. `fingerprints` is absent (production runs).
12. `sections == {}` **iff** a `notation_notes.no_chord_grid` entry is present (§15).

A unit that fails validation after all retries is written as an `.error.json` stub and listed in
`run_report.json` — it does not stop the batch.

---

## Appendix A — Optional fingerprints module (off by default)

If harmonic analysis is explicitly enabled for a run, an optional `fingerprints` array may be added.
Keep it small (≤3 per tune) and ensure every referenced chord actually appears in the transcription
(fingerprints describe transcribed data; they must never introduce new chords).

```json
{
  "id": "river-stay-passing-dim",
  "label": "Chromatic passing diminished",
  "location": { "section": "A", "bars": [3] },
  "chords": ["Ab", "A°"],
  "description": "Ab → A° passing diminished connecting IV to the returning tonic."
}
```
Patterns to look for: ii–V–I, minor ii–V–i, tritone subs, chromatic passing diminished, backdoor
dominants (bVII7), secondary dominants, turnarounds, pedal/modal vamps, cycle-of-fifths dominant
chains.

Because these are interpretive and reduce run-to-run reproducibility, they are **excluded from the
default schema.** Do not emit `fingerprints` unless the run is explicitly configured for analysis.

---

## Appendix B — Suggested runner CLI (non-normative)

```
transcribe.py  --crops crops/  --manifest manifest.csv  --out tunes/
               --model <vlm-id> --workers 8 --retries 3 --dilate 1
               [--page-range 7:120] [--shard 0/4] [--debug]
```
* Resumable: re-running the same command skips tunes whose valid JSON already exists (§4.2).
* Parallel across machines: give each a different `--shard k/N` (§4.4).
* Writes `tunes/*.json`, `run_state.jsonl`, and `run_report.json`.
