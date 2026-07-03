# Melody Digitizer — Specification

Goal: extract the hand-written melodies from the AGJ melody manuscript
(`AGJ_Melody.pdf`, one tune per page or part-page, ~1400 tunes) and produce, per
tune, (a) a machine-readable melody JSON aligned with the already-digitized
chord grille (`tunes/<id>.json`), and (b) a self-contained static HTML lead
sheet (4 bars per row, title, chords above the staff).

This spec encodes everything learned from the manual pilot transcription of
`grilles_melody/9_04_AIN_T_MISBEHAVIN.json` + `aint_misbehavin_melody.png`
(result: `grilles_melody/aint_misbehavin_leadsheet.html`). The pilot took ~40
model-vision reads per tune; the pipeline below is designed to cut that to a
handful, doing everything deterministic in Python/OpenCV and reserving the
model API for the few decisions vision code cannot make reliably.

Repo constraints (must respect):
- `tunes/` is **read-only** ground truth for chords. Never modify it.
- New melody JSONs go to a new `melodies_wip/` directory; human-verified ones
  are promoted to `melodies_verified/` (mirror of the tunes_wip/tunes_verified
  convention).
- Rendered lead sheets go to `leadsheets/` (generated, can be rebuilt anytime).
- HTML verification: headless Edge
  (`msedge --headless=new --user-data-dir=<fresh tmp profile> --window-size=1300,2400 --screenshot=...`),
  window width ≥ 500.

---

## 1. Architecture overview

```
AGJ_Melody.pdf
   │  stage 0  (python, pdf → page PNGs, reuse extract_page.py/crop_tunes.py)
   ▼
page PNG (1-bit scan, ~2500×2600)
   │  stage 1  (python)   staff-system detection + per-system straightening
   ▼
straightened system strips + staff geometry (5 line y's, gap, target center)
   │  stage 2  (python)   symbol candidate extraction
   ▼
per-system symbol lists: barlines, filled/hollow noteheads (+staff step),
stems, flags/beams, rests, accidentals, ties, text zones (chords, title, labels)
   │  stage 3  (python)   bar assembly + rhythm solving against the tune JSON
   ▼
per-bar event candidates with confidence + list of UNRESOLVED bars
   │  stage 4  (model API, only for flagged bars)  annotated-crop adjudication
   ▼
melody JSON (melodies_wip/<id>.melody.json)
   │  stage 5  (python)   validation suite; failures loop back to stage 4 once
   ▼
lead sheet HTML (leadsheets/<id>.html) via the shared SVG engraver
```

Guiding principle: **the chord grille JSON is ground truth** for form, bar
counts, and chord/beat placement. The manuscript's own chord letters are used
only to align bars/beats (anchor points), never for output. Where the
manuscript's chords disagree with the JSON (it happens — e.g. AGJ grille says
`|Eb B9|E|E|E|` where the manuscript writes `|Eb B9|E|Eb B9|E|`), output the
JSON chords unchanged.

---

## 2. Stage 0 — Page/tune extraction (python only)

- Reuse `crop_tunes.py` / `extract_page.py` machinery (embedded 1-bit scan at
  native resolution, `to_ink` polarity fix). Produce one PNG per tune in
  `melody_crops/`, named like the grille crops (`<page>_<idx>_<TITLE>.png`) so
  tune JSON ↔ melody image pairing is a filename join.
- If the melody book has one tune per page, a manifest mapping printed page →
  tune id is enough; build it once by matching the hand-written title line
  (stage 2 text zone) against tune titles with fuzzy string match, and store it
  as `melody_manifest.json` for manual review. Title OCR is one cheap model
  call per page ONLY where fuzzy match on filename/page-order fails.

## 3. Stage 1 — Staff detection & straightening (python only)

Empirically validated on the pilot page:

1. **Find staff bands**: row-darkness histogram `dark[y] = count(px<128)`;
   rows with `dark > 0.45 * max` are line rows; group rows with gap ≤ 40 px →
   one band per system (pilot: 10 bands/page, ~97 px tall, gap ≈ 23–24 px).
2. **Do NOT rely on global deskew** — pages are globally straight but each
   staff is hand-drawn with local slant *and curvature*. Straighten per
   system, per column:
   - Crop band ± 100 px.
   - For windows of 40 px width every 20 px, compute the vertical ink profile
     and cross-correlate with a 5-spike comb at spacing `gap=(y1-y0)/4`,
     searching the comb center within ±45 px. Interpolate gaps (windows with
     < 1000 ink), median-smooth (k=9), then shift every pixel column
     vertically so the staff center sits at a fixed `target`.
   - Persist the straightened strip + `(target, gap)`. All later geometry is
     trivial: staff step of a y-coordinate = `(target - y) / (gap/2)`,
     step 0 = B4, +1 = C5, −2 = G4, etc.
3. Sanity check: after straightening, the 5 line peaks must be within ±2 px of
   `target + k*gap, k∈[-2..2]`; else flag the system for model review.

## 4. Stage 2 — Symbol candidates (python only)

All detectors operate on the straightened strip, binarized at 128.

- **Text zones**: connected ink below `target+2.5*gap` (chord letters of THIS
  system) and above `target-3*gap` (belongs to the system ABOVE, plus title on
  system 1). Extract chord-letter x-centers of the below-zone blobs; these are
  the **beat anchors** (chord label sits under/near its beat). Do not OCR them
  in python; their x-positions are what matters.
- **Barlines**: columns whose longest vertical ink run covers ≥ 95 % of the
  staff height (top line −4 px … bottom line +4 px), grouped within 6 px.
  Expect ~30 % misses and stem false-positives → treat as *candidates* only;
  final segmentation happens in stage 3 with priors.
- **Filled noteheads**: remove staff lines (morphological open, vertical 1×7),
  remove stems (open, ellipse 11×9), connected components with
  area 120–2000, w 12–60, h 8–40, |step| ≤ 6.4. Round step to nearest int.
  Known artifacts (must be classified, all seen in pilot):
  - *Flag blobs*: eighth-note flags of stem-up notes blob at step ≈ +3.5…+4
    within ~15 px of a real head → discard when adjacent to a lower head.
  - *Slash heads*: fast handwriting draws some heads as dashes lying ON a
    line; their blob centroid reads 0.5–1 step high (pilot: D5 quarters read
    as "E5 (2.6–3.1)"). Snap steps in [2.3, 3.2] down to the line below when
    the glyph is a thin diagonal (aspect ratio test).
  - *Chord text leakage*: |step| > 6.5 → discard.
  - *Accidental blobs*: naturals/flats produce 1–2 small blobs at the pitch
    they modify, ~20–40 px LEFT of the head (or ABOVE the staff, see below).
- **Hollow heads (half/whole)**: after line removal the rim splits into two
  arc blobs at the same x, ±1–1.5 steps apart → merge pairs (Δx < 12 px) into
  one hollow head at the mean. A hollow head with a stem = half, without =
  whole. (Pilot: whole notes at B4/C5/Eb4/G4 all detected this way.)
- **Stems & flags**: vertical runs 2–5 px wide, ≥ 2.2*gap long, touching a
  head; flag = short curved stroke at the far stem end. Beams = thick (>5 px)
  near-horizontal strokes connecting ≥ 2 stem ends (above for stem-up groups,
  below for stem-down). Beam membership ⇒ eighths.
- **Rests** (glyph zoo from the pilot — critical):
  - eighth rest: "7"-shape, small dot-blob around C5–E5 + slash tail; compact
    (≤ ~2.5 spaces tall).
  - quarter rest: "3"/"ȝ" zigzag, taller.
  - Distinguish rest-vs-slash-head by the presence of a dot blob + absence of
    a stem, and finally by the bar-sum solver (stage 3).
- **Accidentals**: ♮ = two parallel verticals + slanted box; ♭ = vertical +
  bowl. Search both LEFT of each head and DIRECTLY ABOVE it — this writer
  puts courtesy accidentals above the staff over the note (seen twice).
- **Ties/slurs**: thin arcs (low solidity, high width/height, curvature test)
  between two heads of the same step (tie) — pilot ties: within-bar
  (8th→half, quarter→half), across barlines (quarter→whole, whole→whole).
- **Repeats/endings**: double barline + two dots = `|:` / `:|`; horizontal
  bracket above the staff starting with a small "1"/"2" = volta. Map volta 1
  to section A bars 7–8 and volta 2 to A1 bars 7–8 (the grille JSONs already
  write the sections out separately, e.g. `A`, `A1`, `B`, `A2`).

## 5. Stage 3 — Bar assembly & rhythm solving (python only)

1. **Bar segmentation**: expected bar count per system comes from the tune
   JSON form (verse/chorus sections, usually 4 bars/system; endings compress
   to 2 short bars after the `:|`). Fuse barline candidates with two priors:
   chord-anchor x-positions (a bar contains the labels of its JSON beats) and
   roughly uniform bar widths. Dynamic programming over candidates; flag
   low-margin segmentations.
2. **Event ordering**: sort symbols by x within each bar; attach accidentals
   (left/above within window) and dots (small blob right of head at ±½ step)
   to heads; attach flags/beams to stems to fix eighths.
3. **Duration solving — the workhorse.** Assign durations so the bar sums to
   the time signature (eighth = 1 unit, 4/4 ⇒ 8 units):
   - Fixed by shape: whole = 8, hollow+stem = 4, beamed/flagged = 1,
     eighth rest = 1, quarter rest = 2, dot ⇒ ×1.5.
   - Un-flagged filled heads are ambiguous (quarter, or sloppy eighth) —
     solve by exact cover: choose durations from {1,2,3} for ambiguous items
     so Σ = 8. If several solutions, prefer (a) the solution matching a
     *rhythm-cell library* (see below), (b) onsets that put chord-change
     notes on their JSON beats, (c) fewer syncopations.
   - **Rhythm-cell library**: hand-written charts reuse cells heavily. Seed
     with the pilot's cells and grow it as tunes get verified:
     `♪♩♪♩♩` (onsets 1,1.5,2.5,3,4 — the entire AIN'T MISBEHAVIN' verse),
     `𝄾♪♪♪♩♩`, `𝄾♪♪♪(half)`, `♩𝄾♪‿(half)`, `♪♪♪♪♪♪𝄽`, whole, tied
     whole‿whole. Matching a known cell is strong evidence.
   - Same-section repetition prior: bars over identical chord cells inside a
     tune usually repeat the melody cell (pilot: bars 1/3/9/11 identical,
     chorus bar 5 = bar 1). Cross-check and reuse, but *verify* with the
     blob evidence — never copy blindly (pilot: final A2 bar 2 differed).
4. **Confidence scoring**: each bar gets a score from (segmentation margin,
   head-count vs solver agreement, unique duration solution?, accidental
   attachments unambiguous?, cell-library hit?). Bars below threshold →
   stage 4. Pilot experience says expect **10–30 % of bars flagged**, mostly
   rests-vs-slash-heads, accidentals, and hollow-note pitch.

## 6. Stage 4 — Model API adjudication (the ONLY model usage)

- For each flagged bar, render ONE annotated crop: straightened strip, 3–5×
  zoom, red lines drawn on the 5 staff lines, green dashes on spaces + first
  ledger positions (this overlay is what made the pilot reliable — pitch
  reading without it is guesswork).
- Prompt contains: the tune's key signature, time signature, the JSON chords
  of that bar (beats), the python pipeline's candidate reading(s) with the
  open question(s) ("is the glyph at x≈612 an eighth rest or a note-head on
  the D5 line?"), and the constraint that durations must sum to 8 eighths.
- Response format: strict JSON `{"events":[{"p":"Bb4","d":2,"a":"n","tie":false},
  {"r":true,"d":1}, ...]}` — same schema as the output file. Temperature 0.
- Batch several bars per request (one image per bar, up to ~6 images) to cut
  request overhead. Only send follow-up crops (higher zoom, raw un-straightened
  variant) for bars the model marks uncertain — the raw crop matters because
  straightening can smear glyph shapes (pilot: bar-13 rest/slash confusion).
- Budget: at 10–30 % flagged bars ≈ 5–15 bars/tune ≈ 2–3 batched calls/tune.
  Never send whole pages or whole systems to the model.

## 7. Melody JSON schema (`melodies_wip/<id>.melody.json`)

```json
{
  "id": "9_04_AIN_T_MISBEHAVIN",
  "title": "AIN'T MISBEHAVIN'",
  "key": "Eb", "time_signature": "4/4",
  "clef": "treble",
  "source": {"image": "melody_crops/9_04_....png", "book": "AGJ melody ms."},
  "sections": {
    "verse_A": [
      {"bar": 1,
       "events": [
         {"p": "G4",  "d": 1},
         {"p": "Bb4", "d": 2},
         {"p": "G4",  "d": 1},
         {"p": "B4",  "d": 2, "a": "n"},
         {"p": "B4",  "d": 2}
       ]}
    ],
    "A": [], "A1": [], "B": [], "A2": []
  },
  "status": {"transcribed": ["verse_A","verse_A1","A","A1"],
             "missing": ["B","A2"],
             "flags": [{"section":"A","bar":4,"note":"final rest length inferred"}]}
}
```

Conventions: `d` in eighths (1,2,3,4,6,8); pitches spelled with explicit
letter+octave, accidental `a` = "b"|"n"|"#" is the *written* accidental (key
signature is implied — a `Bb4` inside Eb major is written `{"p":"B4"}` with no
accidental; the natural is `a:"n"`); `tie: true` ties to the NEXT event;
rests `{"r":true,"d":n}`. Section names mirror the tune JSON's `sections`
keys exactly, and each section must contain exactly the number of bars the
grille has. A partial transcription is valid — renderer falls back to slash
notation for `missing` sections (as in the pilot HTML).

## 8. Stage 5 — Validation (python only)

Run on every generated JSON; failures send the bar back to stage 4 once, then
to a human-review queue:

1. Bar sums = time signature, every bar.
2. Bar counts per section = grille bar counts; sections present or declared
   `missing`.
3. Pitch range sanity: F3 … C6; leaps > octave flagged.
4. Accidental sanity: `a:"n"` only on letters flatted by the key sig (or
   canceling an earlier in-bar accidental); courtesy accidentals allowed.
5. Repeated-section diff: sections/bars over identical chord cells that
   differ melodically get a soft flag (usually fine, occasionally a stage-3
   error).
6. Tie sanity: tie targets exist and have identical pitch.
7. Render the HTML, screenshot with headless Edge, and run a trivial pixel
   check (non-blank, expected row count) — catches renderer/data crashes.

## 9. Renderer (shared, python-generated static HTML)

Factor the pilot's inline SVG engraver
(`grilles_melody/aint_misbehavin_leadsheet.html`) into a template:

- Self-contained single file: no external fonts/scripts/CSS. Music data is a
  JS object generated from the melody JSON; SVG built client-side.
- Fixed **4 bars per row**; clef + key signature on every row, time signature
  on the first; title / composer / meta / form header; section labels in red
  above the row; bar numbers in gray; footnotes for missing sections and
  grille variants (`variants` field of the tune JSON).
- Engraving rules implemented and adequate: stem up below middle line, stem
  down otherwise; flags (no beams needed); hollow half/whole; dotted values;
  ♭/♮/♯ via Unicode text; quarter/eighth rests as drawn paths (do NOT rely on
  U+1D13D/U+1D13E fonts); ties as quadratic arcs on the notehead side
  opposite the stem, cross-barline ties supported within a row; slash bars
  for untranscribed sections; final double bar.
- Chord text from the grille JSON only, positioned at `(beat-1)/8` of the bar
  width, `b→♭`, `#→♯` prettification.

## 10. Batch driver & ops

- `melody_digitizer.py run --pages 7..706` processes tunes resumably; state
  in `melody_state.json` (per tune: stage reached, flags, model-call count,
  cost). Idempotent re-runs skip completed stages.
- All intermediate artifacts (straightened strips, symbol overlays with the
  detected candidates drawn on them) cached under `melody_debug/<id>/` —
  these overlays are exactly what a human reviewer needs to verify a tune
  quickly, and what stage 4 sends to the model.
- Cost telemetry: log model calls/tune; alert if a tune exceeds ~6 calls
  (indicates a bad scan → route to manual queue instead of burning budget).
- Human verification loop: a tiny review page (reuse the displayer approach)
  showing manuscript strip above rendered row, per system, with
  approve/fix buttons; approved tunes move JSON → `melodies_verified/`.

## 11. Known hazards checklist (all observed in the pilot)

- Staff curvature within one system (not fixable by rotation alone).
- Chord labels of system N sitting directly above system N+1's staff.
- Manuscript chords ≠ grille chords → grille wins; use manuscript letters
  only as x-anchors.
- Accidentals written above the note instead of before it.
- Redundant courtesy flats (♭ on a key-sig-flatted note after a ♮ bar).
- Slash-shaped noteheads on lines (read ~1 step high by centroid).
- Stem-down flags drawn as hooks that look like handwriting flourish, and
  vice-versa — trust the bar-sum solver over the flag detector.
- Eighth-rest "7" vs quarter-rest "3" vs slash-head: solver + model tiebreak.
- Hollow-head rim splits into two blobs after line removal.
- 1st/2nd endings compress two bars into short spans right of the `:|`.
- Whole-note bars tied across the barline (endings) — tie arc is long/flat.
- The last chord bars of a tune may hold a single tied whole note while the
  grille still shows a turnaround (`|G7 C7|Eb|`) — melody and chords are
  independent streams; do not force a note onset per chord.
