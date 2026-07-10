# User Manual — Grilles Digitizer

A human-centric guide to the whole project: what the pieces are, how data
flows through them, which commands to run when something changes, how the
similarity engine thinks, and where the Claude API (and your money) is
involved. The specs in [docs/specs/](specs/) are the contracts; this manual
is the tour.

---

## 1. What this project does

The source material is a scanned French anthology of jazz *grilles* (chord
charts) plus a companion book of melodies. The project turns those scans
into:

1. **Structured chord data** — one JSON file per tune, transcribed by a
   vision model and then verified by a human (you).
2. **Key annotations** — every verified tune gets its key, per-section local
   keys, and a harmonic fingerprint, decided by two independent "voters" and
   reviewed by you.
3. **Similarity links** — a local engine finds tunes and sections that share
   harmony (contrafacts, borrowed bridges, common turnarounds).
4. **A public web app** (the *displayer*, on GitHub Pages) that shows the
   original scans, the rendered chord grids, melodies, and "similar tunes"
   suggestions.

Everything is file-based. There is no database; every pipeline stage reads
one folder and writes the next.

## 2. The data flow at a glance

```
sources/AGJ.pdf (the book scan, local-only)
   │  stage 1: crop
   ▼
data/chords/01_crops/*.png          one PNG per tune            [tracked]
   │  stage 2: transcribe (Claude vision, $)
   ▼
data/chords/02_raw/*.json           machine output, READ-ONLY   [gitignored]
   │  stage 3: verifier app (your edits → 03_wip, approvals ↓)
   ▼
data/chords/04_verified/*.json      ground truth                [tracked]
   │  stage 6: annotate keys (scorer + one Claude call, $)
   ▼
data/chords/05_annotated/*.json     + key, section_keys,        [tracked]
   │                                  opening, fingerprint
   │  stage 8: similarity engine (pure local compute, free)
   ▼
data/chords/06_similarity/          per-tune neighbours          [gitignored,
   │                                + alignments                 regenerable]
   │  stage 5: displayer build
   ▼
apps/displayer/data/*.js            web bundles                 [tracked]
   │  push to main
   ▼
GitHub Pages
```

The melody side is parallel but shorter for now: melody scans are cropped and
straightened (`data/melody/01_crops/`), and any hand-finished `.abc` file in
`data/melody/04_verified/` is picked up automatically by the displayer build.
Symbol extraction (stages 2–5 of the melody spec) is not implemented yet.

### The golden rules

- **`data/chords/02_raw/` is read-only source material.** The verifier app
  never writes there; your edits live in `03_wip/` until a tune is promoted
  to `04_verified/`. Don't edit raw files by hand either — if a transcription
  is wrong, fix it in the app.
- **Never hand-edit `data/chords/05_annotated/` files.** Every correction
  goes through the key verifier app or `annotate_keys.py --set-key`, which
  recompute the derived fields (opening, section-key consistency, staleness
  flags). A hand edit silently breaks those invariants.
- **`data/chords/eval/` is curated human judgment** (confirmed families,
  ratings). It is never a build output; no script may overwrite it.
- **Numbered folders are pipeline tiers; regenerable ones are gitignored**
  (`02_raw`, `06_similarity`). If it's numbered and tracked, it contains
  human work — treat it with care.

## 3. The pipeline, stage by stage

Run everything from the repo root, with the venv active. All stages are
**resumable**: rerunning a command skips whatever is already done, so
stopping mid-run (Ctrl-C, laptop lid) is always safe.

### Stage 1 — crop (`pipelines/chords/crop_tunes.py`)

Cuts the book PDF into one PNG per tune, guided by the book's own index.
The filename `<page>_<index>_<TITLE_SLUG>.png` is the identity of the tune
for the whole rest of the pipeline. **To fix a wrong title, rename the
PNG** — nothing else needs to be kept in sync (the manifest is only
consulted to restore apostrophes and accents the slug can't encode).

### Stage 2 — transcribe (`pipelines/chords/transcribe.py`) — uses Claude

One vision call per crop turns the image into structured JSON in
`data/chords/02_raw/`. Output is validated structurally; failures are
retried with stricter reminders (and a truncated reply retries at a doubled
token cap — dense multi-strain tunes need it), and a tune that never
validates leaves a `*.error.json` stub (listed in `run_report.json`, hidden
from the verifier).
Resume = "a valid output file exists", so you can run the book in as many
sittings as you like.

### Stage 3 — verify (`python apps/verifier/verify_app.py`)

Your main review tool: original crop on the left, editable chord grid on the
right. Three states per tune — *needs review*, *deferred* (parked for
later), *verified*. Saving writes to `03_wip/`; **Mark as Verified** copies
the current state to `04_verified/`.

Both saving and verifying run a server-side validation gate (structure plus
every chord through the similarity engine's chord grammar). If you typo a
chord, the app tells you immediately — this is deliberate, because one
unparseable chord in `04_verified` would otherwise abort the whole
similarity build much later, far from the mistake.

### Stage 4 — index (`pipelines/build_title_index.py`)

Joins chord crops and melody crops by normalized title into
`data/title_index.csv` — the single source of truth for which scans belong
to the same tune. Fuzzy matches it isn't sure about are listed for review;
confirmed pairings are pinned in the script's manual list.

### Stage 5 — publish (`python apps/displayer/build_data.py`, then push)

Bundles everything the public app needs: the tune index, embedded chord
JSONs (annotated version preferred, plain verified as fallback), melody ABC,
similarity suggestions, and copies of the referenced scans. Pushing `main`
deploys to GitHub Pages automatically.

### Stage 6 — annotate keys (`pipelines/chords/annotate_keys.py`) — uses Claude

Every verified tune gets its key decided by **two independent voters**:

- a deterministic **scorer** (free, local) that counts ii-V-I resolutions,
  cadences, tonic residency, first/final chords — with guards for turnaround
  endings, Picardy thirds, and blues heads;
- **one Claude call** per tune (structured output) that also produces the
  harmonic fingerprint (form family, tags, per-section summaries).

If the voters agree → status `agreed`. If they disagree, or the scorer is
unsure, or a section's local key is contested → `needs_review`, for you.
Your decision in the key verifier app sets `verified`, which is permanent
until the underlying tune file changes (a sha256 of the source is stored, so
editing a verified tune automatically re-queues its annotation).

At 50+ pending tunes the calls go through the Batches API (half price,
up to a few hours of waiting). Every result is written to disk the moment it
arrives, and the batch id is saved to `data/chords/key_annotation_batch.json`
until the results are in — if anything interrupts the run, fetch it with
`annotate_keys.py --resume-batch`. Each run also deletes orphan annotations
whose verified source is gone.

### Stage 7 — verify keys (`python apps/key_verifier/key_verify_app.py`)

Reviews the annotations, `needs_review` queue first: crop, resolved key,
both votes, and the fingerprint side by side. `V` verifies, `←`/`→`
navigate. Correcting a key here re-runs the deterministic per-section pass
under the new key (proposals you accept or dismiss) and flags the
fingerprint prose for a key-pinned refresh (one Claude call on the next
annotate run).

### Stage 8 — similarity (`python -m pipelines.chords.similarity.compute`)

Pure local compute, no API, ~seconds at the current corpus size. Reads
`05_annotated` (statuses `agreed` and `verified` only), writes
`data/chords/06_similarity/`. Add `--eval` to also run the metrics harness
against the curated ground truth in `data/chords/eval/`. The **similarity
explorer** (`apps/similarity_explorer/`, rebuild its bundle with
`python apps/similarity_explorer/build_data.py`) is your local UI for
inspecting results side by side and confirming/rejecting candidate pairs —
those judgments land in `eval/` and make the harness smarter.

## 4. How similarity is computed (and what to trust)

### The idea in one paragraph

Two tunes are "similar" when their harmony does the same thing in the same
order, regardless of what key they're printed in. So every chart is first
converted into a **key-independent token sequence**: two slots per bar, each
slot a (scale-degree, chord-quality) pair relative to the tune's annotated
key. `Dm7 G7 | Cmaj7` in C and `Gm7 C7 | Fmaj7` in F become the identical
tokens `ii7 V7 | Imaj7`. That is why **key annotation must exist and be
right before similarity means anything** — with a wrong key, every token is
wrong and the tune matches nonsense.

### The three stages

- **Stage A — exact groups.** Tunes whose entire token sequence hashes
  identically are grouped as outright contrafacts. Free and certain.
- **Stage B — retrieval.** Every tune/section is described by its overlapping
  2-, 3- and 4-chord n-grams, weighted TF-IDF style so that ubiquitous
  fragments (a bare ii-V) count little and rare ones count a lot. Cosine
  similarity picks the ~100 most promising candidates per query. This stage
  exists purely to keep stage C affordable.
- **Stage C — alignment.** Each candidate pair is scored with a
  Smith–Waterman **local alignment** — the same algorithm biologists use for
  DNA — with music-aware substitution scores: an exact chord match scores
  highest, relative-major/minor and tritone substitutions score nearly as
  high, unrelated chords penalize, and gaps (inserted/deleted bars) cost an
  opening plus a per-bar extension. Meter mismatch (3/4 vs 4/4) and mode
  mismatch (major vs minor) apply mild penalties. The result is a 0–1 score
  plus the actual matching bar regions, which is what the apps highlight.

Sections are compared with the same machinery, any section against any
section, so a borrowed bridge is found even when the rest of the tunes
differ. **Verse sections are never compared** (house rule: verses are
prologue, not form), but reported bar numbers still reference the full
printed chart, so highlighting stays accurate.

The displayer gets a compact cut: top suggestions above a score floor, with
per-tune caps; the full data (every kept pair with alignments) stays in
`06_similarity/tunes/*.json` for the explorer.

### What it depends on

| Dependency | If it's wrong… |
|---|---|
| Verified transcription | garbage in, garbage out — a mis-read chord shifts tokens |
| Key annotation | wrong key = every token wrong; `needs_review` tunes are excluded for exactly this reason |
| Section names/boundaries | section-level matches follow your section splits |
| Two-slots-per-bar grid | beat-level passing chords inside a half-bar are invisible |

### Known limitations

- **Harmony only.** Melody is ignored entirely; two tunes with the same
  changes and different melodies are "the same" to this engine — which is
  the point (contrafacts), but worth remembering.
- **No transposition search.** Key handling is annotation-driven; the engine
  never tries "what if this section were read in another key" beyond the
  annotated section keys.
- **Substitution-heavy recompositions score lower.** Real charts of true
  contrafacts can differ in most tokens (Au Privave vs Cheryl agree on ~16
  of 24 slots). Don't read the absolute score as a percentage of identity —
  the reliable signal is *ranking*: true relatives sit in each other's top
  few suggestions.
- **Tuned at ~100 tunes.** The retrieval shortlist (top ~100 candidates) and
  the display thresholds were calibrated on the current corpus. As the
  corpus grows toward 1500, re-run `--eval` at milestones and keep feeding
  confirmed/rejected pairs from the explorer into `eval/` — that harness is
  what tells you if the shortlist or thresholds need widening.
- **Variants are ignored.** Only the main printed changes are compared.

## 5. Workflows — "I changed X, what do I run?"

The safe universal answer is: **run the chain downstream of what you
touched.** Every stage is resumable and cheap when nothing changed, so
over-running is harmless. The full chain:

```sh
python pipelines/chords/annotate_keys.py          # only re-annotates changed/new tunes ($ per changed tune)
python -m pipelines.chords.similarity.compute     # full rebuild, local, fast
python apps/displayer/build_data.py               # rebundle
git add -A && git commit && git push              # publish
```

Specific cases:

**I verified new tunes in the verifier app.**
Run the full chain above. New tunes get annotated (one Claude call each),
check the key verifier if any land in `needs_review`, similarity and the
bundle pick them up automatically.

**I fixed a chord in an already-verified tune.**
Make the fix in the *verifier app* (open the tune, edit, save, re-verify) —
not in the JSON by hand, so the validation gate checks it. The changed file's
sha256 no longer matches its annotation, so the next `annotate_keys.py` run
automatically re-annotates it (one call), even if it was human-verified —
your key verification is intentionally invalidated because the harmony
changed. Then similarity + bundle as above.

**I corrected a tune's key.**
Use the key verifier app, or `annotate_keys.py --set-key <stem> <tonic>
<major|minor>`. Accept/dismiss any re-detected section keys it proposes.
Then run `annotate_keys.py` once more — it performs the key-pinned
fingerprint refresh (one cheap call) for the corrected tune. Then
similarity + bundle.

**I renamed / re-cropped a chord PNG.**
The filename is the tune's identity, so a renamed crop is a *new* work unit:
stage 2 transcribes it fresh (one call), and the old stem's outputs become
orphans — delete the old `02_raw` file, and if it had been verified, delete
the old `04_verified` file too (the next annotate run sweeps the orphaned
annotation automatically). Then the usual chain.

**I un-verified or deleted a tune.**
Nothing else to do manually: the next `annotate_keys.py` run removes the
orphan annotation, and the subsequent similarity + bundle rebuilds drop it
everywhere.

**An annotation batch run was interrupted.**
`python pipelines/chords/annotate_keys.py --resume-batch` — the batch id was
saved when the batch was submitted; the results are fetched and written as
if nothing happened. `--status` will remind you if an unfinished batch is on
record.

**I changed similarity weights or code.**
`python -m pipelines.chords.similarity.compute --eval` and compare the
harness metrics before/after; rebuild the explorer bundle
(`python apps/similarity_explorer/build_data.py`) to inspect pairs, and the
displayer bundle if you want the changes live.

**I finished a melody (.abc).**
Drop it in `data/melody/04_verified/` (stem = melody scan stem), rebuild the
displayer bundle, push.

## 6. Where the Claude API is used, and what it costs

Only two pipeline stages call the API — everything else (cropping,
similarity, both review apps, the displayer) is local and free.

| Step | Calls | Model / mode | Rough cost |
|---|---|---|---|
| Stage 2 transcription | 1 per crop (+ retries on validation failure) | `claude-opus-4-8`, forced tool use, cached system prompt | ~2–3 ¢/tune ⇒ ~$30–45 for the ~1400 crops still to do; a cheaper model with caching lands well under $5 for the same work (spec Appendix C) |
| Stage 6 key annotation | 1 per new/changed verified tune | `claude-opus-4-8`, structured output, adaptive thinking; Batches API (−50 %) at ≥50 pending | a few ¢/tune. Thinking tokens bill as output, so budget ~$20–60 for ~1400 tunes batched, and check the actual `usage` on the first big batch |
| Key correction refresh | 1 per corrected key | same, key pinned | cents; corrections are rare |
| Eval seeding (`--seed-llm`) | 1, ever | one call proposing candidate families | cents; **not yet run** — ask before running |

Practical habits that keep this cheap:

- **Resume is free.** Re-running `transcribe.py` or `annotate_keys.py` costs
  nothing for tunes that are already done — only genuinely new or changed
  work is billed.
- **Let the Batches API kick in** for big annotation runs (it does so
  automatically at 50 pending); interactive mode is for small top-ups where
  you want results now.
- Both callers write every paid result to disk immediately — an interrupted
  run never wastes what was already bought.

## 7. Setup and maintenance

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...       # only needed for stages 2 and 6
```

Tests are plain `unittest`:

```sh
python -m unittest discover -s pipelines -p "test_*.py"
```

Things worth an occasional glance:

- `annotate_keys.py --status` — pending/agreed/needs-review counts, stale
  fingerprints, orphans, unfinished batches. The one-stop health check.
- `data/chords/02_raw/run_report.json` — transcription failures and flagged
  tunes after a stage 2 session.
- `python -m pipelines.chords.similarity.compute --eval` — the similarity
  quality metrics, whenever the corpus has grown meaningfully.
