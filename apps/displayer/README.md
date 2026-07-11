# Grilles displayer

Fully static web app that browses the whole AGJ corpus — chord grille and melody
sheet side by side, with digitized tunes rendered as styled chord grids and
abcjs lead sheets. Spec:
[docs/specs/displayer_app_spec.md](../../docs/specs/displayer_app_spec.md).

## Build the data bundle

```sh
python apps/displayer/build_data.py
```

Reads `data/title_index.csv`, `data/chords/04_verified/`, `data/melody/04_verified/`
and copies every referenced scan into `crops/` and `melody_crops/` here, so the
deployed folder is self-contained. Rerun after verifying new tunes or melodies,
then commit the changes. Open `index.html` directly (file://) to test locally.

## Practice rhythm samples

`data/brush_samples.js` (the swing-brush kit used by the topbar practice
player) is generated — rerun after tweaking the synthesis and commit:

```sh
python apps/displayer/render_brush_samples.py
```

## Deploy

Pushing to `main` deploys automatically — the GitHub Pages workflow
(`.github/workflows/deploy-pages.yml`) uploads `apps/displayer/`.

Manual alternative (gh-pages branch):

```sh
git checkout main && git pull
git subtree push --prefix=apps/displayer origin gh-pages
```

Note: the first subtree push after the 2026-07 repo reorganization (prefix
changed from `grilles_displayer` to `apps/displayer`) must be forced:

```sh
git push origin "$(git subtree split --prefix=apps/displayer main)":gh-pages --force
```
