# Strain Model — Phase C Plan (explicit strains in the tune JSON)

Status: **planned, not implemented.** This document is the concrete design and
migration plan for making *strains* a first-class part of the tune JSON, so the
strain / label / role of every section stops being encoded in — and re-parsed
out of — the section-key string.

Prior context: Phases A and B (a single shared strain classifier; build-time
`form_strains`; removing case-as-semantics) were considered as cheaper interim
steps. The corpus is early (~180 verified tunes) and expected to grow by an
order of magnitude, so the format is being locked now via Phase C directly
rather than migrating a much larger corpus later.

---

## 1. Motivation

Today three things carry the form/strain information, and they must be kept
mutually consistent by hand and by an alignment cross-check:

| Field | Role today | Problem |
|---|---|---|
| `form` (string) | printed form, e.g. `"16 A A \| 16 A A \| 16 A B"` | also the machine source for bar counts, label order, strain order — brittle prose parsing |
| `section_labels` (map) | recovers printed primes (`A'`) the keys throw away | denormalised; editable, so must persist |
| `form_strains` (map) | per-strain `{bars, labels}` | purely derived, yet stored in every file → staleness + `source_sha256` churn |
| `sections` (map) | chord data, keyed `impro_B` etc. | strain + label baked into the key string; classified by regex in **three** places (`normalize._strain_of_key`, displayer `strainOf`, verifier `unknownStrain`), which disagree on edge cases; capitalisation silently decides aux-vs-strain (the Chattanooga `Transition_T` footgun) |

The cross-check between `form` and `sections` is the source of the HARD/SOFT
warnings and the `KNOWN_FORM_DEFECTS` churn. Phase C makes the structure
authoritative and demotes `form` to a display string, so there is nothing to
cross-check.

---

## 2. Goals / non-goals

**Goals**
- Strain identity (`name`, `role`) is an explicit field, never parsed from a key.
- Printed labels (with primes) live on the part they label.
- The "identical parts stored once" repeat is explicit data, not inferred.
- One classifier, in Python, consumed everywhere; no JS regex re-derivation.
- Behaviour-preserving: identical rendered output and identical similarity slots.

**Non-goals**
- No change to the chord payload (`{"bar", "beats": {...}}`) — only its container.
- No change to the digitizer/VLM raw output shape (`02_raw` stays a section map).
- No new musical analysis; primes stay display-only unless similarity later needs them.

---

## 3. Target schema

The `sections` map is replaced by an ordered `strains` list. Each strain has a
`name`, a `role`, and an ordered list of `parts`. Each part has a printed
`label`, an optional `plays` repeat count (default 1), and its `bars`.

```json
{
  "form": "16 A A | 16 A A | 16 A B",     // retained VERBATIM, display only
  "strains": [
    {
      "name": "intro",
      "role": "aux",
      "parts": [
        { "label": "A", "plays": 2,
          "bars": [ { "bar": 1, "beats": { "1": "Am" } }, "…8 bars" ] }
      ]
    },
    {
      "name": "thema",
      "role": "strain",
      "parts": [
        { "label": "A", "plays": 2,
          "bars": [ { "bar": 1, "beats": { "1": "Am" } }, "…8 bars" ] }
      ]
    },
    {
      "name": "impro",
      "role": "strain",
      "parts": [
        { "label": "A", "bars": [ { "bar": 1, "beats": { "1": "Am" } }, "…8 bars" ] },
        { "label": "B", "bars": [ { "bar": 1, "beats": { "1": "Dm" } }, "…8 bars" ] }
      ]
    }
  ]
}
```

Distinct labels get distinct parts (Tailgate's `part1` = `"A A'"`):

```json
{ "name": "part1", "role": "strain", "parts": [
    { "label": "A",  "bars": ["…8 bars"] },
    { "label": "A'", "bars": ["…8 bars"] }
] }
```

A plain lettered chorus (the common case) is a single strain named `"chorus"`:

```json
{ "name": "chorus", "role": "chorus", "parts": [
    { "label": "A",  "bars": ["…"] },
    { "label": "A'", "bars": ["…"] },
    { "label": "B",  "bars": ["…"] },
    { "label": "A",  "bars": ["…"] }
] }
```

### 3.1 Field reference

| Path | Type | Notes |
|---|---|---|
| `strains` | list | ordered as printed / played |
| `strains[].name` | string | lowercase; `"chorus"`, `"verse"`, `"impro"`, `"part1"`, a connector name (`"intro"`, `"coda"`, `"transition"`), … |
| `strains[].role` | enum | see §4 |
| `strains[].parts` | list | ordered |
| `strains[].parts[].label` | string | printed label with primes: `"A"`, `"A'"`, `"B"`, or a word `"BLUES"` |
| `strains[].parts[].plays` | int, optional | number of times this part is played in a row; default `1`. Encodes the old "identical parts stored once" rule |
| `strains[].parts[].bars` | list | the chord payload, **unchanged** (`{"bar": n, "beats": {...}}`), numbered `1..k` within the part |

Derived, never stored (computed by the one classifier at build/read time):
- a strain's total bars = `sum(len(part.bars) * part.plays)`
- a strain's label sequence = `[label repeated plays times for each part]`
  (i.e. today's `form_strains[name].labels`)

### 3.2 `label`: string, not split

`label` stays a single string (`"A'"`), **decided**, reversible. Splitting into
`letter: "A"` + `prime: 1` makes "same letter different prime" queryable but is
more verbose and nothing needs it yet (similarity compares chords, not labels).
If needed later, add derived `letter`/`prime` at build time without touching the
stored files.

### 3.3 Variants

Tune `variants` currently name a section key in `applies_to`. Under Phase C a
variant references a strain + part position instead:

```json
"variants": [ { "applies_to": { "strain": "chorus", "part": 3 }, "bars": ["…"] } ]
```

(`part` is the 0-based index into that strain's `parts`.) The variant `bars`
payload is unchanged.

---

## 4. Role vocabulary

`role` replaces the case-and-prefix inference in `_strain_of_key`. The migration
derives it from today's classification so behaviour is preserved.

| `role` | Migrated from (today) | Tint | Caption | In similarity slots? |
|---|---|---|---|---|
| `chorus` | plain-letter keys (`A`, `B1`) | per-letter hue | named only against a verse | yes |
| `verse` | `verse_*` keys | uniform (verse colour) | "Verse" | **no** (verses excluded) |
| `strain` | lowercase-prefix / `sN_` named strains (`impro`, `thema`, `part1`, `s1`, `blues`, …) | uniform per-name | title-cased name | yes (as today) |
| `aux` | bare / capitalised connectors (`coda`, `interlude`, `Transition`) | uniform per-name | title-cased name | yes (as today — expand includes them) |

Notes:
- The `NAMED_STRAINS` allow-list (verifier policy) becomes an allow-list on
  `name` for `role: "strain"`, defined once in Python and exported to the
  clients (see §7). Adding a strain = one edit.
- `aux` connectors get an explicit case-insensitive vocabulary
  (`AUX_CONNECTORS = {intro, coda, interlude, transition, tag, vamp, …}`), so a
  capitalised connector can never silently misgroup — this folds Phase B into
  Phase C. An unrecognised `name`/`role` combination is a **loud validation
  error at edit time**, not a downstream count mismatch.
- Whether `aux` sections belong in the similarity slots must be pinned to
  *current* `expand_tune` behaviour during migration (see §6, open decision O3).

---

## 5. What Phase C removes

- `form_strains` (stored) — it *is* the `strains` list.
- `section_labels` (stored) — `label` lives on the part.
- `_strain_of_key` / `strainOf` / `strainNameOf` / `unknownStrain` key regexes.
- The `form`↔`sections` alignment (`derive_labels`, `_assign`, `_segment_labels`,
  `parse_form`, `_verse_form_from_notes`) and the HARD/SOFT warning machinery,
  including `KNOWN_FORM_DEFECTS`. `form` becomes a display string only.
- Case-as-semantics (Phase B footgun).

`form` is kept verbatim for display and as a human sanity aid; it is no longer
parsed. A one-line lint may optionally warn if `form`'s obvious bar total
disagrees with the structure, but it is advisory, never blocking.

---

## 6. Migration

Deterministic and low-risk because it reads the **already-derived, already-verified**
`form_strains` + `section_labels` + `sections`, which between them encode exactly
the parts, labels, and repeat counts. Effectively: run today's `derive_labels`,
then reshape its output into `strains`.

**Algorithm** (`pipelines/chords/tools/migrate_to_strains.py`, new):
1. For each tune, compute `structured, labels, _ = derive_labels(tune)` (reuse
   the current authority so the mapping matches what is already verified).
2. Walk `section_groups(tune)` in document order. For each strain group:
   - `name` = the group's strain id (`chorus` for the plain-letter group;
     `verse`; the prefix otherwise).
   - `role` from §4.
   - Build `parts` from the group's stored keys + `labels` + the strain's
     label sequence in `structured`:
     - distinct labels, one key each → one part per key.
     - one stored key whose strain repeats an identical label N times →
       one part with `plays: N` (the "identical parts stored once" case).
   - `bars` = the stored key's bars, unchanged.
3. Aux sections (not in any group) → single-part `aux` strains, `label` from
   `labels[key]`, `plays: 1`.
4. Rewrite `variants[].applies_to` from a key to `{strain, part}`.
5. Drop `form_strains` and `section_labels`; keep `form`.

**Properties**
- Idempotent (re-running on a migrated file is a no-op).
- Byte-stable ordering (document order preserved).
- Runs over `03_wip`, `04_verified`, `05_annotated`.

**`source_sha256` handling.** The annotation carries `key_annotation.source_sha256`
over the source. Decide (O1) whether the hash is recomputed over the new shape
(one clean bump for all annotated tunes, via `annotate_keys --reuse-annotation`
after migration) or whether the migration is treated as shape-only and excluded
from the hash. Recommended: recompute once, since the source genuinely changed.

**Equivalence gate.** Before/after, assert for every tune that (a) the flattened
similarity slots from `expand_tune` are identical, and (b) the derived per-strain
labels + bar totals match today's `form_strains`. This is the safety net; the
migration is not accepted until it is byte-identical on both.

---

## 7. Consumer changes, module by module

**`pipelines/chords/similarity/normalize.py`**
- Replace `_strain_of_key`, `section_groups`, `strains_from_labels`,
  `_key_fallback_label`, `parse_form`, `_segment_labels`, `_verse_form_from_notes`,
  `derive_labels`, `form_warnings`, `form_hard_warnings` with a small read layer
  over `strains`: `iter_parts(tune)`, `strain_label_seq(strain)`,
  `strain_bars(strain)`, `is_compared(strain)`.
- `expand_section`/`expand_tune` walk `strains[].parts[]` (respecting `plays`);
  the verses-excluded rule becomes `role != "verse"` (plus O3 for aux).
- Keep `NAMED_STRAINS` + add `AUX_CONNECTORS`; add `validate_strains(tune)`
  returning loud errors for unknown `name`/`role`.

**`apps/displayer/build_data.py`**
- No more injecting `form_strains`; the bundle carries `strains` directly.
- Export `NAMED_STRAINS` + `AUX_CONNECTORS` into a generated `named_strains.js`
  used by both apps (single source).

**`apps/displayer/app.js`**
- `strainOf` / `strainNameOf` / `strainFormLabel` become field reads over
  `strains`. `renderBoxGrid` and the grid loop iterate `strains[].parts[]`;
  `plays` replaces the "identical parts stored once" special-case. `sectionTint`
  keys off `role`/`name` (chorus → per-letter, else per-name), unchanged visuals.

**`apps/verifier/verify_app.py` + `static/app.js`** (the bulk of the build cost)
- Editor edits the nested model: add/remove strains, reorder, set `role`,
  add/remove parts, set `label`/`plays`. Chord editing per part is unchanged.
- `api_derive` / `api_save` validate via `validate_strains` (loud, at save).
- **Ingest conversion:** `02_raw` is still a section map, so the verifier
  converts map → `strains` on first load of a tune (reusing the migration
  reshape). Provide a **fast path**: accept a typed form string
  (`"16 A A' B B'"`) as a shortcut that expands into strains/parts, so entry
  stays quick even though the stored artifact is the explicit structure.
- Drop the `NAMED_STRAINS` array + `unknownStrain` regex; use the generated
  constant.

**Similarity corpus (`similarity/corpus.py`, `align.py`, slot_map)**
- Slot map keyed off section names moves to `(strain, part, index)` addressing.
  Verse exclusion → `role == "verse"`. Confirm aux inclusion against O3.

**`pipelines/chords/annotate_keys.py` + `key_annotation/core.py`**
- `carry_annotation` / opening computation read `strains`. The reuse path
  re-bumps `source_sha256` after migration (O1).

**Tools**
- `check_form.py`: retire (nothing to cross-check) or repurpose as the advisory
  `form`-vs-structure lint.
- `check_chord_syntax.py`: walk `strains[].parts[].bars` instead of `sections`.
- `build_examples.py`: `DERIVED_FIELDS` (`form_strains`, `section_labels`) no
  longer exist; strip logic updated.
- `migrate_form_labels.py`: superseded by the migration; archive.

**Specs**
- Update `docs/specs/jazz_chord_digitization_spec.md`,
  `verification_app_spec.md`, `displayer_app_spec.md`, `tune_similarity_spec.md`
  to the `strains` shape; note `02_raw` keeps the legacy map.

**Tests**
- Rewrite `test_normalize.py` form/strain tests against `strains`; drop the
  `form`-alignment and `KNOWN_FORM_DEFECTS` tests. Add the §6 equivalence gate as
  a corpus test. Update `test_keys.py` fixtures. Add `validate_strains` branch
  tests (chorus / verse / named strain / aux / loud-reject).

---

## 8. Rollout order (each step independently verifiable)

1. **Schema + read layer + validator** in `normalize.py`, behind new functions;
   no data changed yet. Unit tests on hand-written `strains` fixtures.
2. **Migration script** + §6 equivalence gate. Run on a copy; prove byte-identical
   slots and labels across the whole corpus. Do not commit migrated data yet.
3. **Similarity + build_data + displayer** switched to `strains`; rebuild bundle;
   visual diff Minor Swing / Tailgate / Fly Me / Chattanooga in both layouts;
   similarity outputs unchanged.
4. **Verifier** editor + ingest conversion + fast-path shortcut; manual pass on a
   handful of tunes (a chorus-only, a multi-strain, a verse, an aux/transition).
5. **Commit the migrated corpus** (`03_wip` + `04_verified` + `05_annotated`),
   re-bump annotations (O1), rebuild bundle.
6. **Retire** dead code and update specs.

**Gate at every step:** `check_chord_syntax` clean; full Python suite green; the
equivalence gate green; displayer visually unchanged.

---

## 9. Open decisions

- **O1 — `source_sha256`:** recompute once over the new shape (recommended) vs
  treat migration as hash-excluded shape change.
- **O2 — container name:** `strains` (chosen here) vs keeping `sections` as the
  key and nesting. `strains` is clearer; costs a global rename.
- **O3 — aux in similarity slots:** pin `aux` connectors' inclusion to current
  `expand_tune` behaviour; decide if that behaviour is also *desired* or should
  change (out of scope, but confirm before the slot_map move).
- **O4 — `plays` vs explicit repeated parts:** `plays: N` (chosen) is compact and
  matches the current "stored once" reality; the alternative is N identical parts
  each with their own bars (more uniform, larger files). Keep `plays`.
- **O5 — verifier fast-path scope:** how much of the old form-string parsing to
  keep purely as an entry shortcut (nice-to-have) vs a fully manual strain editor
  (simplest to build). Affects §7 verifier effort.
```
