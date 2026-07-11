# grilles_digitizer

Digitize the *Anthologie des grilles de jazz*: the book's handwritten **chord
grilles** become structured JSON, the companion manuscript's **melodies** become
ABC notation, and a static web app browses the whole corpus with chord grid and
lead sheet side by side.

New here (or back after a while)? Start with the
[user manual](docs/USER_MANUAL.md) — the human-centric tour of the data flow,
the apps, the similarity engine, the "I changed X, what do I run?" recipes,
and where the Claude API is used and what it costs.

## Repository map

```
sources/                local-only book scans (gitignored — supply these yourself):
                        AGJ.pdf, AGJ_index.pdf, AGJ_Melody.pdf, AGJ_Melody_Index.pdf
pipelines/
  chords/               chord pipeline code (stages: see pipelines/chords/README.md)
  melody/               melody pipeline code (stages: see pipelines/melody/README.md)
  build_title_index.py  joins the two datasets by normalized title
apps/
  verifier/             Flask app for human review of chord JSON (chords stage 3)
  displayer/            static web app deployed to GitHub Pages (final stage)
data/
  title_index.csv       chord↔melody pairing, one row per tune (generated)
  chords/  crops/ → raw/ → wip/ → verified/
  melody/  crops/ → debug/ → wip/ → verified/
docs/
  specs/                the four implementation specs
  displayer_reference.png  visual reference for the rendered chord grid
```

Both pipelines share one data-flow convention under `data/<pipeline>/`:

| Folder | Meaning | Tracked? |
|---|---|---|
| `crops/` | one PNG per tune, cut from the book scan | yes |
| `raw/` (chords) / `debug/` (melody) | machine output, regenerable | no (gitignored) |
| `wip/` | human edits under review | yes |
| `verified/` | approved ground truth | yes |
| `05_annotated/` (chords) | verified + key/fingerprint annotations | yes |
| `06_similarity/` (chords) | similarity engine output, regenerable | no (gitignored) |

`data/chords/02_raw/` is **read-only source material** for the review apps — edits
live in `wip/` until a tune is promoted to `verified/`.

## Install

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # or: pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...             # needed for the VLM stages only
```

Run every command below from the repo root.

## Chords pipeline — run order

| Stage | Command | Input → Output |
|---|---|---|
| 1 crop | `python pipelines/chords/crop_tunes.py sources/AGJ.pdf --out data/chords/01_crops --start-page 7 --full-width --index sources/AGJ_index.pdf` | AGJ.pdf → `data/chords/01_crops/*.png` + `manifest.csv` |
| 2 transcribe | `python pipelines/chords/transcribe.py` | crops → `data/chords/02_raw/*.json` (VLM, resumable) |
| 3 verify | `python apps/verifier/verify_app.py` | raw → `wip/` (edits) → `verified/` (approved) |
| 4 index | `python pipelines/build_title_index.py` | both crops dirs → `data/title_index.csv` |
| 5 publish | `python apps/displayer/build_data.py`, then push `main` | verified + index → displayer bundle → GitHub Pages |
| 6 annotate keys | `python pipelines/chords/annotate_keys.py` | verified → `data/chords/05_annotated/*.json` (two-voter key annotation, resumable) |
| 7 verify keys | `python apps/key_verifier/key_verify_app.py` | human review of the key annotations |
| 8 similarity | `python -m pipelines.chords.similarity.compute` | `05_annotated` → `data/chords/06_similarity/` (regenerable) → rebundled by stage 5 |

Details, options, and helper tools: [pipelines/chords/README.md](pipelines/chords/README.md).

## Melody pipeline — run order

| Stage | Command | Input → Output |
|---|---|---|
| 0 crop | `python pipelines/melody/melody_cropper.py sources/AGJ_Melody.pdf --pages 7..972 --melody-index sources/AGJ_Melody_Index.pdf --index sources/AGJ_index.pdf` | AGJ_Melody.pdf → `data/melody/01_crops/*.png` |
| 0b deskew (one-off) | `python pipelines/deskew_crops.py data/melody/01_crops` | crops fixed in place |
| 1 straighten | `python pipelines/melody/melody_straightener.py data/melody/01_crops` | crops → `data/melody/debug/<id>/` |
| 2–5 | *not yet implemented* — symbol extraction, bar assembly, VLM adjudication, validation (see [docs/specs/melody_digitizer_spec.md](docs/specs/melody_digitizer_spec.md)) | → `data/melody/03_wip/*.abc` → human review → `verified/` |
| then | chords stages 4–5 pick up any `verified/*.abc` automatically | |

Details: [pipelines/melody/README.md](pipelines/melody/README.md).

## Deploy the displayer

Pushing to `main` deploys automatically via GitHub Actions
([.github/workflows/deploy-pages.yml](.github/workflows/deploy-pages.yml) uploads
`apps/displayer/`). Manual alternative and build details:
[apps/displayer/README.md](apps/displayer/README.md).
