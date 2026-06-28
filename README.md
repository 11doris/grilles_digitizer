# grilles_digitizer

Transcribe **already-cropped** per-tune images from *Anthologie des grilles de jazz*
(handwritten chord grids) into one structured JSON file per tune, using a
vision-language model (Claude). Implements
[`Instructions/jazz_chord_digitization_spec.md`](Instructions/jazz_chord_digitization_spec.md).

Cropping and locating are **out of scope** — the input is the finished `crops/*.png`
plus `crops/manifest.csv`. The runner is a resumable batch orchestrator around a
single per-crop VLM transcription call.

```
crops/*.png + manifest.csv  ->  TRANSCRIBE (VLM, 1 call/crop)  ->  VALIDATE  ->  tunes/*.json
```

## Install

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # or: pip install -e .
```

Set your Anthropic API key (the SDK also accepts an `ant auth login` profile):

```sh
export ANTHROPIC_API_KEY=sk-ant-...
```

## Run

```sh
python transcribe.py --crops crops/ --manifest crops/manifest.csv --out tunes/
```

Re-running the same command **resumes**: any tune whose `tunes/<stem>.json` already
exists and still validates is skipped, so you can stop (Ctrl-C, lid close, power
loss) and continue across sittings — at most the one tune in flight is redone.

### Options (Appendix B of the spec)

| Flag | Default | Purpose |
|---|---|---|
| `--crops DIR` | `crops` | Directory of per-tune PNGs |
| `--manifest FILE` | `<crops>/manifest.csv` | The work list (one row per crop) |
| `--out DIR` | `tunes` | Output directory |
| `--model ID` | `claude-opus-4-8` | VLM model id |
| `--workers N` | `1` | Parallel calls. Keep `1` for a local model; raise to 2–4 **only** for a remote API |
| `--retries R` | `3` | Per-unit validation retries (progressively stricter reminder) |
| `--dilate N` | `1` | Ink-thickening iterations before the call (`0` to disable, `2` for very thin scans) |
| `--max-long-edge PX` | `1100` | Downscale the long edge to this before the call (never upscales) |
| `--max-output-tokens N` | `1200` | Output token cap (≈1200 covers a busy tune; minified JSON) |
| `--page-range A:B` | — | Limit a session to tunes whose `page` is in `[A, B]` |
| `--delay S` | `0` | Sleep between units |
| `--only FILE` | — | Restrict to one `current_file` (debugging) |

Run the book in slices with `--page-range`, or just stop and re-run — resume makes
that free; no sharding is needed on a single machine.

## Outputs

```
tunes/<stem>.json          one accepted tune (minified, single bare object)
tunes/<stem>.error.json    stub for a unit that failed after all retries
tunes/run_state.jsonl      append-one-line-per-unit progress log (resumable, auditable)
tunes/run_report.json      final summary + everything a human should review
```

`run_report.json` flags each tune that trips any of: `title_uncertain`, a
`no_chord_grid` note, low manifest confidence (`review == yes` or `conf < 0.5`),
a `?`-marked chord, or an error.

## How it works

- **Image prep** ([`images.py`](grilles_digitizer/images.py)) — grayscale, optional
  single binary dilation of the ink, then downscale the long edge. No
  super-resolution (it hallucinates strokes on thin handwriting).
- **Prompt** ([`prompt.py`](grilles_digitizer/prompt.py)) — a large static system
  block carrying the full schema and notation rules (canonical chord vocabulary,
  bar-subdivision layouts, repeat expansion). It is byte-identical across calls and
  sent with **prompt caching**, so it is billed roughly once. Only the tiny
  title/page anchor varies per call.
- **Call** ([`vlm.py`](grilles_digitizer/vlm.py)) — one `messages.create` per crop,
  temperature 0 where the model allows it, with short exponential backoff on
  transient failures (429/5xx/connection).
- **Validate** ([`validation.py`](grilles_digitizer/validation.py)) — the per-tune
  self-check from spec §17 (single bare object, required fields present, optional
  fields omitted not null, `source`/`page` correct, every bar an object with `1`–`4`
  beat keys, no unexpanded shorthand, no prime section keys, no `fingerprints`,
  `sections == {}` iff a `no_chord_grid` note). Failure triggers a retry; exhausting
  retries writes an `.error.json` stub and the batch continues.
- **Resume & isolation** ([`runner.py`](grilles_digitizer/runner.py)) — the presence
  of a valid output file is the only source of truth; writes are atomic (temp +
  rename); one failing unit never stops the run.

## Cost notes

The defaults follow the spec's cost levers: downscaled images (`--max-long-edge`),
cached static instructions, minified output with a tight token cap. For a
cheap-first pass, run the book on a smaller `--model`, then re-run only the flagged
tunes (resume touches just the unfinished subset) on a stronger model.
