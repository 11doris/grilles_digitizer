# Melody Digitizer v5 — Implementation Plan

Status: proposed (2026-07-17). Supersedes the CV stages 2–4 of
`melody_digitizer_spec.md`; keeps its stage 0–1 machinery, ABC/file
conventions, validation ideas, and ops design. The `melody-digitizer-v2`
branch (v3/v4 CV trials, 26–40 % exact bars) is abandoned — do not port
its readers; only the ground-truth scoring *concept* survives.

## 0. Decision record

- **VLM-first reading.** The manuscript is read by a vision model
  (default `claude-opus-4-8`), not by OpenCV symbol extraction. Two
  failed CV attempts + a Fable-5 blind-read demo (CLOSE_YOUR_EYES,
  2026-07-17) showed the residual difficulty is *musical context*, not
  edge detection: nearly all demo errors were ±1-step notehead
  (line-vs-space) calls that the owner resolves by knowing the tune.
- **Deterministic everything else.** Structure, headers, unrolling,
  validation, rendering, scoring, batching are plain Python — the model
  only ever answers "what notes are printed here".
- **Owner's format changes** (override the old spec): no chords in the
  ABC; repeats/voltas unrolled into written-out sections; coda written
  as printed (not unrolled); metadata from the chords JSON.
- **Budget:** < $0.20/tune hard cap (enforced by a per-tune call cap).
- **Benchmark gate:** nothing runs at corpus scale until the pipeline's
  accuracy on the 14 owner-verified tunes is measured and accepted.

## 1. Module layout

Mirror `pipelines/chords/digitizer/` (config/manifest/images/prompt/
vlm/validation/output/runner/batch/cli):

```
pipelines/melody/digitizer/
  config.py       # Config dataclass: model, workers, caps, paths
  manifest.py     # WorkUnit discovery: crops ⋈ title_index ⋈ chords JSON
  skeleton.py     # headers + unrolled section/bar scaffold from strains
  strips.py       # staff bands, per-system overlay strips, zoom crops
                  #   (port scratchpad overlay_systems.py / zoom.py)
  measure.py      # barline candidates + calibrated head-centroid votes
                  #   (port scratchpad measure.py; add staff-line removal
                  #    + per-system calibration against known heads)
  prompt.py       # read-pass and repair-pass prompt builders
  vlm.py          # Anthropic client wrapper (reuse chords vlm.py shape)
  validation.py   # ABC parse, bar sums, section counts, range, ties,
                  #   beaming-style check, accidental sanity
  merge.py        # bar-level diff of two reads; flagged-bar list
  output.py       # write 03_wip/<stem>.abc + debug artifacts
  score.py        # benchmark vs data/melody/04_verified (bar/note level)
  render.py       # abcjs lead sheet HTML + headless-Edge screenshot
  runner.py       # resumable orchestrator, retries, state, cost log
  cli.py          # python -m pipelines.melody.digitizer ...
```

Artifacts:

- ABC → `data/melody/03_wip/<melody-stem>.abc` (never 04_verified —
  promotion is the owner's move alone)
- lead sheets → `data/melody/leadsheets/<stem>.html` (+ screenshot)
- debug (strips, overlays, flagged-bar crops, raw model output) →
  `data/melody/debug/<stem>/`
- state/cost → `data/melody/03_wip/run_state.jsonl`, `run_report.json`

## 2. Work-unit discovery (`manifest.py`)

A tune is processable when all three exist:

1. `data/melody/01_crops/<melody-stem>.png` (1449 exist — all crops done)
2. a `match_status == both` row in `data/title_index.csv`
3. `data/chords/05_annotated/<chords-stem>.json` (196 today; corpus
   processes in waves as chords digitization progresses)

Support `data/melody/overrides/<stem>.json` for tunes whose melody
structure genuinely differs from the grille (v4 needed this); an
override can adjust section order/bar counts/pickup and is applied by
`skeleton.py`.

## 3. Deterministic skeleton (`skeleton.py`)

From the chords JSON (`title`, `composer`, `year`, `style`, `tempo`,
`time_signature`, `key`, `strains[].parts[]`):

- **Headers:** `X:1`, `T:`, `C:` composer (– year), `O:` chords-page +
  melody-crop pointer (copy the exact format of the verified files),
  `R:` style/tempo lowercased, `M:`, `L:1/8`, `K:`.
- **`K:` = the printed signature, not the analyzed key.** F-minor tunes
  in this book are written with reduced signatures (CLOSE_YOUR_EYES:
  one flat) and inline accidentals; the owner's verified file keeps
  `K:F`. The model reports the printed signature during the read pass;
  skeleton uses it, and validation warns if it clashes with `key` by
  more than the usual relative/reduced-signature patterns.
- **Section plan:** one section per strains part, unrolled. Labels
  `"^A" "^A1" "^B" "^A2"…` in part order (verse parts as `"^verse_A"`
  like AIN'T MISBEHAVIN'). Expected bar counts per section come from
  `parts[].bars` and are non-negotiable for validation.
- **Unrolling rule:** printed `|:` `:|` + voltas map to written-out
  parts (shared bars repeated verbatim; volta 1 fills the first part's
  tail, volta 2 the second's). A pickup/anacrusis is written once
  before the first section (`c3 G ||"^A" …`); on the repeat pass the
  pickup notes typically reappear inside the last shared bar — the
  model is told this pattern explicitly. Codas: transcribe as printed,
  no unrolling.
- **Layout:** one ABC source line = 4 bars; `||` at section ends,
  `|]` final.

## 4. The two LLM read passes

**Pass A — full crop.** The native-resolution PNG (≈2274×2500, fits
Opus 4.8 high-res vision) + the skeleton + instructions. Output: ABC
bodies for every bar of the unrolled skeleton, plus per-system printed
key signature, plus a self-reported uncertainty list (bar numbers).

**Pass B — per-system strips.** Same question, different evidence:
the straightened/overlaid system strips from `strips.py` (red staff
lines, green space dashes — the demo showed these make pitch reading
tractable), sent as multiple images in one call. Decorrelating the
evidence matters because ±1-step errors are *correlated* between two
reads of identical pixels.

**Prompt content (both passes), the load-bearing parts:**

1. **Tune identity prior:** "This is <TITLE> (<composer>, <year>), the
   jazz standard. Use your knowledge of the tune to resolve ambiguous
   noteheads, but transcribe the *printed* page — it may deviate from
   versions you know."
2. **Chord anchors:** per-bar chords from the JSON ("bar 3 sits over
   Gm7b5") — for locating bars and as a harmonic tiebreak. Never
   transcribed into the output.
3. **Writer profile** (from the demo; grow it as tunes verify):
   noteheads often hang low in their space / sit ambiguously between
   line and space — when in doubt prefer the reading consistent with
   the tune and the chord; slash-shaped heads on lines read ~1 step
   high; the chord-text `+` (C7+) mimics a ledgered notehead below the
   staff; quarter rests are tall `ȝ` zigzags, eighth rests small
   `7`-shapes around C5; courtesy/redundant flats are common;
   accidentals may sit above the note; beamless tuplets under `⌐3¬`
   brackets are quarter-note triplets, beamed ones eighth triplets;
   ties across barlines and system breaks (stub arcs) occur.
4. **ABC house style:** `L:1/8`; uppercase = C4 octave, lowercase = C5,
   `,`/`'` beyond; **adjacency = beaming — reproduce the manuscript's
   beam groups exactly, never space every note**; explicit accidentals
   exactly as printed (courtesy ones included); tie targets written
   plain (no re-accidental: `_B8- | B2 …`); `(3xyz` eighth triplets,
   `(3X2Y2Z2` quarter triplets; rests `z`, invisible `x` only for
   declared-missing sections; durations must sum to the meter.
5. **One worked example** from `04_verified` (few-shot): 17_01 (verse +
   voltas) or 318_02 (pickup) — chosen to match the tune's features.
6. Output contract: fenced ABC only, bars exactly matching the skeleton
   counts, uncertainty list at the end as `% flag: <bar#> <reason>`.

**Merge (`merge.py`):** normalize (strip spaces inside beam groups for
comparison, canonical accidentals) and diff bar-by-bar. Agreement →
accept. Disagreement, validation failure, or self-flag → flagged bar.

**Repair pass** (only if flagged bars exist; one batched call): per
flagged bar send the zoomed overlay crop (4×, the demo's `zoom.py`
output), both candidate readings, the calibrated head-centroid
measurements from `measure.py` as a stated third opinion, the chord,
and the constraint "must sum to 8". Ask for a decision per bar.
Unresolved bars stay flagged in the output file.

**Model config:** `claude-opus-4-8`, adaptive thinking, max ~2500
output tokens/read. Per-tune cap: 5 calls (2 reads + repair + 1
validation re-ask + 1 spare); tunes exceeding the cap go to the manual
queue with whatever is best-so-far. Interactive mode for ≤ ~20 tunes,
Batch API (50 % price) beyond — copy `batch.py` from chords.

## 5. Validation (`validation.py`)

Hard failures (trigger one re-ask, then flag):

1. ABC parses (tiny dialect parser — the demo's `validate_abc.py`
   grown up: tuplets, ties, accidentals, `x` rests).
2. Every bar sums to the meter. (This caught 3 short bars *in the
   owner's verified 149_01* — the check is proven.)
3. Bars per section == strains bar counts; sections all present or
   declared missing (`x8` bars + `% melody n/a` annotation).
4. Pitch range F3–C6.
5. Tie targets exist; same letter (accidental-carry per house rule).

Soft warnings (recorded, not blocking): leaps > octave, beam-style
check (an output where no two eighths are ever adjacent is rejected as
v2-style flat spacing), courtesy-accidental anomalies, `K:` vs
analyzed-key mismatch, repeated-section bars that differ between
passes of the same figure.

Render check: `render.py` builds the abcjs page (vendored
`apps/displayer/vendor/abcjs-basic-min.js`), screenshots with headless
Edge (`--headless=new`, fresh profile), asserts non-blank and expected
system count.

## 6. Benchmark (`score.py`) and the gate

Compare `03_wip` output against `data/melody/04_verified` for the 14
verified tunes (owner's file wins on every discrepancy; known issue:
149_01 bars 4/12/28 are short in the verified file — ask the owner to
confirm the intended `(3B2c2B2 (3G2B2G2` before scoring against it,
and whether the bridge label there should be `^B` instead of `^C`).

Metrics per tune and aggregate:

- exact-bar rate (normalized: whitespace-insensitive, beam-agnostic)
- note-level pitch accuracy / rhythm accuracy (aligned per bar)
- error taxonomy: ±1-step pitch, octave, duration, tuplet-type,
  missing/extra note, structure
- flagged-bar precision/recall (do flags cover the actual errors?)

**Gate:** run Phase 3 on all 14, review with the owner. Reference
points: solo cold read = 24 % exact bars; v4 CV = 26–40 %. The corpus
run needs owner sign-off on the measured number — target discussion
range ≥ 85 % exact bars with flags covering most of the rest. If the
ensemble plateaus below an acceptable bar, stop and report — do not
burn budget.

## 7. Review loop (unchanged from spec §10, trimmed)

Review page per tune: manuscript system strips above an abcjs editor
(textarea ⇆ score, cursor↔note highlight) + synth playback + the
flagged-bar list front and center. Reviewer fixes are one-character ABC
edits; ±1-step errors are audible instantly. Approving = owner moves
the file to `04_verified` (never the pipeline).

## 8. Cost & runtime (Opus 4.8, interactive)

| Item | Tokens (in/out) | Cost |
|---|---|---|
| Read A (full crop) | ~5K img + 3K text / ~1.2K | ~$0.07 |
| Read B (strips) | ~6K imgs + 3K text / ~1.2K | ~$0.08 |
| Repair (when needed) | ~4K / ~0.5K | ~$0.03 |
| **Per tune (typical)** | | **~$0.15–0.18 interactive, ~$0.08–0.09 batch** |

Under the $0.20 cap with the call limiter; Sonnet 5 halves it if the
benchmark shows parity (measure, don't assume). Wall clock ≈ 3–6
min/tune sequential, ~15–20 min for 10 tunes with 3–4 workers.

## 9. Phases

1. **Deterministic core (no API):** manifest, skeleton, strips,
   measure, validation, render, score. Prove: skeleton+headers for the
   14 verified tunes byte-match their headers/section plans; validator
   reproduces the 149_01 short-bar finding.
2. **Single-read E2E:** wire prompt+vlm+output; run 2–3 verified tunes
   interactively; eyeball lead sheets.
3. **Dual-read + repair + benchmark:** run all 14, produce the
   accuracy report, owner gate.
4. **Corpus wave 1:** the ~196 tunes with annotated chords, batch
   mode, resumable; review queue ordered by flag count.

## 10. Decisions (owner, 2026-07-17)

1. **149_01 fixed** — A3 bars are now `(3B2c2B2 (3G2B2G2`, bridge label
   `^B`; re-validated clean (33 bars, all sums OK). Ground truth for
   the benchmark is trustworthy.
2. **`K:` = printed signature is the rule** (inline accidentals for
   reduced-signature tunes), as already implemented in §3.
3. **Go/no-go threshold: decided after measurement, not before.** The
   Phase-3 report presents exact-bar rate, note accuracy, error
   taxonomy, and flag coverage; the deciding metric is **expected
   review effort** — unflagged wrong bars per tune (flags make errors
   cheap to fix; silent errors are what cost verification time). Owner
   picks the bar looking at real numbers.
4. **Verse/structure mismatches → override file**
   (`data/melody/overrides/<stem>.json`, §2). No silent `x8` skipping;
   an override is an explicit operator decision per tune.
