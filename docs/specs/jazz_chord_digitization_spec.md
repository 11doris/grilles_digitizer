# Jazz Chord Grid Transcription — Implementation Spec (transcription only)

**Source book:** *Anthologie des grilles de jazz* (handwritten chord grids)
**Goal:** Convert each **already-cropped per-tune image** into one structured JSON file.
**Scale:** ~**1600 tunes**, each exported as a PNG. The run targets a **single laptop**: it must be
batchable and **resumable** (sequential by default; stop and continue across sittings).
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
INPUT   data/chords/01_crops/*.png  +  manifest.csv         (produced upstream; not our concern)
   |
   v
TRANSCRIBE   VLM, one call per crop   ->   one JSON file per tune
   |
   v
VALIDATE     schema + self-check      ->   accept | retry | flag for review
   |
   v
OUTPUT   data/chords/02_raw/*.json  +  run_report.json
```

One **work unit = one cropped PNG → one JSON file.** Units are fully independent, which keeps the
run simple and resumable: it processes them one at a time on a single machine and can be stopped and
resumed at any point. (Independence would also allow parallelism, but that is unnecessary here —
see §4.3.)

---

## 2. Input contract

The upstream crop step delivers two things into a working directory:

### 2.1 The crops
A directory `data/chords/01_crops/` of per-tune PNGs. Each image is **one tune**, **full page width**, at the
scan's native resolution. The filename stem encodes the printed page number and the title slug,
e.g. `341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.png`.

### 2.2 The manifest
A `manifest.csv` with one row per crop. Relevant columns (others may be present and ignored):

| Column | Use |
|---|---|
| `current_file` | The PNG filename inside `data/chords/01_crops/`. **This is the work-unit key.** |
| `page` | Printed page number (integer). Used for the JSON `page` field and naming. |
| `title` | Canonical title (already cleaned). **This is the authoritative title** (see below). |

Other columns (e.g. `review`, `conf`) are **ignored** — the title set has been cleaned up front, so
no upstream uncertainty flags are consulted.

**Title handling.** The JSON `title` is taken **verbatim from the manifest `title`** and is written
by the runner, not produced or altered by the model. The model never re-reads or "corrects" the
title from the image. (There is no title-uncertainty field; see §6.)

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
* Recording credits run vertically in the left and/or right margins of each crop. **Transcribe
  them** into `recordings` (§13.1); cut-off credits reappear on overlapping neighbour crops and
  need not be reconstructed.

---

## 4. Batch orchestration (this is what makes 1600 tunes tractable)

Implement a runner (e.g. `transcribe.py`) around the per-tune VLM call. It must satisfy all of
the following.

### 4.1 Work discovery
Read `manifest.csv`; the work list is its rows (one per `current_file`). Process in a stable order
(manifest order). Support selecting a subset (§4.4).

### 4.2 Idempotency / resume (required — this is the laptop's safety net)
For each unit, the output path is deterministic (§7). **Skip a unit if its JSON already exists and
passes validation (§9).** This makes the run safely re-entrant: closing the lid, sleeping, losing
power, Ctrl-C, or simply quitting and resuming later all cost at most the **one** tune in flight.
Never depend on in-memory state to know what is done — the presence of a valid output file is the
only source of truth. Write each JSON atomically (temp file + rename) so a kill mid-write cannot
leave a half-written, "present but invalid" file.

### 4.3 Sequential by default (single machine / local model)
This runs on **one laptop**, so process units **sequentially (one at a time)** by default
(`--workers 1`). If the transcription model runs **locally** (e.g. via a local server / GPU / CPU),
there is a single model instance and parallel calls would only contend for the same memory/compute
and can trigger out-of-memory or thrashing — keep it sequential. Raise `--workers` above 1 **only**
if you are calling a **remote API** from the laptop (then a small pool, e.g. 2–4, hides network
latency). Default = 1.

**Robust calls.** On a transient failure (local OOM/timeout, or — for a remote API — HTTP 429/5xx),
retry the unit with a short backoff (e.g. 1s, 2s, 4s). There are no provider rate limits to respect
for a local model; backoff there just rides out a momentary resource spike.

### 4.4 Running in chunks (no sharding needed)
**Sharding is not needed on a single machine** — it only exists to divide work across multiple
machines. To run the ~1600 tunes over several sittings, just **stop and re-run the same command**:
the resume rule (§4.2) skips everything already done. Optionally limit a session with
`--page-range A:B` (only tunes whose `page` is in `[A, B]`) if you want to deliberately do the book
in slices; it is a convenience, not a requirement.

### 4.5 Per-unit retries (required)
The VLM may return invalid JSON or fail validation. For each unit, retry up to **R** times
(default **3**) with a progressively stricter reminder appended to the prompt
("Return one bare JSON object only; no prose; valid JSON."). If still failing after R attempts,
**do not abort the run** — write a stub `data/chords/02_raw/<stem>.error.json`
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
  "total": 1800, "succeeded": 1788, "failed": 12,
  "elapsed_s": 5123, "model": "<model-id>",
  "flagged": {
    "missing_required_field": ["123_SOME_TUNE.png", "..."],
    "no_chord_grid":          ["7_AFTER_HOURS.png", "..."],
    "errors":                 ["456_OTHER.png", "..."]
  }
}
```
**Flag a tune for human review when any non-optional (always-present) output field is missing**
(§6.2 / §17 check 2) — i.e. any of `title`, `style`, `form`, `time_signature`, `page`, `source`,
`sections` absent. A unit that never produced valid JSON after retries (§4.5) is an `error`. The
informational `no_chord_grid` list is included for convenience but is a valid state, not a defect.

### 4.10 Laptop throughput & resource notes
Expect a **long sequential run**: at, say, 10–30 s per tune on a local model, 1600 tunes is roughly
**5–13 hours**. Plan to run it in sittings — resume (§4.2) makes that free. Practical guidance:
* **Memory:** stream one image at a time (open the crop, send it, release it). Never load all
  ~1600 PNGs at once.
* **Heat/throttling:** offer an optional `--delay S` pause between units (default 0) if the laptop
  runs hot during long sessions.
* **Interruptions:** lid-close/sleep/Ctrl-C are safe — at most the in-flight tune is redone on
  resume. There is nothing to clean up.
* **Scaling:** the only knob that increases throughput here is a faster model or `--workers` >1
  **when calling a remote API** (§4.3). On a local model, throughput is fixed by the hardware.

---

## 5. Transcription task (given to the VLM, once per crop)

Send the (optionally ink-thickened) crop plus this instruction. The `title` is **not** asked of the
model — the runner fills it from the manifest. Provide `page` as context.

> You are transcribing **one** handwritten jazz chord grid (one tune) from a scanned French jazz
> "grilles" book. The image shows a large hand-lettered title, smaller style/tempo/form labels, and
> a grid of **chord boxes** organized into rows (sections). Recording credits may run vertically in
> the margins — **transcribe them** into `recordings`; alternate `VARIANTE` bars below the grid go
> into `variants` (§13).
>
> Produce **one bare JSON object** (no array, no prose, no markdown fence) following the schema and
> rules you have been given. **Do not output a `title` field** — it is supplied separately. Expand
> every repeat/shorthand into explicit chords. Use the canonical chord vocabulary. Read each chord
> and its alterations only from within its own box region. If a mark is ambiguous due to scan
> quality, make your best reading, append `?` to that chord, and add a `notation_notes` entry. The
> printed page is `<manifest.page>`.

The runner then sets `title` (from the manifest), `page` (from the manifest), and `source` (the
constant) on the returned object before validating and writing it. This guarantees those three
always-present fields can never be missing.

### 5.1 Prompt assembly (stable prefix vs. per-call tail)
Split every request into two parts so the stable part can be cached (§18.3):

* **Cached system prompt (identical for all ~1800 calls):** the task instruction above plus the
  notation rulebook (§8–§15) and the worked examples (Appendix D). This block is large on purpose —
  it must clear the platform's minimum cacheable length (≥4,096 tokens to be safe on every platform;
  see §18.3) and this spec's rulebook + examples already exceed that. Mark the cache breakpoint at
  the **end** of this block.
* **Per-call message (changes every call):** the tune's image, plus the single line giving its
  printed `page`. Keep this tail minimal — everything reusable belongs in the cached prefix.

Because the prefix is byte-for-byte identical across calls, the first call pays a one-time cache
write and the remaining ~1799 pay the reduced cache-read rate for it.

---

## 6. Output schema

Each unit emits **one JSON file** containing a **single bare object** (not wrapped in an array).

### 6.1 Object shape
```json
{
  "title": "River Stay Way from My Door",
  "composer": "Harry Woods – Mort Dixon",
  "year": "1931",
  "style": "STANDARD",
  "tempo": "MEDIUM",
  "form": "32 A A B A",
  "time_signature": "4/4",
  "page": 341,
  "source": "Anthologie des grilles de jazz",
  "sections": { "...": [ /* bar objects */ ] },
  "recordings": [ /* optional; margin credit lines */ ],
  "variants": [ /* optional; alternate bars, see §13.2 */ ],
  "notation_notes": { /* optional */ }
}
```

`title`, `page`, and `source` are written by the runner (§5); the model supplies the rest.

### 6.2 Field rules
| Field | Rule |
|---|---|
| `title` | **Written by the runner, verbatim from the manifest `title`.** The model does not output or alter it. Always present. |
| `composer` | Names joined with `" – "` (space–en dash–space). **Omit the field entirely if absent.** |
| `year` | Composition year as a string. **Omit if absent.** |
| `style` | The upper-left genre label exactly as printed (`DIXIELAND`, `NEW ORLEANS`, `SWING`, `STANDARD`, `ELLINGTONIA`, …). Always present. |
| `tempo` | The tempo label (`MEDIUM`, `MEDIUM FAST`, `MEDIUM SLOW`, `FAST`, …). **Omit if absent.** |
| `form` | The form string exactly as printed, **preserving primes** (e.g. `32 A B C A'`). Always present. |
| `time_signature` | Default `"4/4"`; override only if the score indicates otherwise. Always present. |
| `page` | Printed page number (integer). **Written by the runner from the manifest `page`.** Always present. |
| `source` | Constant `"Anthologie des grilles de jazz"`. **Written by the runner.** Always present. |
| `sections` | See §7. Always present (may be `{}` only for the missing-grid case, §14). |
| `recordings` | List of margin performer/year credit lines, one string per printed line (§13.1). **Omit if none.** |
| `variants` | List of alternate-bar objects printed below the grid (§13.2). **Omit if none.** |
| `notation_notes` | See §14. **Omit if none.** |

**Optional-field policy:** when an optional field has no content, **omit the key entirely** (do not
emit `null` or `""`). The only constant-but-present field is `source`.

> `fingerprints` (harmonic analysis) are **not** part of the default output. They are an optional
> module; see Appendix A. Do not emit them in production runs.

---

## 7. Output naming & layout

* Output filename mirrors the input PNG stem with a `.json` extension:
  `data/chords/01_crops/341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.png` → `data/chords/02_raw/341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.json`.
  This guarantees a 1:1 mapping, avoids slug recomputation, and inherits the upstream de-duplication
  (no collisions).
* Error stubs: `data/chords/02_raw/<stem>.error.json` (§4.5).

```
data/chords/01_crops/        341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.png …   (input)
manifest.csv                                                       (input)
data/chords/02_raw/        341_ROCK_A_BYE_YOUR_BABY_WITH_A_DIXIE_MELODY.json …  (output, one per tune)
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
  `verse_A`, `verse_B`, `clarinet_A`, `clarinet_A1`. **Multi-strain pieces** use this with a strain
  prefix — see §8.5.
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
{ "bar": 3, "beats": { "1": "Ab", "3": "Ao7" } }
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

### 8.5 Multi-strain pieces

A few pieces — multi-strain rags, stride numbers, marches — print **two or more separate grids
("strains") stacked on the page, each with its own form label** (e.g. `16 A A'`, then `24 A B A`,
then `16 A A`), sometimes with a short connecting passage (a modulation or interlude) between them.
A single letter map cannot capture this, so use the convention below — still inside the **one flat
`sections` map** (no new nesting, the object shape of §6.1 is unchanged):

* **Number the printed strains `s1`, `s2`, `s3`, …** top to bottom. If the score *names* a strain
  (e.g. a "TRIO"), a lowercase word may be used as the prefix instead (`trio`).
* **Prefix every section key of a strain** with its strain id and an underscore:
  `s1_A`, `s1_A1`, `s2_A`, `s2_B`, `s2_A1`, `s3_A`, `s3_A1`. The letter / counter / named-section
  rules of §8.1 apply **independently within each strain** — counters restart at `A` in every
  strain, and primes are still never used in keys (primes live only in `form`).
* **A connecting passage between strains** that is not itself a lettered strain (a modulation,
  interlude, intro, or coda) is a **bare named section** with no strain prefix
  (`modulation`, `interlude`, `intro`, `coda`), placed in the map in playing order.
* **`form`** is the per-strain printed labels joined with `" | "`, in printed order
  (e.g. `"16 A A' | 24 A B A | 16 A A"`). The *i*-th `" | "`-separated segment is the label of
  strain `s`*i*. This keeps the always-present `form` field a faithful summary of the whole piece.
* Bars, beats, subdivision layouts (§9), the boundary rule (§10), and repeat/shorthand expansion
  (§11) work exactly as for single-strain tunes, applied within each strain's rows.

**Single-strain tunes — the overwhelming majority — are unchanged:** a flat `sections` map with
plain letter keys (`A`, `A1`, `B`, …) and one `form` string. **Only** use the `s<N>_` convention when
the page actually shows more than one labelled strain; never wrap an ordinary AABA tune in a single
`s1_` strain.

Abbreviated example (a 3-strain stride piece):
```json
{
  "form": "16 A A' | 24 A B A | 16 A A",
  "sections": {
    "s1_A":  [ /* 8 bars */ ], "s1_A1": [ /* 8 bars */ ],
    "s2_A":  [ /* 8 bars */ ], "s2_B": [ /* 8 bars */ ], "s2_A1": [ /* 8 bars */ ],
    "modulation": [ /* 4 bars */ ],
    "s3_A":  [ /* 8 bars */ ], "s3_A1": [ /* 8 bars */ ]
  }
}
```

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

**Brief-extension split (root carried over).** Sometimes only ONE region names a full chord and the
other region shows just an added degree/extension with **no root of its own** — a bare `7`, `6`, `9`,
`m7`, `maj7`, … written for brevity. Carry the root over from the named region and expand: a box with
`Am` upper and a bare `7` lower means `Am` then `Am7` → `{ "1": "Am", "3": "Am7" }`. It is still a
two-beat split — never collapse it into a single `Am7`. (Borrowing the missing **root** this way is
the one allowed exception to the boundary rule, §10; a bare *alteration* like `b5`/`#5` is not a
chord and does **not** borrow a root — it stays with its own region per §10.)

**Case 3 — Bottom-right inset square only (no full horizontal divider).**
One chord fills the large undivided area; a small framed square sits in the bottom-right corner.
Large area = beats 1–3 → `"1"`; inset square = beat 4 → `"4"`: → `{ "1": "Em7", "4": "Ebo7" }`
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
`o7`, etc.) must be read only from within that beat's own region. An alteration printed in a
neighboring region belongs to that neighbor, even if it sits visually close to the boundary.
**Never reach across a subdivision line** to attach an alteration to an adjacent beat's chord.
Example: diagonal box with `Am7` upper-left and `b5` lower-right → beat 1 is `Am7`, and the `b5`
belongs to the lower-right chord, not to `Am7`.

**One exception — a borrowed root.** If a region shows only an added degree/extension and no root at
all (a bare `7`, `6`, `m7`, …), it is a brevity shorthand: borrow the **root** from the adjacent
named chord (§9 Case 2, "brief-extension split"). E.g. `Am` upper + bare `7` lower → `{ "1": "Am",
"3": "Am7" }`. This borrows only the missing root; it never moves alterations off a chord that
already has its own root.

---

## 11. Repeat and shorthand expansion

The score uses shorthand for repetition. **Always expand fully in the JSON — never output `-`, `%`,
`•/•`, `→`, or any shorthand.** Write the actual chord(s).

| Score mark | Meaning | Action |
|---|---|---|
| Left-arrow row (a `→` at the far left of a row, with or without a vertical line/bracket to an earlier row) | Repeat an earlier **same-letter** row, positionally | First use `form` to label every printed grid row with its letter, top to bottom (e.g. `form "32 A A B A"` → rows A, A, B, A → sections A, A1, B, A2). The arrow row copies from the **nearest row above it with the SAME letter** — usually **not** the row physically just above it: in `A A B A` the 4th row (A2) copies the 2nd row (A1), **jumping over** the 3rd row (B). Never copy a different-letter row (e.g. B into A) just because it is adjacent. Fill each bar from the same bar number of that same-letter row (as already resolved, its own overrides carried through); any bar written explicitly in the arrow row overrides at that position. |
| Diagonal spanning **two** adjacent boxes | Two-bar repeat | Copy the immediately preceding two bars into those two bars. (Distinct from a single-box diagonal split, which is Case 2.) |
| `•/•` or similar within one box | Bar repeat | Copy the immediately preceding bar. |
| `-` (dash) in a box | Bar repeat | Copy the immediately preceding bar. **Exception:** inside a left-arrow row a dash is not a bar-repeat — it is an empty placeholder taking that bar from the same position of the referenced same-letter row (see the left-arrow row above). |

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
| Diminished | `o7` | `Co7` |
| Augmented triad | `+` | `Eb+` |
| Augmented dominant | `7#5` | `Eb7#5` |
| Minor–major 7th | `m(maj7)` | `Dm(maj7)` |
| Sixth / ninth etc. | `6`, `9`, `m6`, `9#11`, … | `Ab6`, `Db9` |

**Conversions from the book's notation → canonical**
| In the book | Encode as | Note |
|---|---|---|
| `7M`, `M7`, `Δ`, `△` (major 7) | `maj7` | e.g. `Eb7M` → `Ebmaj7`, `C△` → `Cmaj7` |
| `mM7`, `m7M`, `m(M7)` (minor-major 7) | `m(maj7)` | e.g. `DmM7` → `Dm(maj7)` |
| `ø`, `Ø`, `m7(b5)` (half-dim) | `m7b5` | e.g. `Aø` → `Am7b5` |
| `o`, `°`, `dim` (diminished) | `o7` | e.g. `Edim` → `Eo7` |
| `+`, `aug` (augmented triad) | `+` | e.g. `Eb aug` → `Eb(#5)` |
| superscript `5+` on a dominant (aug 5th) | `#5` | e.g. `Bb7` with `5+` → `Bb7#5` |
| `b5` shown as superscript/subscript | `b5` | keep, attached to its own chord only (§10) |
| suffix `t` (book shorthand for `+`, i.e. raise a degree) | `#` on that degree | `Eb9t` → `Eb#9`; `F75t` → `F7#5` |
| `…/14` (French "14th" = 7+7) | `#11` | `E9/14` → `E9#11` |
| `…/13`, `…/11`, `…/9` (added French extensions) | `13` / `11` / `9` | added tone on the chord; read the degree literally |
| alteration in parentheses `(…)` | **omit entirely** | `Bb9(b9)` → `Bb9`; `D9(b5)` → `D9`; `G7(F7)` → `G7` |
| flat written on the 9 (e.g. score `A9b`) | `…7b9` | the flat sits on the 9th, not the root: `A9b` → `A7b9` (note the ambiguity in `notation_notes`) |
| slash chord `C/E` (chord over bass) | `C/E` | keep as written if a bass note is clearly indicated |
| `N.C.` / blank first bar | `N.C.` | only when a bar is genuinely empty (§11 dash exception) |

**Chord-reading procedure (apply per beat region):**
1. Identify the **root** letter (`A`–`G`) and any accidental immediately on it (`b`/`#`). Beware
   `B` vs `Bb` — check for the flat.
2. Read the **quality** marks attached to that root within the same region (`m`, `°`, `+`, `maj7`,
   `7`, `6`, `9`, …).
3. Apply the **conversions** above to alteration suffixes (`7M`→`maj7`, `t`→`#`, `/14`→`#11`).
4. **Drop** any parenthesised alteration.
5. Do **not** import any mark from a neighbouring region across a subdivision line (§10).
6. If any mark is unreadable, transcribe the best reading, append `?`, and add a `notation_notes`
   entry.

**Other rules**
* Watch `B` vs `Bb` carefully — they are different chords.
* If a chord is uncertain due to scan quality, append `?` to that chord string, e.g. `Bbmaj7?`, and
  add a `notation_notes` entry.

---

## 13. Recordings & variants (digitize)

### 13.1 Recordings

The left and/or right margins list performers, each followed by a 2-digit recording year
(e.g. `L.Armstrong 29.38.44- C.Hopkins 34`; a performer may carry several years such as
`F.Waller 29.35.38`). **Transcribe** them into a top-level `recordings` field — a list of
strings, **one string per printed margin line**, in top-to-bottom order, read as faithfully
as possible (keep the names, the years, and separators like `-` and `/`).

Recording lists frequently run off the edge of a crop; because adjacent crops **overlap**,
the cut-off portion reappears on the neighbouring crop. That is expected — transcribe the
visible part and do **not** try to reconstruct the missing text. **Omit** `recordings` only
when a tune has no credits at all.

### 13.2 Variants

Some tunes print one or more **alternate** bars below the grid, labelled `VARIANTE` (or
`STATEMENT`) with a bar reference, e.g. `VARIANTE  Bar 1, 9, 25`. Each alternate is tied to
specific bar(s) of the main grid, usually via a **marker symbol** (`*`, `①`, `②`, …) drawn
both next to the target grid bar **and** next to the alternate. The goal is to let a
downstream step **replace** the referenced grid bars with these alternate chords, so both the
chords (per bar) and the reference to which bars they replace must be captured.

Transcribe them into a top-level `variants` field — a list of objects, **one object per
`VARIANTE` label**:

```json
"variants": [
  {
    "marker": "*",
    "applies_to": "Bar 1, 9, 25",
    "bars": [
      { "bar": 1, "beats": { "1": "Fm7", "3": "Gm7" } },
      { "bar": 2, "beats": { "1": "Ab7M", "3": "Bb7" } }
    ]
  }
]
```

* `marker` — the symbol tying this variant to its grid bar(s); **omit** if none is drawn.
* `applies_to` — the printed bar reference, **verbatim** (e.g. `"Bar 1, 9, 25"`, `"Bar 27"`).
* `bars` — the alternate bars in the **same shape** and under the **same** subdivision /
  notation / expansion rules as a section (§8–§12): one object per printed variant box, `bar`
  1-indexed in printed left-to-right order, beats read from each box's own regions.

Rules:

* The main grid stays **unchanged** — the original chords remain in `sections`, and the marker
  symbol is **never** written into a chord string (it only links the variant).
* A page may carry **several** variants, each with its own marker and reference; emit one
  object for each, in printed order (see `20_01_ANNIE_LAURIE`, `15_04_ALL_THROUGH_THE_NIGHT`).
* Variant boxes are sometimes **cut off** at the crop edge — transcribe what you can, append
  `?` to any uncertain chord, and add a `notation_notes` entry. Do not invent chords.
* **Omit** `variants` entirely when the page has none.

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
`data/chords/02_raw/341_RIVER_STAY_WAY_FROM_MY_DOOR.json` (sections abbreviated):

```json
{
  "title": "River Stay Way from My Door",
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
      { "bar": 3, "beats": { "1": "Ab", "3": "Ao7" } },
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
Note: bar 2 is a Case-2 diagonal (`Eb` / `Eb7`); bar 3 is a Case-2 split (`Ab` / `Ao7`); bar 6's
`B7`/`Bb7` must be read with care (§12 — `B` vs `Bb`).

---

## 17. Validation & per-tune self-check (run before accepting each file)

A unit is **accepted** only if it parses as JSON and passes every check below; otherwise retry
(§4.5).

1. Output is a **single bare object** (not an array), valid JSON, no surrounding prose/markdown.
2. All always-present fields are there: `title`, `style`, `form`, `time_signature`, `page`,
   `source`, `sections`. **If any is missing, the tune is flagged for human review** (§4.9).
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
13. Multi-strain pieces (§8.5) are well-formed: every strain-prefixed key is `<prefix>_<section-id>`
    with a non-empty section id and no prime; numbered strains run `s1, s2, …` contiguously from `s1`;
    and the `form` string joins the per-strain labels with `" | "`.

A unit that fails validation after all retries is written as an `.error.json` stub and listed in
`run_report.json` — it does not stop the batch.

---

## 18. Cost optimization (trade duration for cost)

The run is allowed to take longer in exchange for spending less. "Cost" means tokens/$ on a hosted
API, or compute-time/energy on a local model; most levers help both. Apply these:

1. **Pick the cheapest model that passes the accuracy bar.** Validate candidate models on a small
   labeled sample (≈20–30 tunes spanning easy and busy grids). Use the smallest/cheapest model whose
   transcriptions are acceptable. Locally, that means a smaller or more-quantized model.
2. **Right-size the image (biggest single lever).** Before sending, downscale the crop so its long
   edge is the **minimum still legible** — start at **~1100 px** and only raise it if small marks
   (`b5`, `7M`, inset squares) become unreadable; convert to grayscale. Vision tokens scale with
   pixels, so this directly cuts input cost (and local compute). **Never upscale**, and never use
   super-resolution (§3). Expose as `--max-long-edge` (default 1100).
3. **Cache the static instructions (API).** The instruction/schema block — everything in this spec
   that is sent to the model, including the notation rulebook (§8–§15) and the worked examples
   (Appendix D) — is **identical across all ~1800 calls**. Put it in a cached system prompt so it is
   billed at the cache rate (a one-time write, then ~10% of input price per read) instead of full
   price per tune. Only the per-call variable part (the image, and the `page` value) sits after the
   cache breakpoint.

   **Minimum cacheable prefix (must be met or caching silently does nothing):** on the **Claude API**
   it is **1,024 tokens** for Claude Opus 4.8 / Sonnet 4.6 / Sonnet 4.5 (per Anthropic's prompt-caching
   docs). On **Amazon Bedrock** several 4.x models (e.g. Opus 4.5/4.6, Sonnet 4.5, Haiku 4.5) require
   **4,096 tokens** per cache checkpoint. **Build the cached system prompt to comfortably exceed
   4,096 tokens** so it caches on either platform — this spec's rulebook plus the Appendix D examples
   already does. If a prefix is below the minimum the request still succeeds but is billed at full
   rate (check `usage.cache_read_input_tokens` / `cache_creation_input_tokens` to confirm a cache hit).
4. **Use the batch/async API (API).** If the provider offers asynchronous batch processing
   (commonly **~50% cheaper**, results within hours), use it — accepting longer turnaround is
   exactly the trade we want. Submit in chunks; resume (§4.2) still applies to results as they land.
5. **Cap and compact the output.** Set a sane `--max-output-tokens` (≈1200 covers a busy tune) and
   request **minified** JSON (no pretty-printing). Do **not** enable the fingerprints module
   (Appendix A) — it adds output tokens and reduces reproducibility.
6. **Cheap-first escalation (optional).** Run the whole book on the cheap model; then re-run **only**
   the tunes flagged for review (a missing required field, an `error` stub, a `no_chord_grid`, or any
   `?` chord) on a stronger/pricier model. Most tunes never touch the expensive model. Resume
   makes the second pass touch only the flagged subset.
7. **Never re-pay for finished work.** Resume (§4.2) guarantees a restart re-processes nothing
   already done — important when running a long job in sittings.

These do not change the schema or notation rules; they only change which model, image size, and
billing mode are used.

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
  "chords": ["Ab", "Ao7"],
  "description": "Ab → Ao7 passing diminished connecting IV to the returning tonic."
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
transcribe.py  --crops data/chords/01_crops/  --manifest manifest.csv  --out data/chords/02_raw/
               --model <vlm-id> --workers 1 --retries 3 --dilate 1
               --max-long-edge 1100 --max-output-tokens 1200
               [--page-range 7:120] [--delay 0] [--batch] [--debug]
```
* **Single laptop:** keep `--workers 1` for a local model; raise it only when calling a remote API.
* Resumable: re-running the same command skips tunes whose valid JSON already exists (§4.2), so you
  can stop and continue across sittings — no sharding needed.
* Cost: `--max-long-edge` downscales the image before the call (§18.2); `--batch` uses the async
  API when available (§18.4); `--page-range` optionally limits a session.
* Writes `data/chords/02_raw/*.json`, `run_state.jsonl`, and `run_report.json`.

---

## Appendix C — Cost estimate for ~1800 tunes (illustrative)

**Local model on the laptop:** no API fees. Energy only — a laptop drawing ~30–60 W for the several
hours of the run is on the order of **$0.05–0.30** total. Treat as effectively free; optimize for
time, not money.

**Hosted vision API:** estimate per tune ≈ **2,000** instruction-text tokens in, **~1,200** image
tokens in (right-sized to ~1100 px), **~600** JSON tokens out. For 1800 tunes that is ≈ **5.8M input
+ 1.1M output tokens**. At a few representative (illustrative — **verify current rates**) tiers:

| Model tier | $/Mtok (in / out) | ≈ total, 1800 tunes |
|---|---|---|
| Small / cheap VLM | 0.10 / 0.40 | ~$1 |
| Mid (Haiku-class) | 0.30 / 1.20 | ~$3 |
| Upper-mid (Sonnet-class) | 1.00 / 5.00 | ~$11 |
| Premium | 3.00 / 15.00 | ~$33 |

**With the §18 levers:** prompt caching removes most of the 3.6M instruction-token cost, and a batch
API roughly halves the remainder. Cheap model + caching + batch lands **well under $2** for the whole
book; a premium model without those is the ~$33 end. Cheap-first escalation (§18.6) keeps you near
the cheap tier while sending only the few flagged tunes to a premium model.

Numbers scale linearly with tune count and with image size, so `--max-long-edge` is the dial with the
most leverage on the input side.


---

## Appendix D — Worked examples (real transcriptions; part of the cached system prompt)

These are complete, correct outputs for real pages of the book, in the current schema (no
`title_uncertain`; these particular examples happen not to exercise `recordings` or `variants`
(§13), though the schema supports both; `title`/`page`/`source` shown as the runner will
fill them). **Include all of them verbatim in the model's system prompt** — they double as few-shot
guidance and as the bulk of the stable, cacheable prefix (§18.3). Study what each one demonstrates.

### Robbins Nest

Demonstrates: **AABA form expansion** — the page prints one A row and one B row with
`form = "32 A A B A"`, expanded into sections `A`, `A1`, `B`, `A2` where `A1`/`A2` are copies of `A`
(primes never appear in section keys, only in `form`); **Case-2 diagonal splits** (e.g. two chords
in a bar on beats `"1"` and `"3"`); and a **`notation_notes.truncated`** entry recording that part
of the tune ran off the crop. Whole-bar chords are still objects (`{ "1": "..." }`).

```json
{
  "title": "Robbins Nest",
  "composer": "Sir Charles Thompson – Illinois Jacquet – Bob Russell",
  "year": "1947",
  "style": "SWING",
  "tempo": "MEDIUM",
  "form": "32 A A B A",
  "time_signature": "4/4",
  "page": 340,
  "source": "Anthologie des grilles de jazz",
  "sections": {
    "A": [
      {
        "bar": 1,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "A9"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "A9"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Fm7",
          "3": "Eo7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Ebm7",
          "3": "Ab7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Db"
        }
      }
    ],
    "A1": [
      {
        "bar": 1,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "A9"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "A9"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Fm7",
          "3": "Eo7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Ebm7",
          "3": "Ab7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Db"
        }
      }
    ],
    "B": [
      {
        "bar": 1,
        "beats": {
          "1": "F7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "F7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Bb7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Bb7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Eb7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Eb7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Ab7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Ab7"
        }
      }
    ],
    "A2": [
      {
        "bar": 1,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "A9"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "A9"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Fm7",
          "3": "Eo7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Ebm7",
          "3": "Ab7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Db"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Db"
        }
      }
    ]
  },
  "notation_notes": {
    "truncated": "Only 16 bars (sections A and A1) printed on page 340 for the head; the B and final A sections are reconstructed from the 32 A A B A form."
  }
}
```

### Rock-a-Bye Your Baby with a Dixie Melody

Demonstrates: **primes in `form` vs counters in section keys** (`form = "32 A B A' C"` → sections
`A`, `B`, `A1`, `C`); **Case-3 bottom-right inset** (section A bar 2 = `Em7` on beat `"1"` + `Ebo7`
on beat **`"4"`**, the inset square); **conversions** (`7M`/`M7` → `maj7`, `mM7` → `m(maj7)`); a
**parenthesised alteration dropped** (the score's optional `(F7)` is omitted from the grid); and a
**flat-on-the-9 reading** (`A9b` → `A7b9`) with the ambiguity recorded in `notation_notes`. The
score's `VARIANT Bar 1, 17, 29` box is **cut off at the bottom of this crop**, so its bars are not
recoverable here and no `variants` entry is shown; on an un-cropped scan it would be captured under
`variants` per §13.2.

```json
{
  "title": "Rock-a-Bye Your Baby with a Dixie Melody",
  "composer": "Jean Schwartz – Sam M. Lewis – Joe Young",
  "year": "1918",
  "style": "STANDARD",
  "tempo": "MEDIUM",
  "form": "32 A B A' C",
  "time_signature": "4/4",
  "page": 341,
  "source": "Anthologie des grilles de jazz",
  "sections": {
    "A": [
      {
        "bar": 1,
        "beats": {
          "1": "Cmaj7",
          "3": "Dm7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Em7",
          "4": "Ebo7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Dm7",
          "3": "G7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Dm7",
          "3": "C#o7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Dm7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "G7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "C"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "D7",
          "3": "G7"
        }
      }
    ],
    "B": [
      {
        "bar": 1,
        "beats": {
          "1": "Dm",
          "3": "Dm(maj7)"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Dm7",
          "3": "G7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Cmaj7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Em7",
          "3": "A7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "G"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Am7",
          "3": "D7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Dm7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "G7"
        }
      }
    ],
    "A1": [
      {
        "bar": 1,
        "beats": {
          "1": "Cmaj7",
          "3": "Dm7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Em7",
          "4": "Ebo7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Dm7",
          "3": "G7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Dm7",
          "3": "C#o7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Dm7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "G7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "E7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "E7"
        }
      }
    ],
    "C": [
      {
        "bar": 1,
        "beats": {
          "1": "A7b9"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "A7b9"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "D9"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "D9",
          "3": "D#o7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Cmaj7",
          "3": "Dm7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Em7",
          "3": "Am7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "D7",
          "3": "G7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "C"
        }
      }
    ]
  },
  "notation_notes": {
    "conversions": "Major 7th written '7M'/'M7' normalised to 'maj7'; minor-major 7th 'mM7' normalised to 'm(maj7)'.",
    "C_opening": "Section C bars 1-2 read 'A9b' in the score (flat on the ninth); encoded as A7b9. The flat sits on the 9, not the root — an alternative reading is Ab9."
  }
}
```

### Roll On Mississippi, Roll On

Demonstrates: a **verse + chorus tune** with a compound `form` string and **prefixed/compound
section keys** — the verse rows become `verse_A`, `verse_A1`, `verse_B`, and the chorus rows become
`A`, `B`, `A1`, `C`, `D`. Section counts are **not** forced to 8 bars; each section has whatever the
printed row contains. Conversions and an `uncertain` note are present.

```json
{
  "title": "Roll On Mississippi, Roll On",
  "composer": "Eugene West – James McCaffrey – Dave Ringle",
  "year": "1931",
  "style": "STANDARD",
  "tempo": "FAST",
  "form": "24 A A B (VERSE) + 40 A B A' C D (CHORUS)",
  "time_signature": "4/4",
  "page": 341,
  "source": "Anthologie des grilles de jazz",
  "sections": {
    "verse_A": [
      {
        "bar": 1,
        "beats": {
          "1": "Fm",
          "3": "Fm(maj7)"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Fm7",
          "3": "Dm7b5"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Bbm6"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Bbm6"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "C7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "C7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Fm"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Fm"
        }
      }
    ],
    "verse_A1": [
      {
        "bar": 1,
        "beats": {
          "1": "Fm",
          "3": "Fm(maj7)"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Fm7",
          "3": "Dm7b5"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Bbm6"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Bbm6"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "C7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "C7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Fm"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Fm"
        }
      }
    ],
    "verse_B": [
      {
        "bar": 1,
        "beats": {
          "1": "Eb7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Eb7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Ab"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Ab",
          "3": "Ao7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Eb",
          "3": "Eb",
          "4": "B7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "F7",
          "3": "Bb7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Eb",
          "3": "Ebo7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Eb7"
        }
      }
    ],
    "A": [
      {
        "bar": 1,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Db9"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Db9",
          "3": "Eb7#5"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Bb7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Bb7"
        }
      }
    ],
    "B": [
      {
        "bar": 1,
        "beats": {
          "1": "Eb7",
          "3": "Bb7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "F#o7",
          "3": "Eb7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Eb7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Eb7#5"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Ab",
          "3": "Eb7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Fo7",
          "3": "Abo7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Do7",
          "2": "Eb7",
          "3": "Do7",
          "4": "Eb7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Do7",
          "2": "Eb7",
          "3": "Do7",
          "4": "Eb7"
        }
      }
    ],
    "A1": [
      {
        "bar": 1,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Db9"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Db9",
          "3": "Eb7#5"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "C7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "C7"
        }
      }
    ],
    "C": [
      {
        "bar": 1,
        "beats": {
          "1": "Ao7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Gbo7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Co7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Ao7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Bo7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Abo7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Do7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Bo7",
          "3": "Eb7#5"
        }
      }
    ],
    "D": [
      {
        "bar": 1,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Ab6"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "F7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "F7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Bb7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Eb7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Ab"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Ab"
        }
      }
    ]
  },
  "notation_notes": {
    "conversions": "Minor-major 7th 'mM7' normalised to 'm(maj7)'. Augmented-5th dominant written with superscript '5+'/'t' (= '+') normalised to '#5' (e.g. Eb7#5).",
    "uncertain": "Verse_A bar 2 (Dm7?) and chorus B bar 1 (Bb7?) lower chords are uncertain; see variants."
  }
}
```

### Rockin' in Rhythm

Demonstrates the **hardest layout**: an Ellington head (`style = "ELLINGTONIA"`) with **named
sections** (`intro`, `interlude`), **prefixed letter sections** (`clarinet_A`, `clarinet_A1`),
**non-eight-bar sections**, and **multiple `notation_notes`** (`harmonic_note`, `form_note`,
`performance_note`). One printed tune can therefore expand into many sections of differing lengths;
encode exactly what each row shows and let `form`/notes carry the structure.

```json
{
  "title": "Rockin' in Rhythm",
  "composer": "Duke Ellington – Harry Carney – Irving Mills",
  "year": "1930",
  "style": "ELLINGTONIA",
  "tempo": "MEDIUM",
  "form": "26 A B C",
  "time_signature": "4/4",
  "page": 342,
  "source": "Anthologie des grilles de jazz",
  "sections": {
    "intro": [
      {
        "bar": 1,
        "beats": {
          "1": "B7",
          "3": "E7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "A7",
          "3": "D7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "G7",
          "3": "C"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "C"
        }
      }
    ],
    "A": [
      {
        "bar": 1,
        "beats": {
          "1": "C",
          "3": "C"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "F",
          "4": "F#o7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "C",
          "3": "Am7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Dm7",
          "3": "G7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "C",
          "3": "C"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "F",
          "4": "F#o7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "C",
          "3": "Am7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Dm7",
          "2": "G7",
          "3": "C"
        }
      }
    ],
    "B": [
      {
        "bar": 1,
        "beats": {
          "1": "C",
          "3": "C"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "F",
          "4": "F#o7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "C",
          "3": "Am7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Dm7",
          "3": "G7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "C",
          "3": "C"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "F",
          "4": "F#o7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "C",
          "3": "Am7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Dm7",
          "3": "G7"
        }
      }
    ],
    "C": [
      {
        "bar": 1,
        "beats": {
          "1": "C",
          "3": "C"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "F",
          "4": "F#o7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "C",
          "3": "Am7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Dm7",
          "3": "G7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "C",
          "3": "C"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "F",
          "4": "F#o7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "C",
          "3": "Am7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Dm7",
          "2": "G7",
          "3": "C"
        }
      }
    ],
    "A1": [
      {
        "bar": 1,
        "beats": {
          "1": "C",
          "3": "C"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "F",
          "3": "F#o7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "C",
          "3": "Am7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Dm7",
          "3": "G7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "C",
          "3": "C"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "F",
          "3": "F#o7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "C",
          "3": "Am7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Dm7",
          "2": "G7",
          "3": "C"
        }
      }
    ],
    "interlude": [
      {
        "bar": 1,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      }
    ],
    "clarinet_A": [
      {
        "bar": 1,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Am"
        }
      }
    ],
    "clarinet_A1": [
      {
        "bar": 1,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 2,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 3,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 4,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 5,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 6,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 7,
        "beats": {
          "1": "Am",
          "3": "E7"
        }
      },
      {
        "bar": 8,
        "beats": {
          "1": "Am"
        }
      }
    ]
  },
  "notation_notes": {
    "harmonic_note": "Composer annotation: 'The chord changes of ① and ② are written particularly for the bass line. The real harmony is not very different from a C Maj. chord all the time.'",
    "form_note": "Form 26 A B C: A section = 16 bars (played as A A = two 8-bar repeats), B = 4 bars, C = 6 bars. Section ② = repeat of ①. Section ③ = clarinet solo section (16 bars, Am/E7 vamp).",
    "performance_note": "BACK TO INTRODUCTION, THEN BACK TO ① ONCE WITH Rall... at the end. SOLOS ON ②."
  }
}
```

### Annie Laurie

Demonstrates the **`recordings`** and **`variants`** fields (§13). The right-margin performer/year
credits become `recordings` — one string per printed line. The page carries **two** alternates below
the grid, each with its own marker: a `*`-marked `VARIANTE Bar 6` (three replacement bars `Am D7 G7`)
and an `①`-marked `VARIANTE Bar 27` (two bars, each a **Case-2 diagonal split**, e.g. `C`/`E7`). Each
`variants` entry carries its `marker`, its verbatim `applies_to` reference, and its `bars` in the same
shape as a section, while `sections` keeps the **original** chords unchanged. Also shows a
**brief-extension split** (`B` bar 5 is printed `Am` over a bare `7`, i.e. root carried over →
`{ "1": "Am", "3": "Am7" }`) — two beats that must **not** be collapsed into a single `Am7` (§9
Case 2 / §10 exception); an unusual **style label**
(`OLD SCOTCH SONG`); an **augmented triad** (`C 5+` → `C+`); a **full-section repeat** where `A'`
copies `A` via the leading arrow (dashes expanded); and a **parenthesised optional chord** (`(G7)`)
recorded in `notation_notes`.

```json
{
  "title": "Annie Laurie",
  "composer": "Lady J.D. Scott",
  "style": "OLD SCOTCH SONG",
  "tempo": "MEDIUM",
  "form": "32 A A' B C",
  "time_signature": "4/4",
  "page": 20,
  "source": "Anthologie des grilles de jazz",
  "sections": {
    "A": [
      { "bar": 1, "beats": { "1": "C" } },
      { "bar": 2, "beats": { "1": "C+" } },
      { "bar": 3, "beats": { "1": "F" } },
      { "bar": 4, "beats": { "1": "F#o7" } },
      { "bar": 5, "beats": { "1": "C" } },
      { "bar": 6, "beats": { "1": "D7" } },
      { "bar": 7, "beats": { "1": "G7" } },
      { "bar": 8, "beats": { "1": "G7" } }
    ],
    "A1": [
      { "bar": 1, "beats": { "1": "C" } },
      { "bar": 2, "beats": { "1": "C+" } },
      { "bar": 3, "beats": { "1": "F" } },
      { "bar": 4, "beats": { "1": "F#o7" } },
      { "bar": 5, "beats": { "1": "C" } },
      { "bar": 6, "beats": { "1": "G7" } },
      { "bar": 7, "beats": { "1": "C" } },
      { "bar": 8, "beats": { "1": "C" } }
    ],
    "B": [
      { "bar": 1, "beats": { "1": "C" } },
      { "bar": 2, "beats": { "1": "G7" } },
      { "bar": 3, "beats": { "1": "C" } },
      { "bar": 4, "beats": { "1": "C" } },
      { "bar": 5, "beats": { "1": "Am", "3": "Am7" } },
      { "bar": 6, "beats": { "1": "F" } },
      { "bar": 7, "beats": { "1": "E7" } },
      { "bar": 8, "beats": { "1": "E7" } }
    ],
    "C": [
      { "bar": 1, "beats": { "1": "F" } },
      { "bar": 2, "beats": { "1": "F#o7" } },
      { "bar": 3, "beats": { "1": "C" } },
      { "bar": 4, "beats": { "1": "G7" } },
      { "bar": 5, "beats": { "1": "C" } },
      { "bar": 6, "beats": { "1": "G7" } },
      { "bar": 7, "beats": { "1": "C" } },
      { "bar": 8, "beats": { "1": "C" } }
    ]
  },
  "recordings": [
    "D.Byas 45- J.Forrest 61- T.Grimes 48",
    "B.Myers 45- J.Newman 55- T.Dorsey 38",
    "J.Lunceford 37.42- W.Manone 36- B.May 57",
    "M.Sullivan 37- F.Waller 39",
    "C.Basie/F.Sinatra 62"
  ],
  "variants": [
    {
      "marker": "*",
      "applies_to": "Bar 6",
      "bars": [
        { "bar": 1, "beats": { "1": "Am" } },
        { "bar": 2, "beats": { "1": "D7" } },
        { "bar": 3, "beats": { "1": "G7" } }
      ]
    },
    {
      "marker": "①",
      "applies_to": "Bar 27",
      "bars": [
        { "bar": 1, "beats": { "1": "C", "3": "E7" } },
        { "bar": 2, "beats": { "1": "Am", "3": "Fm6" } }
      ]
    }
  ],
  "notation_notes": {
    "conversions": "Augmented triad written with superscript '5+' normalised to '+' (C 5+ -> C+).",
    "C_bar4": "Section C bar 4 is printed parenthesised '(G7)', an optional/passing chord; encoded as G7.",
    "composer_date": "Composer's dates unknown - the header prints '(Lady J.D. Scott, ?)'."
  }
}
```

---

## Appendix E — Common mistakes (anti-patterns to avoid)

These are the recurring errors a transcriber (human or model) makes on this book. Each maps to a
rule above; avoid all of them.

1. **Emitting a bare string for a whole-bar chord.** Wrong: `"Db"`. Right: `{ "bar": 1, "beats": {
   "1": "Db" } }`. Every bar is an object with a `beats` map (§8.3).
2. **Leaving shorthand in the output.** Never emit `-`, `%`, `•/•`, `→`, or arrows — expand them to
   explicit chords (§11). A `-` means "repeat the previous bar," not a literal value.
3. **Padding held chords onto later beats.** Encode a beat only where a chord visibly begins there
   (§8.4). Do not turn `{ "1": "C" }` into `{ "1": "C", "2": "C", "3": "C", "4": "C" }`.
4. **Collapsing a re-struck or brief-extension split.** If the box visibly re-writes the same chord
   in a later region, keep it (`{ "1": "C", "3": "C" }`). Likewise when a divided box names a chord in
   one region and a bare added degree in the other (e.g. `Am` over `7`), it is a two-beat split with
   the root carried over → `{ "1": "Am", "3": "Am7" }`, never a single `Am7`. Transcribe what is
   written, not what theory would simplify (§8.4, §9 Case 2, §10).
5. **Reaching across a subdivision line for an alteration.** A `b5` in the lower-right region
   belongs to the lower-right chord, never to the upper-left one (§10).
6. **Confusing the Case-3 inset (beat 4) with a Case-2 diagonal (beat 3).** Inset square → `"4"`;
   diagonal → `"3"`. If genuinely unsure, default to diagonal/beat 3 and add a note (§9 Case 3).
7. **Using primes in section keys.** Keys are `A`, `A1`, `A2`; primes live only in `form` (§8.1).
8. **Keeping parenthesised alterations.** `D9(b5)` → `D9`; `G7(F7)` → `G7` (§12). Parentheses mean
   optional → omit.
9. **`B` vs `Bb` slips.** Always check for the flat on the root (§12).
10. **Dropping the recordings or variants, or folding a variant into the main grid.** Margin credits
    go in `recordings`; `VARIANTE`/`STATEMENT` bars go in `variants` with their bar reference, while
    `sections` keeps the original chords unchanged (§13).
11. **Inventing chords for a grid-less tune.** Set `sections: {}` and add `no_chord_grid`; never
    fabricate changes (§15).
12. **Outputting an array, prose, or a markdown fence.** Emit exactly **one bare JSON object** (§6,
    §17). No ```json fences, no commentary.
13. **Emitting `null`/`""` for an absent optional field.** Omit the key entirely (§6.2).
14. **Re-reading the title from the image.** The title is supplied by the runner from the manifest;
    do not output or alter it (§5).

---

## Appendix F — Glossary

* **Tune / chart** — one song's chord grid; one cropped PNG = one tune = one output JSON.
* **Grid** — the table of boxes holding the chord symbols.
* **Section** — one printed row of the grid (e.g. an A section, a verse, an intro). One section ID =
  one printed row (§8.1).
* **Bar (measure)** — one box in a section's row; 1-indexed within its section, restarting each
  section.
* **Beat** — a position within a bar, keyed `"1"`–`"4"`; where a chord begins (§8.3–§8.4).
* **Form** — the section map of the tune as printed (e.g. `32 A A B A`); primes (`A'`) appear here
  only.
* **Head** — the main statement of a tune (as opposed to solos); these grids are heads.
* **Style** — the upper-left genre label (`DIXIELAND`, `SWING`, `STANDARD`, `ELLINGTONIA`, …).
* **Cross-reference** — a titled line that points to another tune ("X → SEE Y") with no grid of its
  own; skipped unless `--keep-crossref` (§15).
* **Cacheable prefix** — the stable system-prompt content (rulebook §8–§15 + Appendix D) reused
  unchanged across every call, billed at the cache rate once it exceeds the platform minimum (§18.3).
* **Work unit** — one crop → one JSON; the atomic, resumable, independent task (§1, §4.2).
