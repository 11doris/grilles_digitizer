# Tune Similarity — Implementation Spec

**Goal:** For every digitized tune, suggest other tunes that are harmonically similar — for the
whole form or for a single section — so that a player who has learned one tune discovers what else
is within reach. Two UIs consume the result: a standalone **similarity explorer** (debug/quality
tool) and a **"Suggest similar tunes"** feature in the existing displayer app.
**Scale:** must work unchanged at **1500–2500 tunes** (currently ~95 digitized; the corpus grows
continuously as tunes are verified).
**Audience:** an implementing agent working in this repository.

This document is the single source of truth for this feature. Where it conflicts with earlier
discussion notes, this document wins.

> **Phase C (2026-07-15, strains model)** — supersedes this spec wherever they
> disagree (see `strain_model_phase_c_plan.md`): verified/annotated tunes store
> an ordered `strains` list instead of a `sections` map. Everything here that
> reads "section" operates on the **parts** of that list via
> `normalize.sections_view` (an ordered `{part_id: bars}` view; ids generated
> from the structure — chorus `A`/`A1`, prefixed `verse_A`, bare aux `coda` —
> never parsed). Verse exclusion keys off the explicit `role == "verse"`;
> `section_keys` and the engine's `section` refs use the generated part ids;
> the `form`↔`sections` cross-check (HARD/SOFT warnings) is replaced by
> `normalize.validate_strains`, loud at edit time.

**Status (2026-07-10):**

* **Phase 0 is complete and owner-verified** (2026-07-09): all tunes in `05_annotated`, scorer
  91% standalone on the hand-labeled set, 19 of ~95 tunes carry human-verified `section_keys`.
* **Phase 1 is mostly implemented already**: §4.1 (parser) and §4.2 (grid expansion/flattening)
  live in `pipelines/chords/similarity/normalize.py` + tests. Only §4.3 (tonic-relative
  sequences, a thin layer over the existing code) remains.
* **This revision simplifies Phase 1**: the former §4.3a delta-encoding channel is dropped from
  v1 (rationale in §4.3); §5–§10 are updated to match. Handling of modulating sections now rests
  entirely on the human-verified `section_keys` annotations, which turned out to have real
  corpus coverage (~20% of tunes), not to be a Con-Alma-only edge case.

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
        |                        ^                                 key_annotation, fingerprint)
        |                        | verify / correct
        |                  apps/key_verifier  (local Flask app: crop PNG + key + fingerprint)
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
* **Numbered = regenerable pipeline stage; un-numbered = curated data that must be preserved.**
  `data/chords/06_similarity/` may be deleted and rebuilt from `05_annotated` at any time —
  it is therefore gitignored (as is the explorer's `explorer_data.js` bundle); only the compact
  `apps/displayer/data/similar_data.js` the deployed app needs is committed.
  `data/chords/eval/` is deliberately *not* numbered: it holds human judgment (confirmations,
  ratings, hand-labeled keys) that no pipeline can regenerate — like `data/title_index.csv`, it
  sits beside the tiers, not in them. Never treat it as a build output.
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
"section_keys": { "B": { "tonic": "A", "mode": "major" } },
"opening": { "degree": "ii", "quality": "min", "chord": "Gm7" },
"key_annotation": {
  "status": "agreed",
  "scorer": { "tonic": "F", "mode": "major", "margin": 0.42,
              "section_keys": { "B": { "tonic": "A", "mode": "major", "margin": 0.31 } } },
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
* `key_annotation.status` — three values:
  * `"agreed"`: both voters matched and the scorer margin cleared its threshold (machine-set);
  * `"needs_review"`: disagreement or thin margin (machine-set) — tune is **excluded from
    similarity output** until a human resolves it;
  * `"verified"`: a human confirmed or corrected the annotation in the key verifier app (§3.6)
    or via `--set-key`. Terminal for a given source file. A correction keeps both voter votes
    untouched for the record and adds a `human` sub-field
    (`{ "tonic": ..., "mode": ..., "corrected": true|false }`).
* **`section_keys` — local keys for modulating sections.** Present only for sections whose local
  key clearly differs from the global `key`; a tune with no entry modulates nowhere. This is what
  lets a modulated section be compared against tunes that simply *live* in a I-centered
  progression (see §4.3, §6.2): without it, a modulated stretch — encoded relative to the global
  tonic — could only ever match tunes that modulate by the same interval. Adjudicated like the
  tune key:
  scorer (per-section pass, §3.2) and LLM (`local_key` in the fingerprint sections, §3.3) must
  agree, otherwise the tune goes to `needs_review` with the section named.
* **`opening` — what the tune starts on.** The first printed chord of the *form proper* — verse,
  intro and other aux strains are skipped (2026-07-15 owner rule), so the field names what the
  chorus (or the first main strain of a multi-strain tune) starts on — expressed relative to the
  resolved `key`: `degree` is
  a roman numeral relative to the tune's own tonic (uppercase for `maj`/`dom`/`aug` quality
  classes, lowercase for `min`/`m7b5`/`dim`; accidental prefix for non-diatonic roots, e.g.
  `bIII`, and a minor tune starting on its tonic reads `i`), `quality` is the §4.1 quality class,
  `chord` is the original printed symbol. This field is **purely computed** — derived from
  `key` + the parsed first chord, no voter and no LLM involvement — and is recomputed whenever
  the key changes (e.g. after a `--review` resolution). It exists as player-facing metadata: a
  displayer filter ("show tunes that start on the V", §8), not a similarity feature.
* Tunes that genuinely modulate get their **predominant/opening key** as `key`, the modulated
  sections listed in `section_keys`, a `modulation_note` (free text, e.g. `"bridge in A"`), and
  `"modulates": true` in the fingerprint. Modulations *within* a section are not keyed in v1 —
  an accepted limitation (§10); §4.3 records the extension options if the eval set ever shows
  real misses from it.

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
   hand-labeled set, §3.7).

Exact weights are implementation-tunable; what is fixed is the *feature list* above and that the
scorer is deterministic and free to rerun.

**Per-section pass (local keys).** After the tune key is scored, rerun the same 24-key scoring on
each section's bars alone. Record a `section_keys` entry only when a different key beats the
global key on that section **decisively** (its margin over the global key's section score exceeds
a dedicated threshold, stricter than the tune-level one — 8 bars are noisy, so bias strongly
toward "no modulation"). The final-bar/first-bar bonuses apply to the section's own boundaries in
this pass.

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
* `max_tokens=16000` (output is small; headroom for thinking summary is unnecessary since thinking
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
  **array** of `{ "name": string, "summary": string, "local_key": {tonic, mode} | null }`
  objects instead of a keyed object, and convert to a keyed object when writing the annotated
  file. `local_key` is the LLM's vote on §3.1's `section_keys`: null when the section sits in the
  global key, filled in when the section is genuinely in another key (the prompt must instruct
  that passing ii–Vs and short tonicizations do **not** count — only a sustained local tonal
  center). Adjust the schema accordingly.

* **System prompt** (identical for every tune, with `cache_control: {"type": "ephemeral"}` on it
  so consecutive interactive calls hit the prompt cache): the task definition, the key/mode
  conventions, the turnaround/Picardy/modulation caveats, and the **suggested tag vocabulary**
  below with explicit permission to add a new tag when nothing fits. Keeping tags mostly
  controlled is what makes them clusterable later. **Tags and `family` must be key-agnostic** —
  they describe function and form (`dominant-cycle-bridge`), never absolute pitches
  (`bridge-in-A` is forbidden; that information lives in `section_keys`/`modulation_note`). This
  keeps the fingerprint's machine-usable fields valid across keys and comparable between tunes;
  only the free-text section summaries and `modulation_note` may name keys or roman numerals.

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
* **Durability:** every annotation is written to `05_annotated` the moment its vote arrives
  (a crash loses at most one in-flight call), and a batch run persists its batch id to
  `data/chords/key_annotation_batch.json` until the results are fetched, so an interrupted
  poll (sleep, network, Ctrl-C) is recovered with `annotate_keys.py --resume-batch` — a paid
  batch can never be orphaned.

**Cost budget** (Opus 4.8, $5/$25 per MTok): ~2.5K input + ~250 output tokens per tune →
≈ $0.02/tune interactive, ≈ $0.01/tune batched. Full 2500-tune corpus ≈ **$25–50 one-time**; the
32 current tunes are cents. *Implementation note:* the shipped voter runs **adaptive thinking**
(`MAX_TOKENS = 16000`) and thinking tokens bill as output, so real per-tune output can exceed the
~250-token JSON several-fold; check `usage` on the first large batch and expect the corpus total
toward the upper end of (or above) this range — still halved by the Batches API. The fingerprint accounts for only ~100–250 *output* tokens of that —
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

### 3.5 Adjudication, verification and correction

```
python pipelines/chords/annotate_keys.py                      # annotate everything pending
python pipelines/chords/annotate_keys.py --set-key <stem> <tonic> <major|minor>   # scripted override
python apps/key_verifier/key_verify_app.py                    # human verification UI (§3.6)
```

* **Agreement** (same tonic+mode, scorer margin ≥ threshold, **and** matching `section_keys` —
  same set of modulating sections with the same local keys) → `status: "agreed"`, written
  directly.
* **Disagreement or thin margin** (on the tune key *or* on any section key) → file is still
  written (so the copy exists) with `status: "needs_review"`.
* **Human verification** happens in the key verifier app (§3.6), which is the primary review
  surface — it walks all tunes (the `needs_review` queue first) and writes `status: "verified"`.
* **Corrections go through code paths, never hand-edits.** `05_annotated` files must not be
  edited by hand: a key change ripples into derived fields (`opening`, `section_keys`
  consistency) and hand edits silently skip that. Both correction surfaces — the app's save and
  the `--set-key` CLI — call **one shared update routine** in the annotation module that applies
  the new key, recomputes every derived field, preserves the original voter votes, and sets
  `status: "verified"` with `human.corrected: true`. *(Implemented: `update_annotation` in
  `key_annotation/core.py` recomputes `opening` and normalizes `section_keys` on every save.)*
* **A key correction must not leave stale analysis behind.** Two derived artifacts depend on the
  key beyond `opening`:
  * **`section_keys` re-detection.** Cleaning is not enough (an entry equal to the new key is
    dropped, but a section that *now* differs from the new key goes undetected). On a key
    change, the update routine re-runs the deterministic scorer's per-section pass (§3.2) under
    the corrected key and surfaces newly detected local keys as **proposals in the verifier app**
    for the human to accept or dismiss in the same session — never silently written into a
    `verified` file.
  * **`harmonic_fingerprint` refresh.** The fingerprint's free-text parts (section summaries in
    roman numerals, `modulation_note`, `modulates`) were written by the LLM under *its* key
    assumption and become wrong when a human corrects the key. The update routine sets
    `harmonic_fingerprint.stale: true` whenever the key or section keys actually changed (unless
    the human edited the fingerprint in the same save). The next `annotate_keys.py` run re-runs
    the **LLM pass in key-pinned mode** for stale fingerprints: same prompt machinery as §3.3,
    but the verified key (and `section_keys`) are stated as ground truth in the user turn and
    the schema drops the key-voting fields — the call returns only a fresh fingerprint. It never
    touches `key`, `opening`, or `status`. Corrections are rare and the call costs ~$0.02, so
    this stays off the app's hot path and the verifier needs no API access.
  Deliberately **not** chosen: making the whole fingerprint key-agnostic. Roman-numeral section
  summaries are precisely what makes the fingerprint useful for display and adjudication;
  neutering them to avoid staleness would trade permanent vagueness for a rare, cheap refresh.
  The similarity *engine* is unaffected either way — it consumes `key`/`section_keys` through
  normalization (§4.3) and, at most, the already key-agnostic `tags`/`family` (§6.5); it never
  reads the fingerprint prose. A stale fingerprint can mislabel a UI caption, not poison a
  score.
* **Idempotence:** a tune is skipped when its `05_annotated` file exists, embeds a
  `source_sha256` of the `04_verified` file it was built from, and that hash still matches —
  `verified` annotations therefore survive pipeline reruns. A changed source file re-triggers
  annotation and **demotes the tune back to the machine statuses** (the chart itself changed, so
  a previous human verification no longer applies); the app's queue picks it up again.

### 3.6 Key verifier app (`apps/key_verifier/`)

A local Flask app mirroring the existing chord verifier (`apps/verifier/verify_app.py`):
same structure (`key_verify_app.py` + `templates/` + `static/`), launched locally, browser UI,
keyboard-driven. Unlike the chord verifier it needs no WIP tier — `05_annotated` is this
pipeline's own output, so saves write there directly (through the shared update routine, §3.5).

```
python apps/key_verifier/key_verify_app.py \
    [--annotated data/chords/05_annotated] [--crops data/chords/01_crops] [--port 5001]
```

**Layout.** Left: the tune's **original crop PNG** from `data/chords/01_crops/<stem>.png` — the
human reads the key from the actual chart, not from anyone's transcription of it. Right, a
verification panel:

* resolved `key` (tonic + mode, prominent) and the `opening` badge;
* `section_keys`, when present;
* both voter votes with their confidences/margins, disagreement highlighted;
* the full fingerprint: `family`, `tags`, per-section summaries, `modulation_note`, `modulates`;
* `status` and position in the queue ("214 verified / 12 needs review / 2274 remaining").

**Actions** (all keyboard-reachable, like the chord verifier):

* **Verify** — accept everything as shown → `status: "verified"`;
* **Correct key** — tonic picker + major/minor toggle, then verify; saved via the shared update
  routine so `opening` and `section_keys` are recomputed/revalidated;
* **Edit section keys** — add, change, or remove a local-key entry per section;
* **Edit fingerprint** — family (text/dropdown of seen values), tags (chip editor), modulation
  note, `modulates` flag; section summaries editable as plain text;
* **Skip / next / previous**; filter tabs: *needs review* / *unverified* / *all* (default order:
  `needs_review` first, then unverified `agreed` tunes).

Progress is derivable from the files themselves (`status` fields), so the app keeps no separate
state file.

**Verification of the app itself:** Playwright with the repo venv (`channel="msedge"`), like the
other apps — flows: load a tune → correct its key → saved file contains the new key, recomputed
`opening`, `status: "verified"`, and untouched voter votes.

### 3.7 Phase 0 acceptance

**Status: met (2026-07-09)** — all tunes verified through the app, scorer 91% standalone
against the ground truth. Outstanding from this revision: the §3.5 staleness handling
(fingerprint `stale` flag + key-pinned refresh, per-section rescan proposals) and the §3.3
key-agnostic-tags prompt rule.

* Hand-label the current 32 tunes' keys once (project owner; minutes) into
  `data/chords/eval/key_groundtruth.json` → `{ "<stem>": {"tonic": ..., "mode": ...}, ... }`.
* Acceptance: all 32 tunes carry `status: "verified"` after a pass through the key verifier app
  and match the ground truth; **scorer alone**
  achieves ≥ 80% on them (it must be a real voter, not a rubber stamp); `Con Alma` carries a
  modulation note and at least one `section_keys` entry; non-modulating tunes have **no**
  `section_keys` entries (false-positive local keys are the failure mode to test against); unit
  tests cover the scorer on at least the hard cases named in §3.2.
* `opening` unit tests against known cases from the current corpus, e.g. Heart and Soul (F major,
  starts `F`) → `I`; I'll Never Smile Again (Eb major, starts `Fm7`) → `ii`; How Long Has This
  Been Going On (G major, starts `D7(13)`) → `V`; Close Your Eyes (F minor, starts `Gm7b5`) →
  `ii`.
* This file stays as a regression set whenever scorer weights change.

---

## 4. Phase 1 — Normalization library

A small importable package, `pipelines/chords/similarity/` (`normalize.py` + tests). No I/O
side effects — pure functions from an annotated tune dict to sequences. Everything downstream
(scorer refinement, engine, client-side transposition logic) mirrors these rules.

**Status: §4.1 and §4.2 are already implemented** (`normalize.py` + `test_normalize.py` —
parser, quality reduction, 2-slot grid expansion, form flattening, degree naming, `opening`),
built as part of Phase 0. **The only remaining work is §4.3**, a thin function over the
existing code. This revision also deletes the former §4.3a (dual indexing + delta encoding);
the rationale is at the end of §4.3.

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

### 4.3 Tonic-relative sequences (the remaining work)

One function from an annotated tune dict to token sequences, using Phase 0's `key` and
`section_keys`:

* Reference pitch class = the tonic for major tunes; the **relative major's tonic** for minor
  tunes (i.e. majors read as if in C, minors as if in A minor — one shared pitch space, per the
  locked decision).
* Token = `(degree, quality_class)` where `degree = (root_pc − reference_pc) mod 12`.
* Each tune yields exactly two things, plus metadata (mode, meter, form string, bar count):
  * **`full_seq`** — the flattened form, every degree relative to the tune's **global** key.
    This preserves the tune's overall shape, modulations included: a modulating tune looking
    less similar to non-modulating tunes at the whole-tune level is musically correct.
  * **`section_seqs`** — **one sequence per section**, keyed by section name. A section with a
    Phase 0 `section_keys` entry has its degrees computed against its **local** key and carries
    a `local_key` marker (so the UIs can label the match "bridge, locally in A"); every other
    section's sequence is simply the corresponding slice of `full_seq`. This is what lets a
    bridge that sits in the IV match another tune's I-centered section directly, in one shared
    index, with no special casing downstream.

**Dropped from v1 — the former §4.3a delta-encoding channel.** The earlier draft gave every
section *three* sequence variants (`global_seq`, `local_seq`, `delta_seq`) plus a tune-level
`full_delta_seq`, a second transposition-invariant retrieval index, and shift-aware alignment
with its own penalty (§6.2/§6.3). All of that machinery served exactly one gap: modulations
*inside* a section, which Phase 0 does not key. That trade is no longer worth it —
`section_keys` turned out to be a real, human-verified channel with ~20% corpus coverage (19 of
95 tunes), not a Con-Alma-only special case, so annotation is the modulation-handling mechanism
of record. Intra-section modulations become an accepted, documented limitation (§10). If the
Phase 2 eval set ever demonstrates real misses from it, the recorded extension options are:
(a) reinstate the delta retrieval channel as originally drafted, or (b) extend Phase 0 to
sub-section local keys. Both slot in without changing the v1 interfaces — which is exactly why
deferring them is safe.

### 4.4 Phase 1 acceptance

* Parser round-trips every chord symbol in the current corpus without error (run against all
  `04_verified` files). *(Already covered by `test_normalize.py`; keep as a regression test.)*
* **Contrafact test**: `Au Privave` (F blues) and `Cheryl` (C blues) normalize to sequences with
  ≥ 90% identical tokens. This test is the canary for the whole normalization stack.
* A section with a `section_keys` entry yields a local-relative sequence with the `local_key`
  marker; a tune without `section_keys` yields section sequences that are exact slices of
  `full_seq`. Test against one of the 19 annotated tunes.
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

**The ground truth is bootstrapped, not hand-authored.** The project owner is not expected to
compile contrafact families from musical knowledge; their manual part is reduced to yes/no
confirmation of machine-proposed pairs. Every entry carries a status: `"candidate"` (proposed,
unconfirmed) or `"confirmed"` (visually verified by a human).

`pipelines/chords/similarity/evaluate.py --seed-eval` generates candidates from three sources:

1. **Fingerprint groupings** (needs Phase 0): tunes sharing a `harmonic_fingerprint.family` or a
   distinctive tag (`rhythm-changes-a`, `blues-form`, …) form candidate families. Free byproduct
   of annotation.
2. **Exact-hash groups** (needs Phase 1): tunes/sections whose normalized sequences hash
   identically are contrafacts **by construction** — these enter as `confirmed` directly (they
   need no judgment, though they exercise normalization and recall rather than the subtle end of
   the scoring spectrum).
3. **Title-index LLM call**: one Claude request (`claude-opus-4-8`, structured outputs, same
   conventions as §3.3) over the titles in `data/title_index.csv`, asking which titles are known
   contrafacts of each other or share a progression family, and proposing a handful of
   obviously-unrelated pairs as `non_matches` candidates. Costs cents; runs once.

Candidates from sources 1 and 3 are **LLM judgment and must not be confirmed by an LLM** — a
human confirms them in the explorer's confirmation mode (§7), which promotes to `confirmed` or
deletes. Until Phase 4 exists, the harness may be run against the candidate set: with statuses
reported separately, candidate-set metrics are a legitimate *relative* signal for comparing
engine variants, while absolute quality claims require the confirmed set. Phase 3 acceptance is
re-checked once confirmations exist.

The set grows over time from explorer ratings (§5.2/§7). A few explicit `non_matches` guard
against degenerate everything-matches configurations. The confirmed seed **must include at least
one modulating-section family** — a pair where one member's section is in a different local key
than the other's (e.g. a bridge in the IV matching another tune's I-centered A section) — so the
harness actually measures the §4.3 local-key machinery rather than only in-key matching; if no natural one
surfaces from the generators, the title-index LLM call is explicitly asked for modulating-bridge
examples present in the book.

Even a small confirmed set (~10 families) is sufficient to start: family-recall@k comparisons
between engine variants are meaningful long before precision numbers are.

### 5.2 Ratings ingestion

The explorer (Phase 4) exports rating files (`good` / `bad` judgments on suggested pairs) as JSON
downloads; they are committed under `data/chords/eval/ratings/` and merged by the harness:
`good` ratings extend families (or add pairs), `bad` ratings extend `non_matches`. Ratings on
pairs that exist as `candidate` entries promote them to `confirmed` (or delete them) — rating and
seed confirmation are the same gesture in the explorer.

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

Input: all `05_annotated` tunes with `status` ∈ {`agreed`, `verified`}
(`needs_review` tunes are excluded). Output: `data/chords/06_similarity/`.

Verse sections (section names starting `verse`) never enter comparisons, at either level:
the tune-level sequence is the verse-free form, and verses get no section entries. Reported
bar numbers still reference the full flattened chart (verses included), so the apps highlight
the right bars.

### 6.1 Stage A — exact-hash contrafact groups

Hash each normalized `full_seq` and `section_seq`. Identical hashes → score 1.0 families,
reported directly. Cheap sanity layer; also a good self-test (Au Privave/Cheryl land here or very
close).

### 6.2 Stage B — retrieval (n-gram cosine)

* Shingle each sequence into overlapping token n-grams, n ∈ {2, 3, 4}.
* TF-IDF weight over the corpus; L2-normalized sparse vectors; cosine via sparse matrix product
  (`scipy.sparse` / `sklearn` acceptable dependencies; keep it optional-import-guarded like other
  repo tooling if desired).
* Two indexes: tunes (~2500 vectors, over `full_seq`) and sections (~10 000 vectors, any↔any so
  one flat index, over each section's sequence from `section_seqs` — annotated modulating
  sections are local-relative there (§4.3), so they compete in their own key with no extra
  channel).
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
* Section pairs align on their `section_seqs` sequences (local-relative where annotated, §4.3);
  no shift search — transposition handling is annotation-driven, not alignment-driven.
* Apply multiplicative penalties: meter mismatch (small) and mode mismatch (small — mode is
  already implicitly penalized by the shared pitch space, this is a nudge, not a wall).
* Whole-tune alignment stays **global-relative** (`full_seq`): a modulating tune scoring lower
  against non-modulating tunes at the whole-tune level is musically correct — its modulated
  sections find their partners through the section channel.
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

`displayer_similar.json` (displayer): per tune, up to 10 whole-tune suggestions and up to 10
section matches (deduped: repeated sections A/A1/A2 share a base name — one entry per tune-pair
relationship, best score) — score, matched-section labels, bar-mapping only. No component
breakdowns. Entries scoring below 0.25 are dropped, except that the top 5 are always kept
whatever their score (the UI shows ~5 rows and scrolls the rest). Size guard: this bundle must
stay < ~2 MB at 2500 tunes (it ships to GitHub Pages).

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
  matched section sits; fingerprint `sections` lines shown as captions. Section matches involving
  a modulated section carry a badge ("locally in A") so cross-key matches are visually distinct
  from in-key ones.
* **Rating buttons** (`good match` / `bad match`) per suggested pair → persisted in
  `localStorage`, exportable as a JSON download whose format matches §5.2 for committing under
  `data/chords/eval/ratings/`.
* **Confirmation mode** for the §5.1 seed: iterate `candidate` ground-truth entries, showing each
  proposed pair side by side; confirm/reject with one keystroke. Uses the same export mechanism —
  confirmations are just ratings on candidate pairs.
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
2a. **"Starts on" filter** in the tune list/browse view: a dropdown (or chip row) over
   `opening.degree` — `I`, `i`, `ii`, `IV`, `V`, … plus an "other" bucket for rare degrees — so a
   player can browse e.g. all tunes that start on the V. Populated from the degrees actually
   present in the bundled data (don't hard-code the list); combinable with existing
   search/filtering; tunes without annotation (no `opening`) appear under an "unknown" bucket
   rather than vanishing. Also show the opening degree as a small badge on the tune view next to
   the key.
2b. **Key / form / tag filters** in the top bar, next to the "starts on" dropdown, all built the
   same way (populated from the bundled data, combinable with search, playlist and each other;
   un-annotated tunes land in an "unknown" bucket):
   - **Key**: a dropdown over the annotated `key` ("F major", "D minor", …), sorted chromatically
     by tonic, each option carrying its tune count.
   - **Form**: a dropdown over `harmonic_fingerprint.family` ("32-bar AABA standard", "12-bar
     blues", …). The family strings are free text with a long tail — families carried by a single
     tune collapse into an "other" bucket, mirroring the rare-degree handling of 2a.
   - **Tags**: a "Tags" button opening a **multi-check** dropdown over
     `harmonic_fingerprint.tags` ("ii-V-chains", "chromatic-descent", "turnaround-ending", …),
     each with its tune count. Checked tags combine with **AND** (a tune must carry every checked
     tag); the menu stays open while checking so several tags can be combined, closes on
     outside-click or Escape, and becomes a bottom sheet on phones (same `positionDropdown`
     mechanics as the playlist menu). The button shows the number of active checks ("Tags · 2")
     and lights up while any are set.
   None of these persist across reloads (session-only, like "starts on"). On narrow screens the
   top bar wraps so the filter controls form their own row(s).
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
→ toggle all three display modes → highlighted bars present; and: set the "starts on" filter to a
degree → tune list narrows to matching tunes → clear filter restores the full list.

---

## 9. Phase order, dependencies, acceptance summary

| Phase | Deliverable | Depends on | Acceptance |
|---|---|---|---|
| 0 | `05_annotated` + `annotate_keys.py` + `apps/key_verifier/` | — | **✅ Met 2026-07-09** (scorer 91%). Remaining from this revision: §3.5 staleness handling, §3.3 key-agnostic-tags rule |
| 1 | `similarity/normalize.py` | 0 | §4.4: parser covers corpus (✅ done); remaining: §4.3 tonic-relative sequences, Au Privave ≈ Cheryl canary, local-key sequence test, forms validated |
| 2 | eval harness + `--seed-eval` candidates | 0, 1 | `--seed-eval` produces candidate families from all three §5.1 sources; harness runs end-to-end and reports candidate/confirmed separately |
| 3 | `06_similarity` + `compute.py` | 1, 2 | Rebuild ≤ 15 min at target scale (extrapolated); harness metrics reported (candidate set acceptable at this stage); Au Privave/Cheryl mutual top-3 |
| 4 | `apps/similarity_explorer/` | 3 | Playwright-verified; rating export round-trips into the harness; confirmation mode promotes §5.1 candidates — Phase 3 acceptance re-checked on the confirmed set afterwards |
| 5 | Displayer integration | 3 (4 recommended first) | Playwright-verified; bundle size guard holds; JS/Python transposition fixture passes |

Phases 0–2 are useful standalone (keys in the data, a clean chord parser, an eval harness) even
before any similarity ships. Build in order; do not start Phase 3 tuning before Phase 2 exists.

---

## 10. Risks and mitigations

* **Wrong keys poison everything downstream** → dual-voter design, review gate, `needs_review`
  exclusion from similarity, regression set.
* **False-positive local keys** (a strong passing ii–V read as a modulation) would spawn spurious
  local-relative variants → strict per-section margin threshold (§3.2), LLM prompt rule against
  tonicizations (§3.3), section-key disagreement forces review (§3.5), and the §3.7 acceptance
  check that non-modulating tunes carry no `section_keys`.
* **Stale analysis after a key correction** (fingerprint prose and section-key detection written
  under the old key) → §3.5: the update routine already recomputes `opening`; it additionally
  re-runs the per-section scorer as proposals and flags the fingerprint `stale` for a key-pinned
  LLM refresh on the next `annotate_keys.py` run. The engine is immune by construction — it
  reads `key`/`section_keys`/tags, never the fingerprint prose.
* **Intra-section modulations are not keyed in v1** (accepted limitation, was the delta channel's
  job) → sections match via their annotated local keys only; a modulation inside a section can
  cost recall on that passage. Watched via the eval set's modulating-section family (§5.1);
  extension options recorded in §4.3 slot in without interface changes.
* **Chord vocabulary drift** → single grammar source (`check_chord_syntax.py`); parser failure on
  any symbol is a hard error listing the offending file, never a silent skip.
* **JS/Python normalization drift** (Phase 5 transposition) → shared generated test fixture, §8.3.
* **Everything-matches degeneracy** (substitution costs too generous) → `non_matches` in the eval
  set; harness violation count must be zero.
* **Bundle bloat on GitHub Pages** → compact displayer format with hard size guard in
  `build_data.py` (fail the build, don't truncate silently).
* **Corpus 80× growth** → performance budget stated per stage; retrieval is vectorized from day
  one; alignment only runs on retrieval candidates.
