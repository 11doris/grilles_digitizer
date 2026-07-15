# Harmonic Analysis Spec — functional symbolism for the displayer

Status: **implemented 2026-07-15** (owner decisions in §0; implementation
notes in §9)
Depends on: strain model (Phase C), key annotation (05_annotated), displayer.

The goal is memorization support: let a player read a tune as functions and
building blocks ("ii–V to the IV, then a turnaround") instead of 32 absolute
chords, and carry that understanding into any key via the existing
transposition feature.

Notation dialect follows the reference pages in `docs/harmonic_structure/`
(arrows & brackets, roman numerals with subV7/x and subii7/x, key-colon
prefixes, stacked pivot-chord modulations).

## 0. Owner decisions (2026-07-15)

1. **Block catalog**: start with a standard core set (§5.1); data-driven file
   so the owner's own list / Insights-in-Jazz bricks can extend it later.
2. **Chord view**: per-tune 3-way toggle Absolute / Hybrid / Roman, persisted
   per browser. Hybrid = small numeral under the printed chord in the same box.
3. **Overlay** (brackets, arrows, key prefixes, block labels): its own on/off
   toggle, independent of the chord-spelling mode — absolute chords + overlay
   reproduces the book's four-layer reading.
4. **Verification**: spot-check only. The analysis is deterministic and
   auto-regenerated; low-confidence tunes are flagged in a report, no new
   verifier UI. (Contrast: keys needed human verification because a wrong key
   poisons everything downstream; the analysis is *derived from* the verified
   key, so errors are local and fixed by improving rules.)

## 1. Architecture

A new deterministic module `pipelines/chords/harmonic_analysis/` computes a
`harmonic_analysis` derived field, written into `05_annotated` by the
annotation step. It is a **pure function of (strains chords, key,
section_keys)** — no LLM in the core path.

Consequences:

- **No staleness problem.** `key_annotation.core.update_annotation` (the only
  legal write path for corrections) recomputes `harmonic_analysis` on every
  save, exactly as it recomputes `opening` today. A key correction in the key
  verifier instantly yields the analysis under the new key. Only LLM-written
  prose (the existing `harmonic_fingerprint`) keeps the `stale` mechanism.
- `carry_annotation` (`--reuse-annotation`) recomputes it too, so source edits
  in 04_verified flow through without re-voting.
- Reruns over 1500 tunes are free and instant, so the rules and catalog can
  iterate without a re-annotation or re-verification campaign.
- Stored (rather than computed client-side) so the planned Flutter app renders
  it without reimplementing the analyzer in Dart. The displayer keeps only
  rendering logic. Roman numerals are transposition-invariant, so the
  displayer transpose feature needs no changes.

### Rule-based vs LLM (assessed 2026-07-15)

Rule-based covers the symbol layer essentially completely: degrees/qualities
(already implemented in `similarity/normalize.py` + `chords.js`), ii–V
detection, secondary dominants, tritone subs, to-minor moves, dominant chains,
diminished passing chords, catalog block matching. The fuzzy residue is
tonicization-boundary placement in ambiguous or modal passages; the analyzer
emits per-region confidence and flags low-confidence tunes in the run report.
An optional LLM QA voter over flagged tunes only (same adjudication pattern as
key annotation) may be added later — never a full-corpus LLM pass: at ~$15–50
per Batches run it is affordable once, but rule iteration would multiply both
the cost and the human re-review burden.

## 2. Analysis model (what the analyzer computes)

All numerals are computed against a **key context**: the global key by
default, a `section_keys` entry inside its section, or a detected local
region (§2.3). Degree spelling reuses `normalize.degree_name` (uppercase
maj/dom/aug/sus, lowercase min/m7b5/dim, accidental prefixes), extended with
the printed quality suffix: `ii7`, `V7b9`, `IΔ`, `#ivø7`, `bIIIo7`.

### 2.1 Per-chord function

For every chord slot: `{part, bar, beat, numeral, key: "G"|"A", role}` where
`role` classifies the token when it participates in a device:

- `sec_dom` — dominant resolving down a fifth to a non-tonic diatonic target →
  slash numeral `V7/x` (also `V7/V` chains).
- `sub_v` — dominant resolving down a half step → `subV7/x` (`subV7/I` = the
  plain tritone sub).
- `sec_ii` — the related ii in front of either → `ii7/x` (dotted-bracket
  partner of a subV gets `subii7/x`).
- `backdoor` — bVII7 resolving to I.
- `dim_passing` / `dim_aux` — diminished passing/auxiliary chords (`#io7`
  between I and ii, etc.).

### 2.2 Links (the arrows & brackets lane)

`{type, from: {part,bar,beat}, to: {part,bar,beat}}` with the book's five
marks:

| type        | rendering                | rule |
|-------------|--------------------------|------|
| `iiV`       | solid bracket            | min/ø chord then dominant a fourth up |
| `iiV_sub`   | dotted bracket           | ii–subV or subii–V pair |
| `fifth`     | solid arrow              | dominant resolving down a perfect fifth |
| `half`      | dotted arrow             | dominant resolving down a half step |
| `to_minor`  | headless arrow           | maj/dom chord to same-root m7 or ø7 |

### 2.3 Key regions

`{start, end, tonic, mode, kind, confidence}` with
`kind: "section" | "tonicization" | "modulation"`.

- `section` regions come verbatim from `section_keys` (owner-verified — never
  second-guessed).
- A resolved tonicization **shorter than 4 bars** stays slash-notated in the
  prevailing key (no region) — that is the "mixture of absolute and relative"
  reading: `ii7/IV  V7/IV  IVΔ`.
- **4 bars or longer** (tunable constant) opens a region: numerals inside are
  relative to it, the displayer prints the `Eb:` colon prefix at its start,
  and a pivot chord at the seam gets both readings for the stacked rendering
  (`F: iii7` over `G: ii7`).

### 2.4 Blocks

`{name, catalog_id, start, end, key}` — catalog matches over the numeral
sequence of a key context (§5). Overlapping matches: longest span wins, ties
broken by catalog order.

Beyond the catalog patterns and the code-detected ii–V chains / dominant
cycles, two **root-motion runs** are detected (2026-07-15, sourced from the
recurring LLM tag vocabulary): a *chromatic descent* (≥ 4 distinct roots
falling in half steps) and a *circle of fifths* (≥ 5 distinct roots falling
in fifths; one diminished-fifth step allowed for the diatonic circle's
IV–vii seam). Selection happens in three passes: **named blocks first**
(catalog + chains + cycles, longest span wins as above), then the runs are
**clipped into the gaps** — a turnaround, chain or cycle keeps the book's
name even when a longer run of falling roots passes through it — and
finally the **generic** plain cadences (`"generic": true` in catalog.json:
`cadence_251`, `cadence_251_minor`) take whatever space is left, so a
cadence that merely ends a detected run does not break the run up.

**Tags** (`harmonic_fingerprint.tags`) are derived from this analysis, not
from the LLM: the displayer's tag filter menu is built from these strings,
so each one must mean the same thing on every tune. Vocabulary
(`harmonic_analysis/tags.py`): `blues-form` / `minor-blues` (form says
BLUES), `minor-key`, `verse-present`, `modulates` (section keys or a
modulation region), `turnaround-ending` (turnaround block in a part's last
two bars), `ii-V-chains`, `circle-of-fifths`, `chromatic-descent`,
`dominant-cycle` / `dominant-cycle-bridge` (cycle in a B part),
`rhythm-changes-bridge`, `backdoor-cadence`, `iv-minor-cadence`,
`tritone-sub`, `passing-diminished`. Plain ii–V–I cadences are deliberately
untagged (they would tag nearly the whole corpus). Tags are recomputed
wherever the analysis is — LLM- or human-typed tags never survive.

## 3. JSON shape (in 05_annotated)

```json
"harmonic_analysis": {
  "version": 1,
  "parts": {
    "<part id>": {
      "chords":  [{"bar": 5, "beat": 1, "numeral": "ii7", "key": "G",
                   "role": null}],
      "links":   [{"type": "iiV", "from": {"bar": 5, "beat": 1},
                   "to": {"bar": 6, "beat": 1}}],
      "regions": [{"start": {"bar": 1, "beat": 1}, "end": {"bar": 4, "beat": 4},
                   "tonic": "Ab", "mode": "major", "kind": "tonicization",
                   "confidence": 0.9}],
      "blocks":  [{"catalog_id": "turnaround_1625", "name": "Turnaround",
                   "start": {"bar": 7, "beat": 1}, "end": {"bar": 8, "beat": 4},
                   "key": "G"}]
    }
  },
  "flags": ["low-confidence region in part B"]
}
```

Keyed by the strain model's generated part ids; repeated identical parts (the
three A's of an AABA) repeat their analysis so renderers stay dumb. `version`
bumps when the model changes; the annotate run recomputes any file whose
stored version is old (cheap, deterministic).

## 4. Displayer rendering

- **Chord toggle** (tune panel toolbar, next to the scan toggle): Absolute →
  Hybrid → Roman, persisted like the theme choice. Hybrid puts the numeral in
  a small line under the chord inside the box (uses the stored numeral — no
  client-side analysis); Roman replaces the chord, reusing the comparison
  view's `.chord.degree` styling.
- **Overlay toggle** (separate button): draws under each lattice row a thin
  SVG lane with brackets/arrows (§2.2), prints key-colon prefixes and stacked
  pivot numerals at region starts, and renders block spans as labeled
  underlines in the section-tint palette. Overlay works in all three chord
  modes.
- Boxes-view ("book layout") gets the same lane under each 8-box row;
  comparison view is untouched (its own switch already exists).
- Mobile: lanes add height; on narrow screens block labels collapse to the
  colored underline with the name on tap.

## 5. Block catalog

`pipelines/chords/harmonic_analysis/catalog.json` (data, not code): each entry
`{id, name, pattern, min_bars?}` where `pattern` is a sequence of degree
tokens with harmonic-rhythm wildcards (exact DSL defined at implementation;
kept simple: degree+quality-class tokens, `*` duration).

### 5.1 Starter set (owner: "standard core")

- `ii7 V7 IΔ` cadence (major + minor `iiø7 V7b9 i`)
- turnarounds: `IΔ vi7 ii7 V7`, `IΔ VI7 ii7 V7`, `iii7 vi7 ii7 V7`,
  `IΔ bIIIo7 ii7 V7`, tritone-sub variants (`IΔ bIII7 bVI7 bII7`)
- backdoor cadence `iv7 bVII7 IΔ`
- chromatic / cycle ii–V chains (≥2 consecutive ii–Vs descending by whole or
  half step, or around the cycle)
- `IΔ I7/IV` moves ("to the IV" launch, bar 4/5 of a blues)
- blues cadence core `I7 IV7 I7 V7 I7`
- montgomery-ward / honeysuckle-style bridge patterns (`I7/IV` then `V7/V ii V`)

Extension path: the owner's curated list and/or Elliott's bricks get appended
to the same file; the matcher does not change.

## 6. Pipeline & invalidation changes

- `annotate_keys.py`: after key resolution, compute `harmonic_analysis`
  (fresh runs, `--reuse-annotation`, `--set-key`, and a version-bump sweep).
  The sweep (`refresh_derived_fields`) covers all deterministic derived
  fields — `harmonic_analysis`, `opening`, and the block-derived
  `harmonic_fingerprint.tags` (§2.4) — so a rule change re-materializes the
  corpus on the next run. `--status` counts files whose derived fields are
  outdated.
- `key_annotation/core.py`: `build_annotation`, `carry_annotation`, and
  `update_annotation` all call the analyzer — the key verifier therefore
  **needs no new invalidation logic**; a key correction regenerates the
  analysis in the same save. The existing fingerprint `stale` flow is
  unchanged.
- Run report lists flagged tunes (low-confidence regions, unparseable tokens)
  for spot checks.

## 7. Testing

- Unit tests per rule (secondary dominants, subV, to-minor, backdoor,
  diminished passing, region opening) on synthetic charts.
- A hand-checked fixture set of ~10 real tunes spanning: plain AABA in one key
  (Djangology — expects the B-section A-major region from `section_keys`),
  a blues, a Rhythm-changes bridge (V/V chain), a tune with a subV cadence,
  a minor tune, a multi-strain tune.
- JS side needs no analysis tests (it only renders stored data); one DOM test
  for the toggle cycling + persistence, one for the overlay lane.

## 8. Phases

1. **Toggle** — 3-way chord view in the displayer main grid reading stored
   numerals; ship behind the numeral-only subset of the analyzer (per-chord
   `chords` array, global + section keys, no links/regions/blocks yet).
2. **Devices** — links + roles + key regions in the analyzer; overlay lane
   rendering (brackets, arrows, colon prefixes, stacked pivots).
3. **Blocks** — catalog file + matcher + labeled span rendering; optional
   "tunes containing block X" filter in the list pane.
4. **QA (optional)** — LLM voter over flagged tunes only; surfacing in the
   run report.

## 9. Implementation notes (2026-07-15, phases 1–3 shipped together)

Decisions made during implementation, where they refine the sections above:

- **Region confidence gate** (§2.3): a candidate region below confidence
  0.7 (strictly-diatonic share) is not applied at all — a dominant cycle
  "works toward" many keys without being in any of them (the I Got Rhythm
  bridge must stay a dominant cycle, not a G-major region). Applied regions
  below 0.8 are flagged. Growth rules: backward over chords functional in
  the candidate key that have *left* the outer key, plus one pivot chord
  diatonic in both; forward over chords the outer key cannot explain, plus
  direct re-cadences onto the candidate tonic; the 4-bar minimum is
  measured on the core (pivot excluded).
- **Cross-part resolution** (§2.1): a part-final dominant may resolve into
  the next part's first chord (the last part wraps to the first part of
  its own strain), naming the dominant (V7/x) without drawing an arrow.
- **Ascending passing diminished** spells sharp (`#io7`, not `biio7`).
- **Slash targets are uppercase degrees** (`V7/II`, `subV7/IV`) whatever
  chord sits on the target — matching the book's pages.
- **Blocks carry no key field**; renderers don't need it.
- **catalog.json format**: `{id, name, pattern, max_bars}` with pattern
  tokens `<degree>:<quality-class|any>` (e.g. `"ii:min V:dom I:maj"`);
  ii–V chains and dominant cycles are code-detected, not catalog entries.
- **Overlay lanes ship in both chord views** (4-bar grid and book layout);
  the comparison view keeps its own switch and gets no lanes. Toolbar:
  the spelling switch and overlay toggle appear only on analyzed tunes.
- `annotate_keys.py` recomputes analyses by *value comparison* (write on
  change), so analyzer/catalog edits propagate without a version bump;
  `version` stays in the JSON for readers.
