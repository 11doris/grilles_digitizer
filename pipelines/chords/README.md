# Chords pipeline

Turns the scanned chord-grille book (`sources/AGJ.pdf`) into verified,
structured JSON — one file per tune. Run everything from the repo root.

| Stage | Script | Input → Output |
|---|---|---|
| 1 crop | `crop_tunes.py` | `sources/AGJ.pdf` + `sources/AGJ_index.pdf` → `data/chords/crops/*.png` + `manifest.csv` |
| 2 transcribe | `transcribe.py` (→ `digitizer/` package) | crops + manifest → `data/chords/raw/*.json` (VLM, one call per crop, resumable) |
| 3 verify | `apps/verifier/verify_app.py` | `raw/` → `wip/` (human edits) → `verified/` (approved) |
| 4 index | `../build_title_index.py` | chord + melody crops → `data/title_index.csv` |
| 5 publish | `apps/displayer/build_data.py` | `verified/` + index → displayer bundle |

Helpers (not stages):

- `extract_page.py` — save one full page PNG at crop resolution/polarity, for
  debugging crop coordinates against the manifest.
- `tools/build_examples.py` — regenerate `digitizer/examples.py` (the few-shot
  examples embedded in the cached system prompt) from `data/chords/verified/`.
- `tools/check_chord_syntax.py` — validate chord syntax in
  `data/chords/verified/` and `data/chords/wip/` against the prompt vocabulary.

## Stage 1 — crop

```sh
python pipelines/chords/crop_tunes.py sources/AGJ.pdf \
       --out data/chords/crops --start-page 7 --full-width --index sources/AGJ_index.pdf
```

Index-driven: the book index lists which titles are on each printed page, so the
image is only used to *locate* each known title. Resumable — rerun the same
command and pages whose crops exist are skipped. Rows flagged `review=yes` in
`manifest.csv` deserve a glance; fix the `title` column if wrong, then
`python pipelines/chords/crop_tunes.py --apply data/chords/crops/manifest.csv`.
See the docstring in [crop_tunes.py](crop_tunes.py) for all options.

## Stage 2 — transcribe (VLM)

Transcribe the **already-cropped** per-tune images into one structured JSON file
per tune, using a vision-language model (Claude). Implements
[docs/specs/jazz_chord_digitization_spec.md](../../docs/specs/jazz_chord_digitization_spec.md).

```sh
python pipelines/chords/transcribe.py
```

Re-running the same command **resumes**: any tune whose `data/chords/raw/<stem>.json`
already exists and still validates is skipped, so you can stop (Ctrl-C, lid
close, power loss) and continue across sittings — at most the one tune in
flight is redone.

### Options (Appendix B of the spec)

| Flag | Default | Purpose |
|---|---|---|
| `--crops DIR` | `data/chords/crops` | Directory of per-tune PNGs |
| `--manifest FILE` | `<crops>/manifest.csv` | The work list (one row per crop) |
| `--out DIR` | `data/chords/raw` | Output directory |
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
data/chords/raw/<stem>.json          one accepted tune (minified, single bare object)
data/chords/raw/<stem>.error.json    stub for a unit that failed after all retries
data/chords/raw/run_state.jsonl      append-one-line-per-unit progress log (resumable, auditable)
data/chords/raw/run_report.json      final summary + everything a human should review
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

Browses `data/chords/raw/`, saves edits to `data/chords/wip/`, promotes approved
tunes to `data/chords/verified/`. `raw/` itself is never modified. Spec:
[docs/specs/verification_app_spec.md](../../docs/specs/verification_app_spec.md).
