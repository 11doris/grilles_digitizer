# Melody pipeline

Turns the scanned melody manuscript (`sources/AGJ_Melody.pdf`, hand-written lead
sheets) into verified ABC notation — one `.abc` per tune, aligned with the
already-digitized chord grilles. Spec:
[docs/specs/melody_digitizer_spec.md](../../docs/specs/melody_digitizer_spec.md).
Convention: **one python file per stage**, each runnable standalone, sharing
helpers from the chords pipeline (`pipelines/chords/crop_tunes.py`). Run
everything from the repo root.

| Stage | Script | Input → Output |
|---|---|---|
| 0 crop | `melody_cropper.py` | `sources/AGJ_Melody.pdf` + both index PDFs → `data/melody/01_crops/*.png` + `melody_manifest.json` |
| 0b deskew (one-off) | `../deskew_crops.py` (shared) | `data/melody/01_crops/` fixed in place |
| 1 straighten | `melody_straightener.py` | crops → `data/melody/debug/<id>/` (strips, overlays, `stage1.json` geometry) |
| v5 digitizer | `digitizer/` (see below) | crops + chords JSON → `data/melody/03_wip/<id>.abc` |

The chord grille JSON (`data/chords/02_raw|verified/<id>.json`) is **ground truth**
for form, bar counts, and chords — never modify it from this pipeline.

## v5 digitizer (`digitizer/`) — structure scaffolds

The spec's CV stages 2–4 and a VLM-first read were both tried and both missed
the accuracy bar (see `docs/specs/melody_digitizer_v5_plan.md` and the Phase-3
benchmark: 0 % exact bars, ~16 % pitch, ~28 unflagged-wrong bars/tune — a human
would re-transcribe the whole tune). **The pipeline's product is therefore the
STRUCTURE, not the notes:** correct headers, section labels, and empty barlined
bars, generated deterministically from the chords JSON. A reviewer opens the
scaffold next to the manuscript crop and fills in the notes.

```sh
# generate a fill-in scaffold for every processable tune (zero API cost)
python -m pipelines.melody.digitizer.cli scaffold --all
# or one/a few tunes; --force overwrites existing 03_wip files
python -m pipelines.melody.digitizer.cli scaffold 318_02_HOW_HIGH_THE_MOON
```

A tune is processable when its crop, a `both` row in `data/title_index.csv`, and
`data/chords/05_annotated/<chords-stem>.json` all exist (181 today; grows as
chords digitization lands). Each scaffold's bars are placeholder whole-measure
rests (`x8` / `x6`) so the file validates and renders as blank, labeled staves.

Other subcommands (all deterministic except `read`/`benchmark`):
`discover` (manifest stats), `skeleton <stem>` (headers+plan), `validate
<file>`, `render <file> <stem>`, `check` (Phase-1 acceptance vs the 14 verified
tunes), `score`, and `read <stem> [--dual]` / `benchmark [--single]` (the VLM
read, kept for the record — it spends budget and does not clear the bar).

## Stage 0 — crop

```sh
python pipelines/melody/melody_cropper.py sources/AGJ_Melody.pdf --pages 7..972 \
       --melody-index sources/AGJ_Melody_Index.pdf --index sources/AGJ_index.pdf
```

The melody book's page numbers differ from the grille book's, so titles are
OCR'd and fuzzy-matched against the book indexes to recover the canonical title;
low-confidence matches are flagged `review=yes` in the manifest.

`../deskew_crops.py` is a shared one-off, in-place fix for crops that came out
slanted (same script serves the chords crops). It takes a file, glob, or
directory:

```sh
python pipelines/deskew_crops.py data/melody/01_crops --dry-run   # report angles
python pipelines/deskew_crops.py data/melody/01_crops/247_02_FLYING_HOME.png
```

Crops are stored 1-bit — re-deskewing an already deskewed crop degrades staff
lines; rerun `melody_cropper.py` from the PDF instead.

## Stage 1 — straighten

```sh
python pipelines/melody/melody_straightener.py data/melody/01_crops --debug
```

The staves are hand-drawn with local slant and curvature, so every system is
straightened per pixel column; `stage1.json` persists the staff geometry that
all later stages use. Systems failing the 5-line sanity check are flagged for
model review.

## Verified output

Stage 4/5 output lands in `data/melody/03_wip/<crop-stem>.abc` (same stem as the
PNG in `data/melody/01_crops/`). After human review — abcjs renders and plays the
ABC in the displayer, which is the fastest way to check a jazz tune — promote
the file to `data/melody/04_verified/`; the displayer bundles any `.abc` there
automatically at the next `apps/displayer/build_data.py` run.
