# Tune Verification App — Implementation Spec

## 1. Overview

A Flask web application for manually reviewing and editing the digitized tune JSON files in `data/chords/02_raw/`. The user can inspect, correct, and approve each tune one at a time. Verified tunes are copied to `data/chords/04_verified/`. A persistent state file tracks progress so sessions can be interrupted and resumed.

---

## 2. Technology Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, Flask |
| Frontend | Single-page HTML + Vanilla JS (no build step) |
| Persistence | `data/chords/03_wip/verification_state.json` (progress) + in-place JSON edits saved back to `data/chords/02_raw/` |
| Styling | Plain CSS (or optionally Pico CSS CDN — minimal dependency) |

No database. No npm. Runnable with `python apps/verifier/verify_app.py`.

---

## 3. File Conventions

### 3.1 Which files are tunes?
All `*.json` files in `data/chords/02_raw/` **except**:
- Files matching `*_opus.json` — ignored.
- `run_report.json`, `run_state.jsonl`, `verification_state.json` — system files, ignored.

The file stem determines the tune ID (e.g. `13_01_ALLIGATOR_CRAWL` from `13_01_ALLIGATOR_CRAWL.json`).

### 3.2 Crop images
Each tune may have a corresponding image in `data/chords/01_crops/` with the same stem + `.png` extension (e.g. `data/chords/01_crops/13_01_ALLIGATOR_CRAWL.png`). If the file exists it is shown; otherwise the image panel is hidden.

### 3.3 Verification state file
Path: `data/chords/03_wip/verification_state.json`

```json
{
  "last_opened": "13_01_ALLIGATOR_CRAWL",
  "verified": ["13_01_ALLIGATOR_CRAWL", "13_02_ALL_MY_LIFE"],
  "in_progress": "13_03_ALL_OF_ME"
}
```

- `last_opened`: the tune ID that was open when the app was last closed/navigated away.
- `verified`: list of tune IDs that have been approved and copied to `data/chords/04_verified/`.
- `in_progress`: (optional) tune ID of the last unsaved edit session.

Created automatically on first run if absent.

### 3.4 Verified output
Directory: `data/chords/04_verified/` (created automatically if absent).  
When a tune is verified, its **current (possibly edited) JSON** is copied there. The original in `data/chords/02_raw/` remains.

---

## 4. JSON Data Model

The tune JSON has two parts: **meta fields** and **sections**.

### 4.1 Meta fields (all optional except those marked *)

| Field | Type | Notes |
|---|---|---|
| `title`* | string | Always present in valid tune files |
| `composer` | string | |
| `year` | string | |
| `style` | string | |
| `tempo` | string | |
| `form` | string | e.g. `"32 A A B A"` |
| `time_signature`* | string | e.g. `"4/4"`, `"3/4"` |
| `page` | number | |
| `source` | string | |

Any field present in the JSON but not listed above should be preserved as-is (unknown fields round-trip through without modification).

### 4.2 Sections

`sections` is an ordered object whose keys are section names (e.g. `"A"`, `"B"`, `"A1"`, `"s1_A"`).  
Each value is an array of bar objects:

```json
{
  "bar": 1,
  "beats": {
    "1": "Fmaj7",
    "3": "C7"
  }
}
```

- Beat keys are strings `"1"` through `"4"` (for 4/4).
- A missing beat key means no chord on that beat (rest / held chord).
- The `bar` number is a display label only; it resets to 1 at each section on save.

### 4.3 Save format rules
- On save, renumber bars within each section starting at 1.
- Omit beat keys whose input cell is empty.
- Preserve section order as displayed.
- Pretty-print JSON with 2-space indent (matching existing files).

---

## 5. Application Structure

```
verify_app.py          # Flask entry point
templates/
  index.html           # single-page app shell
static/
  app.js               # all client-side logic
  style.css            # layout & component styles
```

---

## 6. Routes

### 6.1 Static / UI
| Method | Path | Description |
|---|---|---|
| GET | `/` | Serve `index.html` |
| GET | `/static/<path>` | Serve static assets |
| GET | `/crop/<tune_id>` | Stream the crop PNG from `data/chords/01_crops/`; 404 if absent |

### 6.2 API (all return JSON)
| Method | Path | Description |
|---|---|---|
| GET | `/api/tunes` | List all tune IDs with status |
| GET | `/api/tunes/<tune_id>` | Load a tune's JSON + state |
| PUT | `/api/tunes/<tune_id>` | Save edits back to `data/chords/02_raw/<tune_id>.json` |
| POST | `/api/tunes/<tune_id>/verify` | Mark as verified + copy to `data/chords/04_verified/` |
| DELETE | `/api/tunes/<tune_id>/verify` | Unmark verified (remove from `data/chords/04_verified/` and state) |
| PUT | `/api/state` | Update `last_opened` / `in_progress` |

#### GET `/api/tunes` response
```json
{
  "tunes": [
    { "id": "13_01_ALLIGATOR_CRAWL", "title": "ALLIGATOR CRAWL", "verified": false, "has_image": true },
    ...
  ],
  "total": 8,
  "verified": 2,
  "remaining": 6
}
```

Tunes are sorted by filename (numeric prefix first).

#### GET `/api/tunes/<tune_id>` response
```json
{
  "id": "13_01_ALLIGATOR_CRAWL",
  "verified": false,
  "data": { ...full tune JSON... }
}
```

#### PUT `/api/tunes/<tune_id>` request body
The full modified tune JSON object (same schema as above). The server validates structure before writing.

---

## 7. UI Layout

```
┌─────────────────────────────────────────────────────────┐
│ TUNE LIST (left sidebar, ~250px)  │  EDITOR (main area) │
│                                   │                     │
│ [2 / 8 verified]                  │  [Meta fields]      │
│ ─────────────────                 │  ─────────────────  │
│ ✓ ALLIGATOR CRAWL                 │  [Section A]        │
│ ○ ALL MY LIFE      ← selected     │  [Section B]        │
│ ○ ALL OF ME                       │  [+ Add Section]    │
│ ...                               │                     │
│                                   │  [Save] [✓ Verify]  │
└─────────────────────────────────────────────────────────┘
```

On narrow viewports the sidebar collapses to a top dropdown.

---

## 8. Editor — Detailed Behaviour

### 8.1 Meta fields panel
Renders as a 2-column form grid. Fields: `title`, `composer`, `year`, `style`, `tempo`, `form`, `time_signature`, `page`, `source`.

- Each field is a text `<input>`.
- An **"+ Add field"** button opens a small inline form to add an arbitrary key/value pair (for unknown fields).
- Deleting a meta field is possible via a ×  button next to each one.

### 8.2 Section block
Each section is a card with:
- **Section header row**: editable section name, move-up ↑ / move-down ↓ buttons, delete section ×.
- **Bar rows**: groups of up to 4 consecutive bars displayed side-by-side.
- **"+ Add row"** button below the last row of the section (appends 4 empty bars).
- **"+ Add bar"** button at the end of the last row (appends 1 bar to the section).

#### Row controls
Each row of bars has: move-up ↑ / move-down ↓ / delete × controls on the left margin. These operate on the entire row (4 bars) as a unit.

#### Bar cell
Each bar is a card:
```
┌────────────────┐
│  Bar 3         │
│ ╔══╦══╦══╦══╗  │
│ ║B1║B2║B3║B4║  │
│ ╚══╩══╩══╩══╝  │
│          [×]   │
└────────────────┘
```
- Bar number is shown as a non-editable label (auto-recomputed on save).
- The number of beat slots shown equals the numerator of `time_signature` (e.g. 4 slots for `"4/4"`, 3 slots for `"3/4"`). Defaults to 4 if the field is absent or unparseable.
- Each beat slot is an `<input type="text">` field with a small label (`1`, `2`, …).
- Empty inputs are saved as absent beat keys.
- The × button deletes just that bar.
- A **"+ bar"** button appears after the last bar in a section row to insert a new empty bar there.

### 8.3 Section ordering
Drag-and-drop is **not required**; use ↑/↓ buttons only. Simpler, no JS drag library needed.

### 8.4 Adding a new section
An **"+ Add Section"** button at the bottom of the editor opens a small prompt:
- Input: section name (default `"C"`).
- Creates a new section with 4 empty bars (one row).

### 8.5 Crop image panel
If a crop image exists for the tune, a panel is displayed to the right of (or above, on narrow screens) the editor:
- The image is shown scaled to fit the panel width.
- A **zoom** toggle expands it to full width in a scrollable overlay.
- If no image exists, this panel is hidden.

---

## 9. Verification Workflow

1. User edits the tune in the editor.
2. User clicks **Save** — PUT request writes the JSON back to `data/chords/02_raw/<id>.json`. A success toast confirms. `in_progress` state is cleared.
3. User clicks **✓ Mark as Verified** — triggers POST `/api/tunes/<id>/verify`:
   - Server copies the current `data/chords/02_raw/<id>.json` to `data/chords/04_verified/<id>.json`.
   - Updates `verification_state.json`: adds to `verified` list.
   - UI shows a green checkmark badge on the tune in the sidebar.
4. If the user wants to un-verify (e.g. found a mistake later), an **Unverify** button appears on already-verified tunes; triggers DELETE `/api/tunes/<id>/verify`.

---

## 10. Progress & Resume

### Progress bar
Displayed at the top of the sidebar:

```
Verified: 2 / 8   [████░░░░░░░░]  25%
```

### Resume on startup
When the app loads in the browser:
1. Fetch `/api/tunes` to get the list.
2. Fetch `/api/state` (or include in `/api/tunes` response) to get `last_opened`.
3. Automatically open the `last_opened` tune, or the first unverified tune if none was recorded.

### State persistence triggers
- On selecting a different tune in the sidebar → PUT `/api/state` with `last_opened`.
- On first edit of a tune (any keypress) → PUT `/api/state` with `in_progress`.
- On successful save → clear `in_progress`.

---

## 11. Unsaved Changes Guard

If the user navigates away from an unsaved tune (clicks another tune in the sidebar), a confirmation dialog is shown:
> "You have unsaved changes. Save before leaving?"  
> [Save & Continue] [Discard] [Cancel]

---

## 12. Validation (server-side, on PUT)

Minimal validation — reject only clearly broken data:
- `sections` must be a dict.
- Each section value must be a list.
- Each bar must have a `beats` dict with string keys `"1"`–`"4"`.
- Unknown top-level keys are passed through without error.

Return HTTP 400 with a JSON error message on failure.

---

## 13. Entry Point

`verify_app.py` at the repo root:

```python
# Usage:
#   python apps/verifier/verify_app.py
#   python apps/verifier/verify_app.py --tunes data/chords/02_raw --crops data/chords/01_crops --port 5000

import argparse
...
app.run(debug=True, port=args.port)
```

Opens `http://localhost:5000` in the default browser automatically on startup (`webbrowser.open`).

---

## 14. Out of Scope

- Authentication / multi-user support.
- `_opus.json` files — completely ignored.
- Undo/redo history (beyond browser's native input undo).
- Drag-and-drop reordering.
