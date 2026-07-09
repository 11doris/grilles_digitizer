# Chords pipeline

Turns the scanned chord-grille book (`sources/AGJ.pdf`) into verified,
structured JSON — one file per tune. Run everything from the repo root.

| Stage | Script | Input → Output |
|---|---|---|
| 1 crop | `crop_tunes.py` | `sources/AGJ.pdf` + `sources/AGJ_index.pdf` → `data/chords/01_crops/*.png` + `manifest.csv` |
| 2 transcribe | `transcribe.py` (→ `digitizer/` package) | crops → `data/chords/02_raw/*.json` (VLM, one call per crop, resumable) |
| 3 verify | `apps/verifier/verify_app.py` | `raw/` → `wip/` (human edits) → `verified/` (approved) |
| 4 index | `../build_title_index.py` | chord + melody crops → `data/title_index.csv` |
| 5 publish | `apps/displayer/build_data.py` | `verified/` + index → displayer bundle |
| 6 annotate keys | `annotate_keys.py` (→ `key_annotation/` package) | `verified/` → `data/chords/05_annotated/*.json` (key, section keys, opening, fingerprint; resumable) |
| 7 verify keys | `apps/key_verifier/key_verify_app.py` | human review of `05_annotated` (needs-review queue first) |

Helpers (not stages):

- `extract_page.py` — save one full page PNG at crop resolution/polarity, for
  debugging crop coordinates against the manifest.
- `../deskew_crops.py` — shared, in-place deskew for crops that came out slanted
  (chords or melody); takes a file, glob, or directory. `--dry-run` first to see
  the estimated angles. Crops are 1-bit, so don't re-deskew a crop twice — if one
  pass isn't enough, re-crop from the PDF with `crop_tunes.py`.
- `tools/build_examples.py` — regenerate `digitizer/examples.py` (the few-shot
  examples embedded in the cached system prompt) from `data/chords/04_verified/`.
- `tools/check_chord_syntax.py` — validate chord syntax in
  `data/chords/04_verified/` and `data/chords/03_wip/` against the prompt vocabulary.

## Stage 1 — crop

```sh
python pipelines/chords/crop_tunes.py sources/AGJ.pdf \
       --out data/chords/01_crops --start-page 7 --full-width --index sources/AGJ_index.pdf
```

Index-driven: the book index lists which titles are on each printed page, so the
image is only used to *locate* each known title. Resumable — rerun the same
command and pages whose crops exist are skipped. Rows flagged `review=yes` in
`manifest.csv` deserve a glance. To fix a wrong title, simply rename the PNG —
the filename (`<page>_<index>_<TITLE_SLUG>.png`) is the source of truth for
stage 2; the manifest does not need to be kept in sync. (Alternatively, fix the
`title` column and run
`python pipelines/chords/crop_tunes.py --apply data/chords/01_crops/manifest.csv`,
which renames the files from the manifest.)
See the docstring in [crop_tunes.py](crop_tunes.py) for all options.

## Stage 2 — transcribe (VLM)

Transcribe the **already-cropped** per-tune images into one structured JSON file
per tune, using a vision-language model (Claude). Implements
[docs/specs/jazz_chord_digitization_spec.md](../../docs/specs/jazz_chord_digitization_spec.md).

```sh
python pipelines/chords/transcribe.py
```

The work list is discovered from the crop filenames themselves
(`<page>_<index>_<TITLE_SLUG>.png`); the JSON `title` and `page` are derived
from the name, with `manifest.csv` consulted only to restore spellings
(apostrophes etc.) the slug cannot encode. Renaming a crop is all it takes to
fix its title — but note the renamed file gets a new output stem, so it will be
re-transcribed on the next run.

Re-running the same command **resumes**: any tune whose `data/chords/02_raw/<stem>.json`
already exists and still validates is skipped, so you can stop (Ctrl-C, lid
close, power loss) and continue across sittings — at most the one tune in
flight is redone.

### Options (Appendix B of the spec)

| Flag | Default | Purpose |
|---|---|---|
| `--crops DIR` | `data/chords/01_crops` | Directory of per-tune PNGs |
| `--manifest FILE` | `<crops>/manifest.csv` | Optional; only restores original title spellings (missing is fine) |
| `--out DIR` | `data/chords/02_raw` | Output directory |
| `--model ID` | `claude-opus-4-8` | VLM model id |
| `--workers N` | `1` | Parallel calls. Keep `1` for a local model; raise to 2–4 **only** for a remote API |
| `--retries R` | `3` | Per-unit validation retries (progressively stricter reminder) |
| `--dilate N` | `1` | Ink-thickening iterations before the call (`0` to disable, `2` for very thin scans) |
| `--max-long-edge PX` | `1100` | Downscale the long edge to this before the call (never upscales) |
| `--max-output-tokens N` | `2500` | Output token **cap** (billed by actual use, not the cap; raise for very dense/multi-strain grids) |
| `--page-range A:B` | — | Limit a session to tunes whose `page` is in `[A, B]` |
| `--delay S` | `0` | Sleep between units |
| `--only FILE` | — | Restrict to one `current_file` (debugging) |
| `--sample N` | — | Randomly pick at most `N` crops whose tune is not yet decoded into `--out` |
| `--seed N` | — | RNG seed for `--sample` (reproducible selection) |

Run the book in slices with `--page-range`, or just stop and re-run — resume
makes that free; no sharding is needed on a single machine.

### Outputs

```
data/chords/02_raw/<stem>.json          one accepted tune (minified, single bare object)
data/chords/02_raw/<stem>.error.json    stub for a unit that failed after all retries
data/chords/02_raw/run_state.jsonl      append-one-line-per-unit progress log (resumable, auditable)
data/chords/02_raw/run_report.json      final summary + everything a human should review
```

`run_report.json` lists, for human review, each tune that trips any of:
`missing_required_field` (an always-present output field absent — accepted but
flagged), a `no_chord_grid` note (informational), or `errors` (never produced
valid JSON after retries).

### How it works

- **Image prep** ([digitizer/images.py](digitizer/images.py)) — grayscale,
  optional single binary dilation of the ink, then downscale the long edge. No
  super-resolution (it hallucinates strokes on thin handwriting).
- **Prompt** ([digitizer/prompt.py](digitizer/prompt.py)) — a large static
  system block: the full schema and notation rules (canonical chord vocabulary,
  bar-subdivision layouts, repeat expansion, multi-strain convention) **plus the
  worked examples** ([digitizer/examples.py](digitizer/examples.py), regenerated
  from the verified tunes by [tools/build_examples.py](tools/build_examples.py)).
  The examples serve as few-shot guidance and push the block comfortably past
  the **4,096-token cache minimum** so it caches on every platform (spec §5.1 /
  §18.3). It is byte-identical across calls and sent with **prompt caching**, so
  it is billed roughly once; run with `--debug` to see per-call
  `cache HIT/WRITE/MISS` stats. The model does **not** output
  `title`/`page`/`source` — the runner injects those (the title verbatim from
  the manifest). Only the page anchor varies per call (retry reminders go in the
  user turn so they never break the cache).
- **Call** ([digitizer/vlm.py](digitizer/vlm.py)) — one `messages.create` per
  crop, temperature 0 where the model allows it, with short exponential backoff
  on transient failures (429/5xx/connection). The tune is requested via **forced
  tool use** (a single `record_tune` tool), which guarantees structured JSON
  with no prose preamble on every model. A `max_tokens` cutoff is surfaced as a
  clear "raise --max-output-tokens" error.
- **Validate** ([digitizer/validation.py](digitizer/validation.py)) — the
  per-tune self-check from spec §17. Structural failures trigger a retry;
  exhausting retries writes an `.error.json` stub and the batch continues. A
  *missing always-present field* is non-fatal: the tune is accepted and flagged
  (`missing_required_field`).
- **Resume & isolation** ([digitizer/runner.py](digitizer/runner.py)) — the
  presence of a valid output file is the only source of truth; writes are atomic
  (temp + rename); one failing unit never stops the run.

### Cost notes

The defaults follow the spec's cost levers: downscaled images
(`--max-long-edge`), cached static instructions, minified output with a tight
token cap. For a cheap-first pass, run the book on a smaller `--model`, then
re-run only the flagged tunes (resume touches just the unfinished subset) on a
stronger model.

## Stage 3 — verify (human review)

```sh
python apps/verifier/verify_app.py
```

Browses `data/chords/02_raw/`, saves edits to `data/chords/03_wip/`, promotes approved
tunes to `data/chords/04_verified/`. `raw/` itself is never modified. Spec:
[docs/specs/verification_app_spec.md](../../docs/specs/verification_app_spec.md).

## Stage 6 — annotate keys (Phase 0 of the similarity spec)

```sh
python pipelines/chords/annotate_keys.py                # annotate everything pending
python pipelines/chords/annotate_keys.py --status       # per-status counts
python pipelines/chords/annotate_keys.py --set-key <stem> <tonic> <major|minor>
```

Implements Phase 0 of
[docs/specs/tune_similarity_spec.md](../../docs/specs/tune_similarity_spec.md):
every verified tune gets `key`, `section_keys` (modulating sections only),
`opening` (computed), `key_annotation` (both voter votes + status) and
`harmonic_fingerprint` added on top of a verbatim copy in
`data/chords/05_annotated/`. Two independent voters — a deterministic
functional scorer ([key_annotation/scorer.py](key_annotation/scorer.py)) and
one Claude call per tune ([key_annotation/llm.py](key_annotation/llm.py),
structured outputs; Batches API at ≥50 pending) — must agree, otherwise the
tune lands in `needs_review` for the key verifier app. Resumable: a tune is
skipped while its annotated file matches the source's sha256; editing a
verified source demotes it back to the machine statuses. **Never hand-edit
`05_annotated` files** — corrections go through the app or `--set-key`, which
recompute the derived fields.

## Stage 7 — verify keys (human review)

```sh
python apps/key_verifier/key_verify_app.py
```

Shows the original crop next to the resolved key, both votes and the
fingerprint; verify/correct writes back to `05_annotated` through the shared
update routine. Keyboard: `V` verify, `←`/`→` navigate.
