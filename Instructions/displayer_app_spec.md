# Grilles Displayer — Implementation Spec

## 1. Overview

A fully static web app that displays the verified tune JSON files from `./tunes_verified/` as nicely styled jazz chord grids ("grilles"), one tune at a time. The visual reference is `grilles_displayer/Screenshot_20260702-154639.png` (dark background, large condensed chord symbols, boxed section letters, double barlines at section boundaries).

The app is read-only: it never modifies `tunes_verified/`.

---

## 2. Technology Stack

| Layer | Choice |
|---|---|
| Frontend | Single-page HTML + Vanilla JS + plain CSS. No framework, no npm, no build tooling. |
| Data | A generator script bundles all verified tunes into one JS file, so the app works when opened directly from `file://` (no server, no CORS issues). |
| Generator | Python 3.11+ script, standard library only. |
| Fonts | Barlow Condensed (Google Fonts), bundled locally as woff2 in `fonts/` and loaded via `@font-face`. No CDN requests at runtime — the app must work offline. System condensed sans as fallback. |

---

## 3. File Layout

```
grilles_displayer/
├── index.html            # the app shell
├── style.css             # all styling, incl. dark/light themes
├── app.js                # search, navigation, rendering logic
├── chords.js             # chord token parsing + display transformation
├── build_data.py         # generator: tunes_verified/*.json -> data/tunes_data.js
├── fonts/                # bundled Barlow Condensed woff2 (400/500/700)
├── crops/                # GENERATED — per-tune scan PNGs copied from ../crops
└── data/
    └── tunes_data.js     # GENERATED — do not edit by hand
```

Everything the deployed app needs lives inside `grilles_displayer/` — the GitHub
Pages workflow uploads only this directory.

### 3.1 Generator (`build_data.py`)

- Usage: `python grilles_displayer/build_data.py` (default input `./tunes_verified`, overridable with `--tunes-dir`).
- Reads every `*.json` in the input directory, **preserving JSON key order** (section order matters — use `json.load` which keeps insertion order).
- Skips non-tune files (`verification_state.json`, `run_report.json`, etc. — same ignore list as the verifier).
- Writes `data/tunes_data.js` containing a single assignment:

```js
window.TUNES = [
  { "id": "22_02_AS_TIME_GOES_BY", ...full tune JSON... },
  ...
];
```

- `id` is the file stem. Tunes are sorted alphabetically by `title`.
- For each tune with a matching scan (`<crops-dir>/<id>.png`, default `../crops`,
  overridable with `--crops-dir`), the PNG is copied to `grilles_displayer/crops/`
  and the record gets `"image": "crops/<id>.png"`. Stale copies are pruned.
- The script prints a one-line summary (`Wrote 25 tunes to data/tunes_data.js`) and exits non-zero with a clear message if a file fails to parse.
- Rerun manually whenever tunes are verified; the app itself never reads `tunes_verified/` directly.

---

## 4. Data Model (input)

As produced by the digitizer / verifier (see `Instructions/jazz_chord_digitization_spec.md`). Relevant parts:

- Meta: `title`*, `composer`, `year`, `style`, `tempo`, `form`, `time_signature`* (e.g. `"4/4"`), `page`, `source`, `same_chord_changes`.
- `sections`: ordered object; keys are section names (`"A"`, `"A1"`, `"B"`, `"C"`, but also `"verse_A"`, `"interlude"`, `"Transition"`); values are arrays of bar objects:

```json
{ "bar": 3, "beats": { "1": "Eb", "3": "Fm7", "4": "F#o7" } }
```

- `variants`: optional array of `{ "applies_to": "<string>", "bars": [ <bar objects> ] }`.
- `recordings`: optional array of strings.
- `notation_notes`: optional object of string → string.

Unknown fields are ignored (not an error). Missing optional fields simply hide their UI element.

Currently all verified tunes are 4/4 with sections of mostly 8 or 16 bars, but the app must handle any bar count (2- and 5-bar sections exist) and any `n/4` time signature.

---

## 5. UI Layout

```
┌────────────────────────────────────────────────────────────┐
│  Grilles   [ search…            ]                    ☾/☀  │  top bar
├──────────────┬─────────────────────────────────────────────┤
│ tune list    │              (Medium Swing)   Irving Berlin │
│ (sidebar,    │                ALL BY MYSELF                │
│  scrollable) │        Standard · 1921 · 32 A B A C · p.14  │
│              │                                             │
│  All By…     │  [A]                                        │
│  All Of Me   │  4/4 ║ C∆7 │ C6 │ D7 │ Am7 D7 │             │
│  As Time…    │      │ G7 │ Dm7 G7 │ Em7 A7 │ Dm7 G7 ║      │
│  …           │  [B]                                        │
│              │  ║ … chord grid …                    ║      │
│              │                                             │
│              │  ▸ Variants   ▸ Recordings   ▸ Notes        │
└──────────────┴─────────────────────────────────────────────┘
```

### 5.1 Top bar
- App name, a search input, a scan toggle button (▦, hidden when the current
  tune has no image), and a theme toggle button. On narrow screens a ☰ button
  (leftmost) opens the tune-list drawer.
- The search input has focus on page load. `Esc` closes the scan overlay /
  drawer if open, otherwise clears the search.

### 5.2 Sidebar — tune list
- All tunes listed alphabetically by title; each entry shows the **title** and, in smaller muted text, the **composer**.
- The search box filters the list live (see §8). The currently displayed tune is highlighted.
- Clicking an entry displays that tune in the main panel.
- Keyboard: `↑`/`↓` move the highlight through the (filtered) list, `Enter` opens the highlighted tune.
- On narrow screens (≤ 700px) the sidebar is hidden and becomes a slide-in
  drawer over the grid, opened with the ☰ button (typing in the search box also
  opens it). Picking a tune, tapping the backdrop, or `Esc` closes it.

### 5.2a Original scan (PNG)

- Each tune with an `image` field gets a ▦ toggle in the top bar showing the
  original scanned grille (`crops/<id>.png`).
- Wide screens (≥ 900px, laptop / landscape tablet): the scan docks as a sticky
  panel to the right of the chord grid, **on by default**. Clicking the docked
  scan expands it to a fullscreen view.
- Narrow screens (mobile portrait, small tablets): the scan opens as a
  fullscreen overlay.
- In the fullscreen view, clicking the image toggles a magnified state (up to
  natural size, capped at 250vw on small screens) panned by scrolling, starting
  centered. Clicking the backdrop, the ✕ button, or `Esc` closes the
  fullscreen view (back to the dock on wide screens).
- The on/off choice persists in `localStorage` (`grilles.image`); on load it is
  only restored on wide screens (never greet mobile with an overlay).

### 5.3 Main panel — tune header
Mirrors the screenshot:
- **Title**: centered, large, bold.
- **Tempo**: top-left, in parentheses, e.g. `(Slow)` — title-cased from the JSON value.
- **Composer**: top-right.
- **Metadata line** below the title, small and muted: `style · year · form · p. N`. The `source` field is not displayed. Fields that are absent are simply omitted.

### 5.4 Deep linking
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
- If a chord symbol overflows its slots, it may shrink (`font-size` step-down) rather than wrap; chords never wrap to a second line.

### 6.5 Responsive behavior
- The grid targets a comfortable max width (~900px), centered. Below that, bars shrink fluidly; chord font size scales with bar width (CSS `clamp()`/container-relative units). No horizontal page scrolling.
- **Fit to one page**: all grid dimensions are em-based; after rendering, a JS
  fit pass shrinks the grid's base font size until header + grid fit the
  viewport height without scrolling, down to a floor of 8px (below that the
  page scrolls rather than becoming unreadable). Extras (§9) may fall below
  the fold. The grid's width cap is em-based too (38em ≈ 646px at the default
  17px font) so bar width stays proportional to chord size on any screen.
- **Grid zoom**: floating −/+ buttons (bottom right) scale the fitted grid
  size by a user factor (×1.15 steps, clamped 0.5–2.5, persisted in
  `localStorage` as `grilles.gridzoom`); zooming in past the fitted size makes
  the page scroll.
- The tune head stacks to a single centered column when the pane is narrow
  (small screen or docked scan) via a container query.

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
bass      := "/" [A-G] accidental?    (only when "/" is followed by a note letter —
                                       "F6/9" is a quality, not a bass note)
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
| everything else (`m7`, `6`, `9`, `7#5`, `9sus4`, `7(13)`, `6/9`, `m11`, …) | verbatim, with the accidental substitution above |

The root's own accidental also renders as `♯`/`♭`.

### 7.3 Visual style (matching the screenshot)

- **Root letter**: very large (~2.4em relative to body), regular weight (not bold), condensed sans (`'Barlow Condensed', 'Arial Narrow', 'Helvetica Neue', Arial, sans-serif`).
- **Root accidental**: ~50% of root size, raised (superscript position, tucked against the root).
- **Quality**: ~45% of root size, **bottom-aligned with the root's baseline area** (subscript look: `C∆7`, `Am7`, `G7` as in the screenshot). Alterations in parentheses render at the same small size.
- **Bass note** (`/Eb`): small, placed below-right of the chord after a short slash, like `Aø7/E♭` in the screenshot.
- **Optional chords** `(…)`: whole symbol wrapped in thin parentheses at reduced size and slightly muted color.
- `N.C.`: small caps, muted.

### 7.4 Robustness
- Any chord string that fails to parse renders verbatim (monospace, warning color in a `title` tooltip) — never crash the page. (Verified files pass `tools/check_chord_syntax.py`, so this is a safety net only.)

---

## 8. Search

- Single search box; matches against **title** and **composer**.
- Case-insensitive, diacritic-insensitive (`normalize('NFD')` + strip combining marks), substring match on either field. Multiple whitespace-separated terms are AND-ed (every term must match title or composer).
- Filtering is live on every keystroke (25–500 tunes: no debounce needed, no index required).
- Empty query shows the full list. Zero matches shows a "No tunes found" message in the sidebar.

---

## 9. Extras (below the grid)

Rendered as four collapsible `<details>` blocks, collapsed by default, in small muted type:

1. **Same changes** — the `same_chord_changes` string, when present.
2. **Variants** — for each entry in `variants`: its `applies_to` string as a caption, then its `bars` rendered as a mini chord grid (same renderer as §6/§7 at ~65% scale, 4 bars per row, single barlines only — variants have no sections).
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

## 11. Acceptance Checklist

- [ ] `python grilles_displayer/build_data.py` regenerates `data/tunes_data.js` from `tunes_verified/`.
- [ ] Opening `grilles_displayer/index.html` directly from disk (file://) shows the app with all verified tunes listed.
- [ ] `22_02_AS_TIME_GOES_BY` renders: A / A / B / A sections labeled `A A B A`, 8 bars each as 2×4 rows, double barlines at every section boundary, `4/4` before the first barline.
- [ ] `Fm7b5` displays as `Fø7`; `Eb` shows `E♭`; `F#o7` shows `F♯o7`; `C9sus4`, `F6/9`, `N.C.` render sensibly.
- [ ] A chord on beat 3 sits at the horizontal middle of its bar.
- [ ] Sections with 2, 5, or 16 bars render without layout breakage (trailing empty slots).
- [ ] Search for `hupfeld` finds "As Time Goes By"; search is accent- and case-insensitive.
- [ ] Variants/Recordings/Notes blocks appear only when the data exists, collapsed by default.
- [ ] Theme toggle switches dark/light and survives a reload.
- [ ] The app never writes to `tunes_verified/`.
