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
| 2 symbols | *not yet implemented* | per-system symbol lists (barlines, noteheads, stems, rests, …) |
| 3 bars | *not yet implemented* | bar assembly + rhythm solving against the chord JSON |
| 4 adjudicate | *not yet implemented* | model API on flagged bars only → `data/melody/03_wip/<id>.abc` |
| 5 validate | *not yet implemented* | validation suite; then human review promotes `wip/` → `verified/` |

The chord grille JSON (`data/chords/02_raw|verified/<id>.json`) is **ground truth**
for form, bar counts, and chords — never modify it from this pipeline.

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
