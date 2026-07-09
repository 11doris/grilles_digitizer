# Tune Similarity — Implementation Spec

**Goal:** For every digitized tune, suggest other tunes that are harmonically similar — for the
whole form or for a single section — so that a player who has learned one tune discovers what else
is within reach. Two UIs consume the result: a standalone **similarity explorer** (debug/quality
tool) and a **"Suggest similar tunes"** feature in the existing displayer app.
**Scale:** must work unchanged at **1500–2500 tunes** (currently ~32 digitized; the corpus grows
continuously as tunes are verified).
**Audience:** an implementing agent working in this repository.

This document is the single source of truth for this feature. Where it conflicts with earlier
discussion notes, this document wins.

---

## 1. Locked design decisions

These were decided with the project owner and are **not** open for re-litigation during
implementation:

| Decision | Choice |
|---|---|
| Similarity semantics | **Spectrum, scored**: one score from ~1.0 (contrafact / identical changes) down to loosely similar (same functional movements). The UI always shows the score. |
| Section matching | **Any ↔ any**: the bridge of tune X may match the A section of tune Y, regardless of label or position. Whole-tune similarity is computed as well. |
| Major/minor handling | **Cross-mode, penalized**: every tune is normalized relative to its own tonic into one shared pitch space (majors as if in C, minors as if in A minor). Cross-mode matches are possible; mode mismatch lowers the score naturally and via a small explicit penalty. |
| Comparison display | **Three switchable renderings** in the displayer comparison view: original printed keys / suggestion transposed into the current tune's key / roman-numeral degrees. |
| Compute model | **Everything precomputed offline** by pipeline scripts; both UIs are static (the displayer deploys to GitHub Pages). No server. |
| Learned embeddings | Not in the core engine. Included only as an **optional, evaluated experiment** (§7.6) against the n-gram retrieval baseline, using the same evaluation set as everything else. |

---

## 2. Architecture and data flow

```
data/chords/04_verified/*.json           (read-only for this pipeline)
        |
        v
PHASE 0  annotate_keys      -> data/chords/05_annotated/*.json   (verbatim copy + key,
        |                                                          key_annotation, fingerprint)
        v
PHASE 1  normalize (library) -> in-memory tonic-relative token sequences
        |
        v
PHASE 2  evaluation set      -> data/chords/eval/                (ground-truth families, ratings,
        |                                                          metrics harness)
        v
PHASE 3  similarity engine   -> data/chords/06_similarity/       (per-tune top-K + alignments)
        |
        +--------------------------+
        v                          v
PHASE 4  apps/similarity_explorer  PHASE 5  apps/displayer
         (full data, ratings UI)            (compact top-K, "Suggest similar tunes")
```

Conventions that carry over from the rest of the repo:

* **Numbered data tiers are append-only stages.** `04_verified` is never modified by this
  pipeline; `05_annotated` and `06_similarity` are its outputs. Same philosophy as
  `02_raw` → `03_wip` → `04_verified`.
* **One work unit = one tune file.** Every pipeline script is idempotent and resumable: rerunning
  skips work that is already done and current, so the whole chain can be re-executed cheaply every
  time new tunes land in `04_verified`.
* Apps are static; data is bundled as `.js` files (like `tunes_data.js`), not fetched, so pages
  work from `file://` and GitHub Pages alike.

---

## 3. Phase 0 — Key annotation (`05_annotated`)

### 3.1 Purpose and output contract

Every tune gets a tonality. Output directory `data/chords/05_annotated/` mirrors `04_verified`
one-to-one: each file is a **verbatim copy** of its source with these fields added at the top
level (source fields are never altered):

```json
"key": { "tonic": "F", "mode": "major" },
"key_annotation": {
  "status": "agreed",
  "scorer": { "tonic": "F", "mode": "major", "margin": 0.42 },
  "llm":    { "tonic": "F", "mode": "major", "confidence": "high",
              "modulation_note": null }
},
"harmonic_fingerprint": {
  "family": "32-bar AABA standard",
  "tags": ["ii-V-chains", "turnaround-ending", "dominant-cycle-bridge"],
  "sections": { "A": "I–vi–ii–V loop with a V/V in bar 3",
                "B": "dominant cycle entering on III7" },
  "modulates": false
}
```

* `key.tonic`: pitch letter with accidental as printed in this book's vocabulary (`F`, `Bb`, `Eb`,
  `Db`, `F#`, …). `key.mode`: `"major"` or `"minor"`.
* `key_annotation.status`: `"agreed"` (both voters matched, scorer margin above threshold),
  `"needs_review"` (disagreement or thin margin — tune is **excluded from similarity output**
  until resolved), or `"resolved_manually"` (human picked; record which voter won in a
  `resolution` sub-field).
* Tunes that genuinely modulate get their **predominant/opening key** as `key`, a
  `modulation_note` (free text, e.g. `"moves E ↔ G by section"`), and `"modulates": true` in the
  fingerprint. One key per tune; no per-section keys in v1.

### 3.2 Voter 1 — deterministic functional scorer

Pure Python, no external services. For each tune:

1. Parse every chord symbol with the Phase 1 parser (§4) — root pitch class, quality class.
2. For each of the 24 candidate keys `(tonic pc, mode)`, accumulate a score:
   * **ii–V–I resolutions** into the candidate tonic (the strongest signal; both the
     `ii m7 → V7 → I maj` and minor-key `ii m7b5 → V7 → i m` shapes), weighted by count;
   * **V → I cadences** (without the ii);
   * **duration on the tonic chord** with a mode-compatible quality (fraction of half-bar slots);
   * **final-bar bonus** and smaller **first-bar bonus** — final bar of the *last* section of the
     flattened form, so `A A B A'` endings are used, not the looping `A` ending;
   * **mode match** of the tonic chord's quality class (minor tonic chords vote for minor keys).
3. Winner = highest score. `margin` = (best − runner-up) / best, reported as the scorer's
   confidence. Threshold for "confident": `margin ≥ 0.15` (tune during Phase 0 testing against the
   hand-labeled set, §3.6).

Exact weights are implementation-tunable; what is fixed is the *feature list* above and that the
scorer is deterministic and free to rerun.

Known hard cases the scorer must survive (all present in the current 32 tunes): turnaround endings
(`Au Privave`, `Idaho` end on V7; `Cheryl` ends on ii), a Picardy-third final chord in a minor
tune (`Close Your Eyes` ends on `(F)` but is F minor), and a genuinely modulating tune
(`Con Alma`).

### 3.3 Voter 2 — LLM pass (Claude API)

One API call per tune. Independent of the scorer — the prompt must **not** include the scorer's
answer.

**Request shape** (Python SDK, `anthropic` package — same dependency the digitizer already uses):

* `model="claude-opus-4-8"`.
* `thinking={"type": "adaptive"}` — key-finding on reharmonized charts benefits from reasoning.
* **No `temperature` / `top_p` / `top_k`** — these are rejected with a 400 on Opus 4.7+. (Do not
  copy the conditional `temperature = 0` line from `pipelines/chords/digitizer/vlm.py`; that path
  is for older models.)
* `max_tokens=4000` (output is small; headroom for thinking summary is unnecessary since thinking
  tokens are separate from the schema'd text block, but headroom is cheap).
* **Structured outputs**, not free text and not a forced tool call:
  `output_config={"format": {"type": "json_schema", "schema": KEY_SCHEMA}}` guarantees the reply
  parses. Schema (all objects `additionalProperties: false`, all fields `required`):

```json
{
  "type": "object",
  "properties": {
    "tonic":       { "type": "string", "enum": ["C","Db","D","Eb","E","F","F#","Gb","G","Ab","A","Bb","B","C#","D#","G#","A#"] },
    "mode":        { "type": "string", "enum": ["major", "minor"] },
    "confidence":  { "type": "string", "enum": ["high", "medium", "low"] },
    "modulation_note": { "type": ["string", "null"] },
    "fingerprint": {
      "type": "object",
      "properties": {
        "family":    { "type": "string" },
        "tags":      { "type": "array", "items": { "type": "string" } },
        "sections":  { "type": "object", "additionalProperties": false,
                       "properties": {}, "description": "see note below" },
        "modulates": { "type": "boolean" }
      },
      "required": ["family", "tags", "sections", "modulates"],
      "additionalProperties": false
    }
  },
  "required": ["tonic", "mode", "confidence", "modulation_note", "fingerprint"],
  "additionalProperties": false
}
```

  Note on `fingerprint.sections`: structured-output schemas require
  `additionalProperties: false`, and section names vary per tune — so model `sections` as an
  **array** of `{ "name": string, "summary": string }` objects instead of a keyed object, and
  convert to a keyed object when writing the annotated file. Adjust the schema accordingly.

* **System prompt** (identical for every tune, with `cache_control: {"type": "ephemeral"}` on it
  so consecutive interactive calls hit the prompt cache): the task definition, the key/mode
  conventions, the turnaround/Picardy/modulation caveats, and the **suggested tag vocabulary**
  below with explicit permission to add a new tag when nothing fits. Keeping tags mostly
  controlled is what makes them clusterable later.

  Suggested starting vocabulary (extend during implementation, keep kebab-case):
  `blues-form`, `minor-blues`, `rhythm-changes-a`, `rhythm-changes-bridge`, `ii-V-chains`,
  `dominant-cycle-bridge`, `circle-of-fifths`, `turnaround-ending`, `tonic-pedal`,
  `chromatic-descent`, `modal`, `verse-present`, `montgomery-ward-bridge`, `sears-roebuck-bridge`.

* **User turn**: the tune's JSON — `title`, `composer`, `form`, `time_signature`, and `sections`
  (drop `recordings`, `notation_notes`, `variants` to save input tokens; they don't inform the
  key). Title/composer are deliberately included: recognizing a standard is legitimate evidence,
  and disagreement with the chord-derived scorer is exactly what the review queue is for.

**Run modes:**

* `< 50` pending tunes → interactive `client.messages.create()` calls, sequential, with the retry
  discipline already established in `vlm.py` (retry `RateLimitError`, `APIConnectionError`,
  `InternalServerError` with backoff; treat other `APIStatusError` as fatal for that tune and
  record it).
* `≥ 50` pending tunes → **Batches API** (`client.messages.batches.create`, poll
  `processing_status` until `"ended"`, collect by `custom_id` = tune file stem). 50% of standard
  price, completes well within 24 h, perfect for the offline full-corpus run.
* Either way, handle `stop_reason == "refusal"` and schema-violating replies by flagging the tune
  `needs_review` with the error recorded — never crash the batch.

**Cost budget** (Opus 4.8, $5/$25 per MTok): ~2.5K input + ~250 output tokens per tune →
≈ $0.02/tune interactive, ≈ $0.01/tune batched. Full 2500-tune corpus ≈ **$25–50 one-time**; the
32 current tunes are cents. The fingerprint accounts for only ~100–250 *output* tokens of that —
a few dollars across the whole corpus — because the expensive input is already being paid for the
key. This is why the fingerprint rides along in the same call rather than being a separate pass.

### 3.4 Harmonic fingerprint — role and boundaries

The fingerprint is **LLM judgment, not ground truth**. Its committed uses:

1. **UI labels** — `family` as a filter/badge in both apps; `sections` lines as the human-readable
   "why" next to a section match in the explorer.
2. **Adjudication context** — shown in the review queue; it usually makes disagreements obvious.
3. **Candidate/feature channel for Phase 3 — only after evaluation.** Tag-overlap and
   family-equality may be added to retrieval or scoring *if* they improve metrics on the Phase 2
   evaluation set. They must not silently influence scores before that.

### 3.5 Adjudication workflow

```
python pipelines/chords/annotate_keys.py            # annotate everything pending
python pipelines/chords/annotate_keys.py --review   # interactive disagreement queue
```

* **Agreement** (same tonic+mode, scorer margin ≥ threshold) → `status: "agreed"`, written
  directly.
* **Disagreement or thin margin** → file is still written (so the copy exists) with
  `status: "needs_review"`. `--review` iterates these: print title, both votes with their
  confidences, the fingerprint, and the first/last bars; accept scorer / accept LLM / type a key /
  skip. Resolution writes `status: "resolved_manually"`.
* Idempotence: a tune is skipped when its `05_annotated` file exists, embeds a `source_sha256` of
  the `04_verified` file it was built from, and that hash still matches. A changed verified file
  re-triggers annotation. Store `source_sha256` inside `key_annotation`.

### 3.6 Phase 0 acceptance

* Hand-label the current 32 tunes' keys once (project owner; minutes) into
  `data/chords/eval/key_groundtruth.json` → `{ "<stem>": {"tonic": ..., "mode": ...}, ... }`.
* Acceptance: pipeline output matches ground truth on all 32 after review; **scorer alone**
  achieves ≥ 80% on them (it must be a real voter, not a rubber stamp); `Con Alma` carries a
  modulation note; unit tests cover the scorer on at least the hard cases named in §3.2.
* This file stays as a regression set whenever scorer weights change.

---

## 4. Phase 1 — Normalization library

A small importable package, `pipelines/chords/similarity/` (`normalize.py` + tests). No I/O
side effects — pure functions from an annotated tune dict to sequences. Everything downstream
(scorer refinement, engine, client-side transposition logic) mirrors these rules.

### 4.1 Chord parser

`parse_chord(symbol) -> Chord(root_pc, quality, extensions, bass_pc | None, parenthesized: bool)`

* Accept exactly the vocabulary enforced by `pipelines/chords/tools/check_chord_syntax.py` — that
  file is the authoritative grammar; do not invent a new one. Includes parenthesized chords
  (`(F)`, `C(b9)`), altered dominants, slash basses if present in the vocabulary.
* **Quality reduction** to matching classes:

| Class | Symbols (examples) |
|---|---|
| `maj` | `F`, `Fmaj7`, `F6`, `F69`, `Fmaj9` |
| `min` | `Fm`, `Fm7`, `Fm6`, `Fm9`, `FmMaj7` |
| `dom` | `F7`, `F9`, `F13`, `F7b5`, `F7#5`, `F7b9`, `F(b9)`, altered |
| `m7b5` | `Fm7b5` |
| `dim` | `Fdim`, `Fdim7`, `F°` |
| `aug` | `F+`, `Faug` (non-dominant #5) |
| `sus` | `Fsus4`, `F7sus` |

  The full symbol is retained alongside the class — matching uses the class, display uses the
  original.

### 4.2 Grid expansion and form flattening

* Each bar expands to a **fixed 2 slots per bar** (beats 1 and 3 in 4/4; in 3/4, slot 2 repeats
  slot 1 unless a mid-bar chord exists). A bar with 4 chords keeps the beat-1 and beat-3 chords
  for the matching grid; the full beat map is retained for display. A bar with one chord repeats
  it. Empty `beats` continuation bars repeat the previous chord.
* `variants` are ignored for matching (main text only). Parenthesized chords participate normally
  (the parens flag is kept for display).
* **Form flattening**: concatenate sections in the order they appear in the `sections` dict
  (JSON document order — this matches the printed form; `A A B A'` is stored as
  `A, A1, B, A2`). Cross-check the section count against the `form` string and emit a warning on
  mismatch; an early Phase 1 task is a one-shot validation pass of every `form` string in the
  corpus.

### 4.3 Tonic-relative transposition

Using Phase 0's `key`:

* Reference pitch class = the tonic for major tunes; the **relative major's tonic** for minor
  tunes (i.e. majors read as if in C, minors as if in A minor — one shared pitch space, per the
  locked decision).
* Token = `(degree, quality_class)` where `degree = (root_pc − reference_pc) mod 12`.
* Each tune yields: `full_seq` (flattened form) and `section_seqs` (one per section, keyed by
  section name), plus metadata: mode, meter, form string, bar count.

### 4.4 Phase 1 acceptance

* Parser round-trips every chord symbol in the current corpus without error (run against all
  `04_verified` files).
* **Contrafact test**: `Au Privave` (F blues) and `Cheryl` (C blues) normalize to sequences with
  ≥ 90% identical tokens. This test is the canary for the whole normalization stack.
* Every `form` string in the corpus parses or is explicitly warned about.

---

## 5. Phase 2 — Evaluation set and metrics harness

Built **before** the engine, and option-agnostic: every similarity method (n-grams, alignment,
tags, optional embeddings) is measured against the same data. This phase is deliberately early —
without it, engine quality claims are vibes.

### 5.1 Ground truth

`data/chords/eval/similarity_groundtruth.json`:

```json
{
  "families": [
    { "name": "blues-in-major", "level": "tune",
      "members": ["23_04_AU_PRIVAVE", "72_03_CHERYL"] },
    { "name": "example-shared-bridge", "level": "section",
      "members": [ {"tune": "…", "section": "B"}, {"tune": "…", "section": "A"} ] }
  ],
  "non_matches": [ ["114_01_EASY_LIVING", "23_04_AU_PRIVAVE"] ]
}
```

Seeded by hand from known contrafact families present in the book (blues heads, rhythm-changes
tunes, shared bridges); grows over time from explorer ratings (§6). A few explicit `non_matches`
guard against degenerate everything-matches configurations.

### 5.2 Ratings ingestion

The explorer (Phase 4) exports rating files (`good` / `bad` judgments on suggested pairs) as JSON
downloads; they are committed under `data/chords/eval/ratings/` and merged by the harness:
`good` ratings extend families (or add pairs), `bad` ratings extend `non_matches`.

### 5.3 Metrics harness

`pipelines/chords/similarity/evaluate.py` — given a similarity output directory, report:

* **family-recall@k** (k = 5, 10): fraction of ground-truth family co-members that appear in each
  member's top-k (tune level and section level separately);
* **precision@10** against rated pairs (where ratings exist);
* **non-match violations**: any `non_matches` pair scoring above a threshold;
* per-change delta report so engine tweaks show their effect in one command.

**Every scoring-relevant change to Phase 3 must be accompanied by a harness run in the PR/commit
message.**

---

## 6. Phase 3 — Similarity engine (`06_similarity`)

`pipelines/chords/similarity/compute.py`, one CLI entry point:

```
python -m pipelines.chords.similarity.compute            # full rebuild
python -m pipelines.chords.similarity.compute --eval     # rebuild + run harness
```

Input: all `05_annotated` tunes with `status` ∈ {`agreed`, `resolved_manually`}
(`needs_review` tunes are excluded). Output: `data/chords/06_similarity/`.

### 6.1 Stage A — exact-hash contrafact groups

Hash each normalized `full_seq` and `section_seq`. Identical hashes → score 1.0 families,
reported directly. Cheap sanity layer; also a good self-test (Au Privave/Cheryl land here or very
close).

### 6.2 Stage B — retrieval (n-gram cosine)

* Shingle each sequence into overlapping token n-grams, n ∈ {2, 3, 4}.
* TF-IDF weight over the corpus; L2-normalized sparse vectors; cosine via sparse matrix product
  (`scipy.sparse` / `sklearn` acceptable dependencies; keep it optional-import-guarded like other
  repo tooling if desired).
* Two indexes: tunes (~2500 vectors) and sections (~10 000 vectors, any↔any so one flat index).
* Keep **top 100 candidates** per query (tune→tunes, section→sections). Runtime target: seconds.

### 6.3 Stage C — alignment scoring

For each (query, candidate) pair from retrieval:

* **Local alignment (Smith–Waterman)** over token sequences with a music-aware substitution cost:

| Substitution | Cost intuition |
|---|---|
| identical `(degree, quality)` | 0 |
| same degree, related quality (`maj`↔`dom` at I in blues, `maj` variants) | small |
| **tritone sub** (dom chords 6 semitones apart) | small |
| relative major/minor chord substitution (e.g. degree 0 `maj` ↔ degree 9 `min`) | small |
| same quality, unrelated degree | large |
| everything else | large |
| gap (insertion/deletion of a half-bar slot) | medium; affine (open > extend) |

* Normalize the raw alignment score by the query's self-alignment → **score ∈ [0, 1]**, which is
  the user-visible spectrum value.
* Apply multiplicative penalties: meter mismatch (small), mode mismatch (small — mode is already
  implicitly penalized by the shared pitch space, this is a nudge, not a wall).
* Keep the **traceback path** — the slot-to-slot mapping is what the UIs highlight. Store it as a
  compact list of `[query_bar, candidate_bar]` pairs (bar granularity is enough for display).
* Whole-tune score for the displayer's ranked list = alignment score of full sequences; section
  matches are reported separately with their own scores.

**Performance budget: full rebuild ≤ 15 minutes on the laptop at 2500 tunes.** Order-of-magnitude:
~250k tune-pair alignments of ~128-slot sequences plus ~1M section-pair alignments of ~16-slot
sequences. Pure-Python DP will likely miss the budget — vectorize the DP inner loop with numpy or
use numba; either is acceptable. If needed, cut retrieval to top-50 candidates before optimizing
further.

### 6.4 Output format

```
data/chords/06_similarity/
  index.json                 # build metadata: date, corpus size, engine version, harness metrics
  tunes/<stem>.json          # full data for the explorer
  displayer_similar.json     # compact bundle input for build_data.py
```

`tunes/<stem>.json` (explorer): top-20 similar tunes and top-20 section matches, each with score,
score components (retrieval cosine, alignment score, penalties applied), alignment bar-mapping,
and the candidate's fingerprint `family`.

`displayer_similar.json` (displayer): per tune, top-10 whole-tune suggestions and top-5 section
matches — score, matched-section labels, bar-mapping only. No component breakdowns. Size guard:
this bundle must stay < ~2 MB at 2500 tunes (it ships to GitHub Pages).

### 6.5 Fingerprint channel (conditional)

After the baseline (A+B+C) is measured: experiment with tag-overlap / family-equality as (a) a
retrieval booster, (b) a small score bonus. Adopt only on a harness improvement; record the
comparison in the commit.

### 6.6 Optional experiment — learned embeddings

Scoped strictly: embed each tune's roman-numeral text (off-the-shelf embedding model) or train a
small chord2vec on the corpus; use as an alternative **retrieval** layer feeding the same Stage C;
compare family-recall@k against Stage B on the harness. Adopt only if it clearly wins. This is
explicitly *optional* and last — do not start here, and do not let it replace Stage C's alignment
(the UIs need the bar mapping regardless).

---

## 7. Phase 4 — Similarity explorer (debug app)

`apps/similarity_explorer/` — static page in the displayer's mold (plain HTML/JS/CSS, data bundled
as `.js` files so it opens from `file://`; a tiny `build_data.py`-style bundler script converts
`06_similarity/tunes/*.json` + the normalized grids into `explorer_data.js`).

Features (all v1):

* **Tune picker** (search by title) → ranked neighbor list with score, score breakdown, and
  fingerprint family badge; toggle between whole-tune and section-match views.
* **Side-by-side grids** for a selected pair: normalized roman-numeral view with the aligned bars
  highlighted via the stored bar-mapping; section-match view highlights where in each tune the
  matched section sits; fingerprint `sections` lines shown as captions.
* **Rating buttons** (`good match` / `bad match`) per suggested pair → persisted in
  `localStorage`, exportable as a JSON download whose format matches §5.2 for committing under
  `data/chords/eval/ratings/`.
* Filters: min score, same-family only, exclude same tune (for section matches within one tune).

Not required: melody display, original-key rendering, playlist features — this is a quality tool,
keep it lean.

**Verification:** drive it with the repo-venv Playwright setup (`channel="msedge"`), same as the
displayer is verified.

---

## 8. Phase 5 — Displayer integration

Changes to `apps/displayer/`:

1. **`build_data.py`**: switch tune-JSON input to `data/chords/05_annotated/` (falls back to the
   `04_verified` file when a tune has no annotation yet — key-dependent features are then disabled
   for that tune) and bundle `data/chords/06_similarity/displayer_similar.json` into a new
   `similar_data.js`. Only digitized tunes appear as suggestions by construction.
2. **"Suggest similar tunes" button** on the tune view → panel listing top-10 suggestions with
   score (rendered as a 0–100 match value), fingerprint family, and section-match chips
   ("bridge ≈ A of …").
3. **Comparison view**: selecting a suggestion shows the current tune and the suggestion side by
   side (stacked on narrow screens), aligned bars highlighted from the bar-mapping, with a
   **three-way display switch**: *original keys* / *transposed* (suggestion rendered in the current
   tune's key) / *roman numerals*. Transposition and degree rendering are implemented client-side
   in `chords.js`, mirroring the Phase 1 parser/transposition rules exactly (port the reduction
   and transposition tables, plus a shared test fixture: a JSON file of `symbol → transposed
   symbol → degree` triples generated by the Python library and asserted in a JS test, so the two
   implementations cannot drift silently).
4. Playlist and existing features untouched.

**Verification:** Playwright (`channel="msedge"`) flows: open a tune → suggest → pick a suggestion
→ toggle all three display modes → highlighted bars present.

---

## 9. Phase order, dependencies, acceptance summary

| Phase | Deliverable | Depends on | Acceptance |
|---|---|---|---|
| 0 | `05_annotated` + `annotate_keys.py` | — | §3.6: 32/32 keys correct after review; scorer ≥ 80% alone; regression set committed |
| 1 | `similarity/normalize.py` | 0 | §4.4: parser covers corpus; Au Privave ≈ Cheryl; forms validated |
| 2 | eval set + `evaluate.py` | 1 (for section identities) | Harness runs end-to-end on seeded families |
| 3 | `06_similarity` + `compute.py` | 1, 2 | Rebuild ≤ 15 min at target scale (extrapolated); harness metrics reported; Au Privave/Cheryl mutual top-3 |
| 4 | `apps/similarity_explorer/` | 3 | Playwright-verified; rating export round-trips into the harness |
| 5 | Displayer integration | 3 (4 recommended first) | Playwright-verified; bundle size guard holds; JS/Python transposition fixture passes |

Phases 0–2 are useful standalone (keys in the data, a clean chord parser, an eval harness) even
before any similarity ships. Build in order; do not start Phase 3 tuning before Phase 2 exists.

---

## 10. Risks and mitigations

* **Wrong keys poison everything downstream** → dual-voter design, review gate, `needs_review`
  exclusion from similarity, regression set.
* **Chord vocabulary drift** → single grammar source (`check_chord_syntax.py`); parser failure on
  any symbol is a hard error listing the offending file, never a silent skip.
* **JS/Python normalization drift** (Phase 5 transposition) → shared generated test fixture, §8.3.
* **Everything-matches degeneracy** (substitution costs too generous) → `non_matches` in the eval
  set; harness violation count must be zero.
* **Bundle bloat on GitHub Pages** → compact displayer format with hard size guard in
  `build_data.py` (fail the build, don't truncate silently).
* **Corpus 80× growth** → performance budget stated per stage; retrieval is vectorized from day
  one; alignment only runs on retrieval candidates.
