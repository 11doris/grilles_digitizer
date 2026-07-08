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
| 0 crop | `melody_cropper.py` | `sources/AGJ_Melody.pdf` + both index PDFs → `data/melody/crops/*.png` + `melody_manifest.json` |
| 0b deskew (one-off) | `deskew_crops_all.py` / `deskew_crop.py` | `data/melody/crops/` fixed in place (`_orig` backups kept) |
| 1 straighten | `melody_straightener.py` | crops → `data/melody/debug/<id>/` (strips, overlays, `stage1.json` geometry) |
| 2 symbols | *not yet implemented* | per-system symbol lists (barlines, noteheads, stems, rests, …) |
| 3 bars | *not yet implemented* | bar assembly + rhythm solving against the chord JSON |
| 4 adjudicate | *not yet implemented* | model API on flagged bars only → `data/melody/wip/<id>.abc` |
| 5 validate | *not yet implemented* | validation suite; then human review promotes `wip/` → `verified/` |

The chord grille JSON (`data/chords/raw|verified/<id>.json`) is **ground truth**
for form, bar counts, and chords — never modify it from this pipeline.

## Stage 0 — crop

```sh
python pipelines/melody/melody_cropper.py sources/AGJ_Melody.pdf --pages 7..972 \
       --melody-index sources/AGJ_Melody_Index.pdf --index sources/AGJ_index.pdf
```

The melody book's page numbers differ from the grille book's, so titles are
OCR'd and fuzzy-matched against the book indexes to recover the canonical title;
low-confidence matches are flagged `review=yes` in the manifest.

`deskew_crop.py <stem>` / `deskew_crops_all.py` are one-off in-place fixes for
crops that came out slanted. Crops are stored 1-bit — re-deskewing an already
deskewed crop degrades staff lines; rerun `melody_cropper.py` from the PDF
instead.

## Stage 1 — straighten

```sh
python pipelines/melody/melody_straightener.py data/melody/crops --debug
```

The staves are hand-drawn with local slant and curvature, so every system is
straightened per pixel column; `stage1.json` persists the staff geometry that
all later stages use. Systems failing the 5-line sanity check are flagged for
model review.

## Verified output

Stage 4/5 output lands in `data/melody/wip/<crop-stem>.abc` (same stem as the
PNG in `data/melody/crops/`). After human review — abcjs renders and plays the
ABC in the displayer, which is the fastest way to check a jazz tune — promote
the file to `data/melody/verified/`; the displayer bundles any `.abc` there
automatically at the next `apps/displayer/build_data.py` run.
