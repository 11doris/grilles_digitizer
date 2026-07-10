# Grilles Displayer — Implementation Spec

## 1. Overview

A fully static web app that browses the whole AGJ tune corpus — **one entry per
row of `data/title_index.csv`** (~1,568 tunes) — and, for the selected tune, shows its
**chord grille** and its **melody sheet** side by side. The visual reference for
the rendered chord grid is `docs/displayer_reference.png`
(dark background, large condensed chord symbols, boxed section letters, double
barlines at section boundaries).

`data/title_index.csv` (repo root) is the **single source of truth** for what tunes
exist and how a tune's chord scan is paired with its melody scan. Most rows have
only scanned PNGs; a small and growing subset also have a **digitized** form:

- **digitized chords** = a tune JSON in `data/chords/04_verified/` → rendered as a styled
  chord grid (§6–§7).
- **digitized melody** = a verified ABC file in `data/melody/04_verified/`, **named
  after its melody scan** (`<data/melody/01_crops stem>.abc`, e.g.
  `17_01_AINT_MISBEHAVIN.abc` ↔ `data/melody/01_crops/17_01_AINT_MISBEHAVIN.png`) →
  rendered in-app as a lead sheet with a **vendored abcjs**. Only a pilot
  transcription exists today; any `.abc` dropped into `data/melody/04_verified/` is
  picked up automatically at the next `build_data.py` run.

The app is read-only: it never modifies `data/chords/04_verified/`, `data/melody/04_verified/`,
`data/chords/01_crops/`, `data/melody/01_crops/`, or `data/title_index.csv`.

---

## 2. Technology Stack

| Layer | Choice |
|---|---|
| Frontend | Single-page HTML + Vanilla JS + plain CSS. No framework, no npm, no build tooling. |
| Melody rendering | **abcjs** (MIT), vendored once as a single minified file in `vendor/abcjs-basic-min.js` — no CDN requests at runtime. |
| Data | A generator script bundles the index + all digitized tunes (chord JSON **and** melody ABC text) into one JS file and copies every referenced scan PNG into the app folder, so the app works from `file://` and deploys as a static folder. |
| Generator | Python 3.11+ script; standard library + Pillow (for the 1-bit scan re-encoding, §3). Already in the repo venv. |
| Fonts | Saira Extra Condensed (Google Fonts), bundled locally as woff2 in `fonts/` and loaded via `@font-face`. No CDN requests at runtime — the app must work offline. System condensed sans as fallback. |

---

## 3. File Layout

```
apps/displayer/
├── index.html            # the app shell
├── style.css             # all styling, incl. dark/light themes
├── app.js                # search, navigation, rendering logic
├── chords.js             # chord token parsing + display transformation
├── playlists.js          # playlist state, localStorage persistence, export/import (§11)
├── build_data.py         # generator: data/title_index.csv + data/chords/04_verified/*.json
│                         #            + data/melody/04_verified/*.abc -> data/tunes_data.js
├── fonts/                # bundled Saira Extra Condensed woff2 (400/500/700)
├── vendor/
│   └── abcjs-basic-min.js  # vendored abcjs (MIT) for melody lead sheets
├── crops/                # GENERATED — chord scan PNGs copied from data/chords/01_crops
├── melody_crops/         # GENERATED — melody scan PNGs copied from data/melody/01_crops
└── data/
    └── tunes_data.js     # GENERATED — do not edit by hand
```

Everything the deployed app needs lives inside `apps/displayer/` — the GitHub
Pages workflow uploads only this directory, so **all referenced PNGs are copied
in**. This is a deliberate trade for the app working online without a server.

**Deploy weight.** Copied verbatim the scans would weigh ~570 MB (chords
≈ 93 MB, melodies ≈ 477 MB — some melody crops are needlessly RGBA). Since the
source material is a 1-bit scan (grays exist only as interpolation artifacts
from deskew/resize), the canonical fix is **upstream**: the cropping pipelines
(`crop_tunes.py` / `deskew_crops.py`, `melody_cropper.py` /
`melody_straightener.py`) work in grayscale internally but save their final
output as **full-resolution 1-bit optimized PNG**. That is visually identical
and shrinks the corpus ~5× (melodies ≈ 79 MB, chords ≈ 40 MB, **~120 MB
total**; measured on a 30-file sample). Downscaling was rejected — resampling
adds gray gradients that make these files *bigger*.

Rules for the 1-bit convention:
- Binarize **exactly once, at the final write** (threshold 128). Never apply a
  geometric transform (rotate/deskew/resize) to an already-binarized image —
  regenerate from the PDF instead; the AGJ PDFs remain the archival source.
- It is not a loss for the future sheet-music OCR: the melody digitizer's CV
  stages binarize as their first step anyway, and all grays in the crops are
  synthetic (the scans are 1-bit at the source).
- Both `data/chords/01_crops/` (repo root) and the copies in `apps/displayer/` are tracked
  in git, so the 1-bit form also caps repository growth. The conversion is
  deterministic: unchanged sources re-encode to identical bytes → no git churn.

The generator then simply **copies** the scans. As a safety net it verifies
each copied PNG is 1-bit and re-encodes any that isn't (with a warning listing
the offending files), so the deploy stays small even if an upstream crop
predates the convention.

### 3.1 Generator (`build_data.py`)

- Usage: `python apps/displayer/build_data.py`. Inputs, all overridable:
  `--index data/title_index.csv`, `--tunes-dir data/chords/04_verified`,
  `--crops-dir data/chords/01_crops`, `--melody-crops-dir data/melody/01_crops`,
  `--melodies-dir data/melody/04_verified`.
- **Drives off `data/title_index.csv`**, one record per data row (skips the header).
  Relevant columns: `match_status`, `chords_title`, `chords_file`,
  `melody_title`, `melody_file`. (`match_type`, `chords_page`, `melody_page`
  are ignored by the app.)
- For each row it builds a record (see §4). It **embeds the full chord JSON**
  when a digitized tune exists for that row, **preserving JSON key order**
  (`json.load` keeps insertion order — section order matters).
- **Melody ABC**: a row whose melody has been digitized carries the ABC source
  **embedded as a string** (`abc`). The join is by **melody scan stem**:
  `data/melody/04_verified/<stem of melody_file>.abc`. Embedding (rather than
  fetching `.abc` at runtime) keeps the app working from `file://` (no
  CORS/fetch). Any new `.abc` saved to `data/melody/04_verified/` is included
  automatically at the next build; an `.abc` whose stem matches no index row's
  `melody_file` produces a warning.
- Copies the referenced scans into the app folder when the source file exists:
  `data/chords/01_crops/<chords_file>` → `apps/displayer/crops/`, and
  `data/melody/01_crops/<melody_file>` → `apps/displayer/melody_crops/`. Sources are
  expected to already be 1-bit PNGs (§3 "Deploy weight"); any that aren't are
  re-encoded (grayscale → threshold 128 → optimized 1-bit PNG) with a warning.
  Skip the copy when the output already exists with unchanged source
  mtime/size (~3,000 images — keep rebuilds fast). Stale copies in both
  folders are pruned. A referenced-but-missing PNG is a warning, not an error
  (the record just lacks that image).
- Writes `data/tunes_data.js` containing a single assignment:

```js
window.TUNES = [
  { "id": "22_02_AS_TIME_GOES_BY",
    "title": "As Time Goes By",
    "chord_image": "data/chords/01_crops/22_02_AS_TIME_GOES_BY.png",
    "melody_image": "data/melody/01_crops/34_01_AS_TIME_GOES_BY.png",
    "has_chord_json": true,
    "has_melody_abc": true,
    "tune": { ...full chord JSON... },
    "abc": "X:1\nT:AS TIME GOES BY\n..." },
  ...
];
```

- Records are sorted alphabetically by the **displayed title** (§4).
- The script prints a one-line summary (e.g.
  `Wrote 1568 tunes (32 chord-JSON, 0 melody-ABC; 1561 chord PNGs, 1448 melody PNGs)`)
  and exits non-zero with a clear message if the index or a JSON file fails to
  parse.
- Rerun manually whenever the index changes or tunes are verified; the app
  itself never reads the source directories directly.

---

## 4. Data Model

### 4.1 Per-tune record (output of the generator)

| Field | Meaning |
|---|---|
| `id` | Stable key & URL hash. The `chords_file` stem when present, else the `melody_file` stem. |
| `title` | **Displayed** title. The digitized chord JSON's `title` if present; otherwise derived from the CSV title column (`chords_title`, fallback `melody_title`): `_`→space, Title-Cased. Filename-derived artifacts (e.g. `AIN_T` → "Ain T") are accepted as-is. |
| `chord_image` | `data/chords/01_crops/<chords_file>` when that PNG was copied in, else absent. |
| `melody_image` | `data/melody/01_crops/<melody_file>` when that PNG was copied in, else absent. |
| `has_chord_json` | `true` when a digitized chord tune was embedded (`tune`). Drives the left-icon green state. |
| `has_melody_abc` | `true` when a digitized melody was embedded (`abc`). Drives the right-icon green state. |
| `tune` | The embedded chord JSON (§4.2), only when `has_chord_json`. |
| `abc` | The embedded ABC source (string, ABC v2.1), only when `has_melody_abc`. Rendered by abcjs in the melody panel. |

Icon/asset booleans the app derives from the record:
`hasChordAsset = chord_image || has_chord_json`,
`hasMelodyAsset = melody_image || has_melody_abc`.
A tune with neither asset should not occur in the index; if it does, list it with
no icons.

### 4.2 Embedded chord JSON (`tune`)

As produced by the digitizer / verifier (see `docs/specs/jazz_chord_digitization_spec.md`). Relevant parts:

- Meta: `title`*, `composer`, `year`, `style`, `tempo`, `form`, `time_signature`* (e.g. `"4/4"`), `page`, `source`, `same_chord_changes`.
- `sections`: ordered object; keys are section names (`"A"`, `"A1"`, `"B"`, `"C"`, but also `"verse_A"`, `"interlude"`, `"Transition"`); values are arrays of bar objects:

```json
{ "bar": 3, "beats": { "1": "Eb", "3": "Fm7", "4": "F#o7" } }
```

- `variants`: optional array of `{ "applies_to": "<string>", "targets": [ { "section": "<name>", "bar": <n> }, … ], "bars": [ <bar objects> ] }`. `targets` gives one anchor per occurrence — the grid bar (1-indexed within its section) where the variant's first bar applies; the rest follow consecutively in the same section. Legacy tunes may lack `targets`, in which case the app falls back to parsing `applies_to` over the *chorus frame* (bar numbers count the main strain, excluding auxiliary `verse_*`/`intro`/`interlude`/`coda` sections).
- `recordings`: optional array of strings.
- `notation_notes`: optional object of string → string.

Unknown fields are ignored (not an error). Missing optional fields simply hide their UI element.

Currently all verified tunes are 4/4 with sections of mostly 8 or 16 bars, but the app must handle any bar count (2- and 5-bar sections exist) and any `n/4` time signature.

---

## 5. UI Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  Grilles   [ search…            ]                              ☾/☀  │  top bar
├────────────────────┬─────────────────────────────────────────────────┤
│ tune list          │   (Medium Swing)                  Irving Berlin  │
│ (sidebar,          │              AS TIME GOES BY                     │
│  scrollable)       │        Standard · 1931 · 32 A B A C · p.14       │
│                    │                                                  │
│ ▦♪  All By…        │   [ Chords ●on ]        [ Melody  off○ ]         │
│ ▦♪  All Of Me      │  ┌───────────────────┐  ┌─────────────────────┐  │
│ ▦♪  As Time…       │  │  [A]              │  │                     │  │
│ ▦·  Blue Skies     │  │ 4/4 ║ C∆7│C6│…║   │  │   (melody sheet     │  │
│ ·♪  Some Melody    │  │  chord grid /     │  │    PNG, when the     │  │
│  …                 │  │  chord scan       │  │    Melody switch     │  │
│                    │  │  ▦ original)      │  │    is on)            │  │
│                    │  └───────────────────┘  └─────────────────────┘  │
│                    │   ▸ Variants   ▸ Recordings   ▸ Notes            │
└────────────────────┴─────────────────────────────────────────────────┘
        ▦ = chord-grid icon   ♪ = melody icon   green = digitized
```

### 5.1 Top bar
- App name, a search input, a **Playlists** menu button (§11), and a theme
  toggle button, plus a ☰ button
  (leftmost, always present): on phones it opens the tune-list drawer; on
  desktop it collapses/expands the docked sidebar, giving the tune view the
  full width. The desktop choice persists in `localStorage` (`grilles.list`).
  Typing a search query reveals the hidden list in either mode. (The old
  global scan toggle moves into per-panel controls — see §5.4.)
- The **Playlists** menu (§11.3) opens a small panel to pick the active
  playlist, create/rename/delete playlists, and export/import. When a playlist
  is active the button shows its name and a small ✕ to deactivate (return to the
  full corpus).
- The search input has focus on page load. `Esc` closes any fullscreen scan /
  the drawer if open, otherwise clears the search.

### 5.2 Sidebar — tune list
- Every row of `data/title_index.csv` is listed, sorted alphabetically by displayed
  title. Each entry shows the **title** and, in smaller muted text, the
  **composer** (only digitized tunes have one).
- **Availability icons** — a fixed two-slot cluster at the left of each entry:
  - **Left slot = chord grille** icon (a small 2×2 grid glyph, inline SVG).
    Shown when the tune has a chord asset (`chord_image` or `has_chord_json`).
    **Green** when `has_chord_json` (digitized), otherwise **gray** (scan only).
  - **Right slot = melody** icon (a beamed-eighth-notes glyph, inline SVG).
    Shown when the tune has a melody asset (`melody_image` or `has_melody_abc`).
    **Green** when `has_melody_abc` (digitized), otherwise **gray** (scan only).
  - A slot with no asset of that type is left **empty but reserved**, so the two
    columns stay aligned down the list. `both` rows show two icons,
    `chords_only` only the left, `melody_only` only the right.
  - Each icon has a `title`/`aria-label` (e.g. "Chord grid (digitized)",
    "Melody (scan)").
- The search box filters the list live (see §8). The currently displayed tune
  is highlighted.
- Clicking an entry displays that tune in the main panel.
- Keyboard: `↑`/`↓` move the highlight through the (filtered) list, `Enter`
  opens the highlighted tune.
- The list holds ~1,568 rows: render it efficiently (e.g. a single delegated
  click handler; virtualize or cap rendered rows only if scrolling is janky).
- On phones — narrow screens (≤ 700px wide) **or** landscape-short screens
  (≤ 500px tall) — the sidebar is hidden and becomes a slide-in drawer over
  the content, opened with the ☰ button (typing in the search box also opens
  it). Picking a tune, tapping the backdrop, or `Esc` closes it.

### 5.3 Main panel — tune header
Mirrors the screenshot (populated from the embedded chord JSON; for a
non-digitized tune only the **title** is shown):
- **Title**: centered, large, bold.
- **Tempo**: top-left, in parentheses, e.g. `(Slow)` — title-cased from the JSON value.
- **Composer**: top-right.
- **Metadata line** below the title, small and muted: `style · year · form · p. N`. The `source` field is not displayed. Fields that are absent are simply omitted.
- **Key chips** below the metadata: a **Key** chip from the tune's top-level `key` (`{tonic, mode}`), followed by one chip per entry in the tune's top-level `section_keys` (only present where a section modulates away from the main key), labelled with the section name. Both sources are the tune's own top-level fields; the `key_annotation` block (scorer bookkeeping, including its nested `section_keys` copy) is **ignored** for display. Chips track the active transposition.
- **Add to playlist** button (§11.2): a small `＋ Add to playlist` control in the
  header (near the composer/right side, so it never collides with the centered
  title). Present for **every** tune, digitized or not. Clicking opens the
  add-to-playlist popover (§11.2). When a playlist is active, the header also
  shows **‹ Prev / Next ›** step-through controls (§11.4) that move through the
  active playlist in order.

### 5.4 Content panels — chords & melody

The main panel below the header holds up to two **content panels**, each gated
by a visible **switch** placed in a toolbar directly under the tune header:

- **Chords switch** and **Melody switch** — two clearly labelled toggle
  switches. A switch is only shown when the tune has that asset
  (`hasChordAsset` / `hasMelodyAsset`); a tune with only one asset shows only
  that one switch.
- **Defaults**: Chords **on**, and Melody **on whenever the tune has a melody**
  (scan or ABC), so the melody sheet shows by default beside the chords. First
  visit uses these; the choice for each switch persists in `localStorage`
  (`grilles.showChords`, `grilles.showMelody`) and is restored on later visits.
  If a tune lacks the asset for a persisted "on" switch, that panel is simply
  not shown (the switch is hidden), without altering the stored preference.

Each panel's **content** follows one rule — *render if digitized, else show the
scan*:

- **Chord panel**: the styled chord grid (§6–§7) when `has_chord_json`;
  otherwise the chord scan PNG (`chord_image`). When *both* exist, an
  **"original scan" toggle** swaps the rendered grid for the scan **in place**
  (default: rendered; no scroll jump). The button sits in a small tools row
  above the panel content — never overlaid on the image — and shows a photo
  icon when the rendered form is visible, swapping to the grid icon while the
  scan is shown (i.e. the icon shows what it switches to).
- **Melody panel**: the abcjs lead sheet (rendered from `abc` via
  `ABCJS.renderAbc`, responsive width, theme-aware colors) when
  `has_melody_abc`; otherwise the melody scan PNG (`melody_image`). When *both*
  exist, the same **"original scan" toggle** appears (photo icon ⇄ notes icon,
  default: rendered, in-place swap without scroll jump).
  Display-only transform: the `T:`/`C:`/`O:`/`R:` header lines are stripped
  before engraving — the tune head already shows title/composer, and long `O:`
  lines overlap when engraved. The `.abc` file itself stays canonical.
  A malformed ABC must never crash the page: render abcjs errors as a small
  warning and fall back to the scan.

**Layout of the two panels:**
- **Wide / landscape (≥ 900px)**: when both switches are on, the panels sit
  **side by side** (chords left, melody right), each taking half the content
  width, and the chord grid fills its half so the chords stay readable beside
  the melody. When only the **melody** is on it takes the full width; when only
  the **chord** panel is on it still occupies about **half the content width**
  (centered) — the same footprint it has beside the melody — rather than
  spanning the whole pane.
- **Narrow / portrait (< 900px)**: panels **stack vertically** (chords above
  melody), full width, page scrolls.

**Fullscreen a panel**: clicking a scan image (chord scan or melody scan) opens
it fullscreen. In the fullscreen view, clicking the image toggles a magnified
state (up to natural size, capped at 250vw on small screens) panned by
scrolling, starting centered. Clicking the backdrop, the ✕ button, or `Esc`
closes it. (The rendered chord grid is not a scan and has its own zoom, §6.5.)

### 5.5 Deep linking
- The displayed tune is reflected in the URL hash (`#22_02_AS_TIME_GOES_BY`). On load, a valid hash opens that tune; otherwise the first tune in the list is shown.

---

## 6. Chord Grid Rendering

### 6.1 Rows and sections
- Sections render in JSON order. Each section starts with its **section label** and starts a **new row**.
- **Every row holds exactly 4 bar slots** of equal width. A section with 8 bars renders as 2 rows, 16 bars as 4 rows. If a section's bar count is not a multiple of 4 (e.g. 2 or 5 bars), the trailing slots of the last row are **empty**: no chords, no barlines, just blank space keeping the 4-column alignment.

### 6.2 Section labels
- Rendered like the screenshot: a small square badge (inverted colors: light box / dark letter in dark theme) placed above the row's first barline, left-aligned.
- Display name mapping:
  - Name matching `^([A-Z])\d*$` → the letter only (`A1` → `A`, `A2` → `A`, `B1` → `B`). The screenshot confirms repeats of a section reuse the plain letter.
  - Anything else → underscores replaced by spaces, first letter capitalized (`verse_A` → `Verse A`, `interlude` → `Interlude`).

### 6.3 Barlines
- Single thin vertical line between bars within a row, and at row edges — except:
- **Double barline** at the start and end of every section (so also at the very start and very end of the tune). Interior rows of a section start/end with a single barline.
- The **time signature** (stacked numerator over denominator, e.g. `4` over `4`) is rendered once, immediately before the opening double barline of the first section — as in the screenshot.

### 6.4 Beats within a bar
- The numerator of `time_signature` gives the beats per bar (4 for 4/4). Each bar is internally a grid of that many equal beat slots.
- Each chord in `beats` is placed left-aligned in its beat slot (beat `"1"` → slot 1, `"3"` → slot 3). Empty slots stay empty — a chord implicitly sustains until the next one, exactly like the printed grille (e.g. a bar with beats 1 and 3 shows two chords, at the left edge and the middle of the bar).
- Chords never wrap to a second line and never shrink individually — every chord renders at the grid's single font size. If the busiest bar's chords would overflow their slots, a JS pass first **widens** the grid to give each slot more room; when there's no width left to give (a narrow phone, or the chords panel sharing the row with melody) it pins the grid to the available width and **shrinks the font** instead, so the chords fit the now-fixed-width slots (see §6.5).

### 6.5 Responsive behavior
- The chord panel measures the width **actually available to it** — about half
  the content width on a wide screen (whether it shares the row with the melody
  panel or, without a melody, sits alone at half width, §5.4), and the full width
  when stacked on a phone. Bars shrink fluidly to that width; chord font size
  scales with bar width (CSS `clamp()`/container-relative units). No horizontal
  page scrolling.
- **Chord size is width-driven, not height-driven**: the chord font scales with
  the panel width (`2.6cqw`) up to a **15px ceiling** so chords stay a readable
  lead-sheet size and don't balloon on a wide screen (a sparse tune needs no
  larger a font than a busy one to read). It is **not** shrunk to force the tune
  onto one screen: a long tune (many sections) keeps that readable size and the
  page **scrolls** instead — readability wins over one-page fit, since a grid
  collapsed to fit a tall tune becomes unreadable. Extras (§9) may fall below the
  fold. The grid fills its panel until the ceiling is hit, then stops widening:
  its em-based width cap (38.5em, the width at which `2.6cqw` reaches the ceiling,
  ≈ 577px) keeps bar width proportional to chord size and centers the grid rather
  than spreading it across an ever-wider column. Only **width** crowding shrinks
  the font below the clamp: a busy bar first widens the grid (§6.4, up to 56em or
  the available width), and when even the full available width isn't enough the
  font shrinks so the chords still fit (down to an 8px floor).
- **Grid zoom**: floating −/+ buttons (bottom right) scale the width-driven grid
  size by a user factor (×1.15 steps, clamped 0.5–2.5); zooming in makes the page
  scroll. The zoom is **per-tune and transient**: it resets to 1× on every tune
  change and on reload (not persisted), so each tune opens at its width-driven
  size.
- The tune head stacks to a single centered column when the pane is narrow
  (small screen, or the chord panel sharing the row with the melody panel) via a
  container query.

---

## 7. Chord Symbol Styling

All styling is pure CSS + Unicode; no images. Each chord is parsed by `chords.js` into parts and rendered as nested `<span>`s.

### 7.1 Token grammar

```
chord     := "(" core ")"        → optional chord (render in parentheses, ~80% size)
           | core
core      := "N.C."              → rendered verbatim, small caps
           | root quality? bass?
root      := [A-G] accidental?
accidental:= "#" | "b"
bass      := "/" [A-G] accidental?    (only when "/" is followed by a note letter;
                                       six-nine chords are written slashless as F69)
quality   := everything between root and bass
```

### 7.2 Display transformations (applied to the quality string)

| In JSON | Displayed |
|---|---|
| `maj7`, `maj9` | `Δ7`, `Δ9` |
| `m7b5` | `ø7` |
| `o7` | `o7` (kept; e.g. `F#o7` → F♯o7) |
| `m(maj7)` | `m(Δ7)` |
| `#` / `b` (in quality or alterations) | `♯` / `♭` (U+266F / U+266D) |
| everything else (`m7`, `6`, `9`, `7#5`, `9sus4`, `7(13)`, `69`, `m11`, …) | verbatim, with the accidental substitution above |

The root's own accidental also renders as `♯`/`♭`.

### 7.3 Visual style (matching the screenshot)

- **Box grid** (Figure D.3): the symbol is a fixed grid — a full-height root letter on the left, then a middle column and a right column, each split into a top and bottom box of fixed em height. Because the boxes never move, the root shares one ground line across chords whether or not there's an accidental or tension (`B7♯11` and `B♭7♯11` line up).
- **Root letter**: large (~2.1em relative to body), regular weight (not bold), condensed sans (`'Saira Extra Condensed', 'Arial Narrow', 'Helvetica Neue', Arial, sans-serif`), spanning both rows.
- **Middle column**: the root accidental sits in the top box (superscript) directly over the core quality in the bottom box, so the chord stays about a letter-width wide (`E♭ø7`, `A♭-7`). A bare accidental triad (`C♯`) leaves the bottom box empty.
- **Quality** (core): ~45% of root size. Minor renders as `-` (not `m`); `maj`→`Δ`, `m7b5`→`ø7`.
- **Right column** — tensions/alterations in up to two boxes (alt-up / alt-down): a lone alteration sits in the upper box, a pair straddles the core — so `B♭7♯9♯5`, `A♭7(13)`, `(♯5♯9)` stay narrow instead of spreading across the bar.
- **Flat glyph**: the `♭` runs small in the fallback music font, so it's enlarged (both as a root accidental and inside qualities/alterations) to read at the same weight as `♯`.
- **Bass note** (`/Eb`): small, placed below-right of the chord after a short slash, like `Aø7/E♭` in the screenshot.
- **Optional chords** `(…)`: whole symbol wrapped in thin parentheses at reduced size and slightly muted color.
- `N.C.`: small caps, muted.

### 7.4 Robustness
- Any chord string that fails to parse renders verbatim (monospace, warning color in a `title` tooltip) — never crash the page. (Verified files pass `pipelines/chords/tools/check_chord_syntax.py`, so this is a safety net only.)

---

## 8. Search

- Single search box; matches against **title** and **composer** (composer only
  exists for digitized tunes; a non-digitized tune matches on title alone).
- Case-insensitive, diacritic-insensitive (`normalize('NFD')` + strip combining marks), substring match on either field. Multiple whitespace-separated terms are AND-ed (every term must match title or composer).
- Filtering is live on every keystroke. With ~1,568 rows a light debounce (or a
  precomputed lowercase/normalized search string per tune) keeps typing smooth;
  no external index is needed.
- Empty query shows the full list. Zero matches shows a "No tunes found" message in the sidebar.

---

## 9. Extras (below the chord grid)

Only for digitized-chord tunes (they read from the embedded `tune` JSON), shown
below the chord panel. **Variants** render as an always-visible block immediately
below the grid (item 2); the remaining blocks are collapsible `<details>`,
collapsed by default, in small muted type:

1. **Same changes** — the `same_chord_changes` string, when present.
2. **Variants** — rendered directly below the main grid (always visible, **not** collapsed). For each entry in `variants`: its `applies_to` string as a caption, then its `bars` rendered as a mini chord grid (same renderer as §6/§7, column-aligned to the main grid, single barlines only — variants have no sections). When a variant's `targets` (or, for legacy data, its `applies_to`) resolve to real grid bars, the variant box is **clickable**: clicking swaps the variant's chords into the matching grid bars and clicking again restores them. Variants that touch **different** bars toggle **independently** — several may be applied at once, and their overrides merge. Variants that compete for the **same** bar (their resolved targets overlap on any grid bar, e.g. *My Old Flame*'s three "Bar 17" alternatives) are mutually **exclusive**: applying one drops any active variant it overlaps, so the grid never shows two conflicting alternatives for one bar. Applying a variant does **not** visually mark the affected grid bars (the swapped chords stand on their own); an internal `.bar.variant-swap` class marks them only as a hook for tests/inspection. The active state is per-tune and **persisted** in `localStorage` (key `grilles.variants`, a `{ tuneId: [variantIndex,…] }` map): reopening or reloading a tune restores the swaps the user had applied. Saved indices that no longer point at an applicable variant (a changed corpus) are ignored on load.
3. **Recordings** — the `recordings` array as a plain list.
4. **Notes** — each `notation_notes` key/value as `key: value` lines.

Blocks whose data is absent are not rendered at all.

---

## 10. Theming

- Two themes driven by CSS custom properties on `:root`:
  - **Dark (default)**: near-black background (`#0b0b0d`), white chords, muted gray metadata — matches the screenshot.
  - **Light**: paper-white background, near-black chords.
- Toggle button (☾/☀) in the top bar. Choice persisted in `localStorage` (`grilles.theme`); on first visit, follow `prefers-color-scheme`.
- All colors (text, barlines, section badges, muted text, highlight) come from the custom properties — no hard-coded colors in component rules.

---

## 11. Playlists

Playlists let a user collect tunes into named, ordered lists that persist across
visits, can be stepped through in order, and can be moved to another device or
shared with another person. Playlists are a **pure client-side, read-only-corpus**
feature: they live in the browser and never touch the data sources or the
generator — `build_data.py` is unaffected, and the §1 read-only invariant holds
(the app writes only to `localStorage` and, on export, a user-initiated file
download).

### 11.1 Storage model & "per user"

The app is fully static (no server, no login, must work from `file://`), so there
is no server-side account to key playlists to. "Per user" is therefore realized
**per browser plus portable files**:

- All playlists persist in `localStorage` under a single key
  **`grilles.playlists`** — a JSON document `{ "version": 1, "playlists": [ … ] }`.
  The active playlist id persists under **`grilles.activePlaylist`**.
- A user's playlists are whatever lives in their browser. To move them to another
  device or hand them to another person, they **export** to a JSON file and
  **import** it elsewhere (§11.5). This is the deliberate substitute for accounts;
  a true multi-user backend was rejected as it would break the static/offline
  architecture.
- Corruption safety: if `grilles.playlists` fails to parse, the app starts with
  an empty playlist set (and does not overwrite the bad value until the user makes
  a change), rather than crashing.

**Playlist object:**

```json
{ "id": "pl_9f3a12",            // stable local id (generated, e.g. "pl_" + random)
  "name": "Gig set — Friday",   // user-supplied, need not be unique
  "tuneIds": [ "22_02_AS_TIME_GOES_BY", "17_01_AINT_MISBEHAVIN", … ],
  "createdAt": "2026-07-08T10:00:00Z",
  "updatedAt": "2026-07-08T10:12:00Z" }
```

`tuneIds` reference the per-tune `id` (§4.1). A tune appears in a playlist **at
most once** (adding an already-member tune is a no-op). Order is the array order
and is user-editable (§11.3). A `tuneId` that no longer matches any corpus tune
(the index changed) is **kept in storage but skipped/greyed** in the UI with a
small "not in current corpus" note — never a crash.

### 11.2 Adding a tune — the per-tune button

Each tune's header carries a `＋ Add to playlist` button (§5.3). Clicking it opens
a small **popover** anchored to the button:

- A list of existing playlists, each with a checkbox reflecting whether **the
  current tune** is already a member. Toggling a checkbox adds/removes the tune
  from that playlist immediately (updates `updatedAt`, persists).
- A **`＋ New playlist…`** row: prompts for a name (inline text field) and creates
  a playlist containing the current tune. Creation does **not** change the active
  playlist (§11.4).
- If no playlists exist yet, the popover shows only the `＋ New playlist…` affordance.
- The button gives a brief confirmation (e.g. the button label flips to
  `✓ Added` for ~1s, or the checkbox state is self-evident). Closing: click
  outside, `Esc`, or pick an action.

Adding several tunes to a playlist is done by visiting each and toggling it in —
there is no bulk multi-select mode in the sidebar.

### 11.3 Managing playlists — the Playlists menu

The top-bar **Playlists** menu (§5.1) lists all playlists and offers, per playlist:

- **Activate** — makes it the active playlist (§11.4). Selecting the currently
  active one, or an explicit "Show all tunes", deactivates.
- **Rename** — inline edit of `name`.
- **Delete** — removes the playlist (with a confirm; deleting the active one
  deactivates first).
- **Reorder / remove tunes** — when a playlist is active, its tunes are the
  sidebar list (§11.4); each row gets a remove (✕) control and up/down (or drag)
  reordering. Reordering/removing updates `tuneIds` + `updatedAt` and persists.
- The menu also holds **Export** and **Import** (§11.5) and a tune count per
  playlist.

### 11.4 Opening a playlist — filtered view + step-through

Activating a playlist:

- **Filters the sidebar** to exactly the playlist's tunes, shown **in playlist
  order** (not the global alphabetical order). Search (§8) still applies, further
  filtering within the active playlist. The top bar shows the active playlist name
  with a ✕ to deactivate.
- **Enables step-through**: `‹ Prev` / `Next ›` controls in the tune header
  (§5.3) move to the previous/next tune in playlist order, wrapping is **not**
  applied (Prev disabled on the first, Next on the last). Keyboard: the existing
  `↑`/`↓` + `Enter` still drive the (now playlist-scoped) sidebar; step-through
  buttons are an additional explicit control.
- Deactivating (the top-bar ✕, or "Show all tunes") restores the full
  alphabetical corpus list; the previously displayed tune stays displayed.
- The active playlist persists (`grilles.activePlaylist`) and is restored on
  reload; if that id no longer exists, the app starts deactivated.

### 11.5 Export & import

- **Export** downloads the whole `grilles.playlists` document as a JSON file
  (e.g. `grilles-playlists.json`), `{ "version": 1, "playlists": [ … ] }`. This
  is the user-initiated file download that is the app's only write outside
  `localStorage`.
- **Import** opens a file picker, reads a previously exported JSON, and **merges**
  its playlists into the current set: imported playlists are added as **new**
  entries (fresh `id`s) so import **never overwrites or deletes** existing
  playlists. Malformed/unsupported files are rejected with a small error message,
  leaving current playlists untouched. (Simple, non-destructive merge — the user
  can delete duplicates afterward.)

### 11.6 Responsive & theming

- On phones the Playlists menu and the add-to-playlist popover render as
  full-width sheets/drawers consistent with the tune-list drawer (§5.2); the
  header step-through buttons remain reachable.
- All playlist UI uses the §10 CSS custom properties (no hard-coded colors) and
  works in both themes.

---

## 12. Acceptance Checklist

- [ ] `python apps/displayer/build_data.py` regenerates `data/tunes_data.js` from `data/title_index.csv` + `data/chords/04_verified/`, and populates `apps/displayer/crops/` and `apps/displayer/melody_crops/` with every referenced scan.
- [ ] Bundled scans are 1-bit optimized PNGs; the two folders together stay in the ~120 MB range, and rerunning the build without source changes rewrites nothing (git status clean).
- [ ] Opening `apps/displayer/index.html` directly from disk (file://) lists **every** `data/title_index.csv` row, alphabetically by displayed title.
- [ ] Sidebar icons: a `both` row shows two icons (chord grid + melody); a `chords_only` row shows only the left icon, a `melody_only` row only the right; icon columns stay aligned. A digitized-chord tune shows the left icon **green**; scan-only assets show **gray**.
- [ ] A non-digitized tune shows a Title-Cased title (from the index) and its scan(s); a digitized-chord tune shows the JSON `title`, composer, and metadata line.
- [ ] The Chords and Melody switches show only for assets the tune has; default is Chords **on**, Melody **on when the tune has a melody**; both choices survive a reload.
- [ ] With both switches on: on a wide screen the chord and melody panels sit **side by side**, the chord grid filling its half; on a narrow/portrait screen they **stack** full width. With **no** melody, the lone chord panel is about **half the content width** (centered) on a wide screen and **full width** on mobile.
- [ ] Chord size is **width-driven**, not height-driven: the font scales with the panel width up to a **15px ceiling** and is the same for two same-width tunes regardless of length (`25_02_AUTUMN_LEAVES` and `25_01_AUTUMN_IN_NEW_YORK` render at the same size); a long tune keeps that readable size and the page **scrolls** rather than collapsing to fit one screen. On a wide screen the grid caps at ~577px (centered) instead of spreading.
- [ ] Grid −/+ zoom scales the current tune's grid; it **resets to 1× on every tune change and on reload** (not persisted).
- [ ] A digitized-chord tune with a chord scan offers the per-panel **original-scan toggle** (photo-icon button above the content, default: rendered grid, swap happens in place without scrolling to the top); same for a digitized-melody tune with a melody scan.
- [ ] `17_01_AINT_MISBEHAVIN` (Ain't Misbehavin') renders its melody as an abcjs lead sheet in the melody panel, in both themes; its right sidebar icon is green. Dropping a new `.abc` (named `<data/melody/01_crops stem>.abc`) into `data/melody/04_verified/` and rebuilding is all it takes to activate another tune.
- [ ] `22_02_AS_TIME_GOES_BY` renders: A / A / B / A sections labeled `A A B A`, 8 bars each as 2×4 rows, double barlines at every section boundary, `4/4` before the first barline.
- [ ] `Fm7b5` displays as `Fø7`; `Eb` shows `E♭`; `F#o7` shows `F♯o7`; `C9sus4`, `F69`, `N.C.` render sensibly.
- [ ] A chord on beat 3 sits at the horizontal middle of its bar.
- [ ] Sections with 2, 5, or 16 bars render without layout breakage (trailing empty slots).
- [ ] Search for `hupfeld` finds "As Time Goes By"; search is accent- and case-insensitive across the full corpus.
- [ ] Variants/Recordings/Notes blocks appear only for digitized-chord tunes when the data exists, collapsed by default.
- [ ] Theme toggle switches dark/light and survives a reload.
- [ ] Every tune header shows a `＋ Add to playlist` button; clicking it opens a
      popover to toggle membership in existing playlists or create a new one from
      the current tune. Adding a tune already in a playlist is a no-op.
- [ ] Playlists persist in `localStorage` (`grilles.playlists`) and survive a
      reload; a corrupt value starts an empty set without crashing.
- [ ] Activating a playlist filters the sidebar to its tunes in playlist order
      and enables `‹ Prev` / `Next ›` step-through (disabled at the ends);
      deactivating restores the full alphabetical corpus. The active playlist
      survives a reload.
- [ ] A playlist can be renamed, deleted, and its tunes reordered/removed;
      changes persist.
- [ ] Export downloads a `{ "version": 1, "playlists": […] }` JSON; importing it
      on another browser merges the playlists as new entries without overwriting
      or deleting existing ones; a malformed import is rejected without touching
      current playlists.
- [ ] A playlist referencing a tune id no longer in the corpus greys/skips that
      entry with a note instead of crashing.
- [ ] The app never writes to `data/chords/04_verified/`, `data/melody/01_crops/`, or
      `data/title_index.csv` — playlists touch only `localStorage` and a
      user-initiated export download.
