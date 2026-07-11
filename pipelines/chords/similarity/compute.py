"""Phase 3 — similarity engine (tune_similarity_spec §6).

    python -m pipelines.chords.similarity.compute            # full rebuild
    python -m pipelines.chords.similarity.compute --eval     # rebuild + harness

Input: data/chords/05_annotated tunes with status agreed/verified.
Output: data/chords/06_similarity/ — a regenerable tier, deleted and rebuilt
from 05_annotated at any time.

Stages: A exact-hash contrafact groups; B n-gram TF-IDF cosine retrieval
(top candidates only); C Smith–Waterman alignment scoring with the
music-aware substitution model and meter/mode penalties. Section matching is
any↔any over §4.3 section sequences (local-relative where annotated); no
shift search — transposition handling is annotation-driven.

Verse sections (names starting "verse") never enter comparisons, at either
level: the tune-level sequence is the verse-free form and verses get no
section entries. Reported bar numbers still reference the full flattened
chart (verses included) via Entry.slot_map, so the apps highlight the right
bars.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

from pipelines.chords.similarity import corpus  # noqa: E402
from pipelines.chords.similarity.align import (  # noqa: E402
    TokenTable, sw_score_batch, sw_traceback,
)
from pipelines.chords.similarity.normalize import (  # noqa: E402
    TuneSequences, form_warnings, tonic_relative,
)

ENGINE_VERSION = "1.0"

NGRAM_SIZES = (2, 3, 4)
TOP_CANDIDATES = 100          # retrieval candidates per query (spec §6.2)
TOP_TUNES = 20                # explorer: top-K similar tunes (spec §6.4)
TOP_SECTIONS = 20             # explorer: top-K section matches
DISPLAYER_TUNES = 10          # displayer bundle caps (spec §6.4)
DISPLAYER_SECTIONS = 10
DISPLAYER_MIN_SCORE = 0.25    # drop weaker suggestions from the bundle ...
DISPLAYER_MIN_KEEP = 5        # ... but always keep the top 5, any score
MIN_SECTION_SLOTS = 8         # skip tiny codas/interludes (< 4 bars)
METER_PENALTY = 0.95          # multiplicative, small (spec §6.3)
MODE_PENALTY = 0.9            # a nudge, not a wall (spec §6.3)


def _is_verse(name: str) -> bool:
    """Verse sections are prologue material, not the form: they never enter
    comparisons at either level (owner decision 2026-07-10)."""
    return name.lower().startswith("verse")


def _section_base(name: str) -> str:
    """'A1', 'A2', "A'" -> 'A': repeats of a section share their base name."""
    return re.sub(r"[0-9']+$", "", name)


def _displayer_cut(matches: list[dict], cap: int) -> list[dict]:
    """Bundle cut (spec §6.4): drop scores below DISPLAYER_MIN_SCORE, but
    always keep the top DISPLAYER_MIN_KEEP whatever they score. `matches`
    is score-sorted descending."""
    kept = [m for m in matches if m["score"] >= DISPLAYER_MIN_SCORE]
    if len(kept) < DISPLAYER_MIN_KEEP:
        kept = matches[:DISPLAYER_MIN_KEEP]
    return kept[:cap]


# ---------------------------------------------------------------------------
# Stage B — n-gram TF-IDF cosine retrieval
# ---------------------------------------------------------------------------

def _shingles(tokens: tuple) -> Counter:
    grams: Counter = Counter()
    for n in NGRAM_SIZES:
        for i in range(len(tokens) - n + 1):
            grams[tokens[i:i + n]] += 1
    return grams


def ngram_matrix(sequences: list[tuple]):
    """L2-normalized TF-IDF vectors over token n-grams (scipy CSR)."""
    from scipy.sparse import csr_matrix

    per_seq = [_shingles(s) for s in sequences]
    df: Counter = Counter()
    for grams in per_seq:
        df.update(grams.keys())
    vocab = {g: i for i, g in enumerate(df)}
    n_docs = len(sequences)

    rows, cols, vals = [], [], []
    for r, grams in enumerate(per_seq):
        for g, tf in grams.items():
            idf = math.log((1 + n_docs) / (1 + df[g])) + 1.0
            rows.append(r)
            cols.append(vocab[g])
            vals.append(tf * idf)
    mat = csr_matrix((vals, (rows, cols)), shape=(n_docs, len(vocab)),
                     dtype=np.float64)
    norms = np.sqrt(np.asarray(mat.multiply(mat).sum(axis=1)).ravel())
    norms[norms == 0] = 1.0
    mat = mat.multiply((1.0 / norms)[:, None]).tocsr()
    return mat


def top_candidates(mat, k: int, block: int = 1024
                   ) -> list[list[tuple[int, float]]]:
    """Per row: top-k (other_row, cosine) pairs, self excluded.

    The self-similarity product is taken in row blocks so the full N x N
    matrix never materializes — at the 1500-tune target the section matrix
    is thousands of rows and a one-shot product would spike to hundreds of
    MB while only the per-row top-k is ever kept.
    """
    out: list[list[tuple[int, float]]] = []
    for start in range(0, mat.shape[0], block):
        sims = (mat[start:start + block] @ mat.T).tocsr()
        for i in range(sims.shape[0]):
            r = start + i
            row = sims.getrow(i)
            idx, val = row.indices, row.data
            keep = idx != r
            idx, val = idx[keep], val[keep]
            if len(idx) > k:
                top = np.argpartition(val, -k)[-k:]
                idx, val = idx[top], val[top]
            order = np.argsort(-val)
            out.append([(int(idx[j]), float(val[j])) for j in order])
    return out


# ---------------------------------------------------------------------------
# Stage C — alignment scoring
# ---------------------------------------------------------------------------

@dataclass
class Entry:
    """One alignable unit: a whole tune or one section."""
    stem: str
    section: str | None
    tokens: tuple
    encoded: np.ndarray
    self_score: float
    mode: str            # effective mode (section local key wins)
    meter: str | None
    local_key: dict | None = None
    start_bar: int = 0   # bar offset of the section inside the flattened form
    slot_map: np.ndarray | None = None  # comparison slot -> full-chart slot


def _final_score(query: Entry, cand: Entry, raw: float, cosine: float) -> dict:
    alignment = min(raw / query.self_score, 1.0) if query.self_score > 0 else 0.0
    meter_pen = 1.0 if query.meter == cand.meter else METER_PENALTY
    mode_pen = 1.0 if query.mode == cand.mode else MODE_PENALTY
    return {
        "score": round(alignment * meter_pen * mode_pen, 4),
        "components": {
            "cosine": round(cosine, 4),
            "alignment": round(alignment, 4),
            "meter_penalty": meter_pen,
            "mode_penalty": mode_pen,
        },
    }


def _chart_bar(entry: Entry, slot: int) -> int:
    """Slot in the comparison sequence -> 1-based bar in the full chart."""
    if entry.slot_map is not None:
        slot = int(entry.slot_map[slot])
    return slot // 2 + 1


def _rescore_with_path(query: Entry, cand: Entry, cosine: float,
                       table: TokenTable) -> dict:
    raw, path = sw_traceback(query.encoded, cand.encoded, table.matrix)
    result = _final_score(query, cand, raw, cosine)
    bars, seen = [], set()
    for qs, cs in path:
        pair = (_chart_bar(query, qs), _chart_bar(cand, cs))  # bar granularity
        if pair not in seen:
            seen.add(pair)
            bars.append(list(pair))
    result["bars"] = bars
    return result


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def build_entries(docs: dict[str, dict]
                  ) -> tuple[list[Entry], list[Entry], TokenTable]:
    table = TokenTable()
    tunes: list[Entry] = []
    sections: list[Entry] = []
    for stem, doc in docs.items():
        for warning in form_warnings(doc):
            print(f"  form warning: {stem}: {warning}")
        seqs: TuneSequences = tonic_relative(doc)
        meter = seqs.meter
        # tune-level comparison sequence: the form without verses; slot_map
        # points each comparison slot back at its full-chart slot so bar
        # mappings stay chart-accurate
        chart_slots = [i for name, sec in seqs.section_seqs.items()
                       if not _is_verse(name)
                       for i in range(sec.start, sec.start + len(sec.tokens))]
        full = tuple(seqs.full_seq[i] for i in chart_slots)
        enc = table.encode(full)
        tunes.append(Entry(stem, None, full, enc, table.self_score(enc),
                           seqs.mode, meter,
                           slot_map=np.asarray(chart_slots, dtype=np.int32)))
        for name, sec in seqs.section_seqs.items():
            if _is_verse(name) or len(sec.tokens) < MIN_SECTION_SLOTS:
                continue
            enc = table.encode(sec.tokens)
            mode = sec.local_key["mode"] if sec.local_key else seqs.mode
            sections.append(Entry(stem, name, sec.tokens, enc,
                                  table.self_score(enc), mode, meter,
                                  sec.local_key, sec.start // 2))
    return tunes, sections, table


def hash_groups(entries: list[Entry]) -> list[list[Entry]]:
    """Stage A: identical normalized sequences -> score-1.0 contrafact
    groups. Groups spanning a single tune (an AABA's identical A sections)
    are trivial and dropped."""
    by_hash: dict[tuple, list[Entry]] = {}
    for e in entries:
        by_hash.setdefault(e.tokens, []).append(e)
    return [g for g in by_hash.values() if len({m.stem for m in g}) >= 2]


def _entry_ref(e: Entry):
    return e.stem if e.section is None else {"tune": e.stem, "section": e.section}


def run_engine(docs: dict[str, dict], out_dir: Path,
               top_candidates_k: int = TOP_CANDIDATES) -> dict:
    t0 = time.time()
    tunes, sections, table = build_entries(docs)
    titles = {stem: doc.get("title", stem) for stem, doc in docs.items()}
    families = {stem: (doc.get("harmonic_fingerprint") or {}).get("family")
                for stem, doc in docs.items()}
    print(f"corpus: {len(tunes)} tunes, {len(sections)} sections"
          f" (>= {MIN_SECTION_SLOTS} slots)")

    # Stage A — exact-hash groups
    tune_groups = hash_groups(tunes)
    section_groups = hash_groups(sections)
    print(f"stage A: {len(tune_groups)} tune hash groups,"
          f" {len(section_groups)} cross-tune section hash groups")

    # Stage B — retrieval. The shortlist widens with the corpus (5% of the
    # entry count, floored at --top-candidates) so recall doesn't erode as
    # the big families (blues, rhythm changes) outgrow a fixed top-100;
    # verify with --eval's candidate-recall metric at corpus milestones.
    def k_for(n_entries: int) -> int:
        return max(top_candidates_k, math.ceil(n_entries * 0.05))

    tune_k, sec_k = k_for(len(tunes)), k_for(len(sections))
    tune_cands = top_candidates(ngram_matrix([e.tokens for e in tunes]),
                                tune_k)
    sec_cands = top_candidates(ngram_matrix([e.tokens for e in sections]),
                               sec_k)
    print(f"stage B: retrieval done at {time.time() - t0:.1f}s"
          f" (k: tunes {tune_k}, sections {sec_k})")

    # Stage C — alignment scoring on retrieval candidates
    def score_all(entries: list[Entry], cands, keep: int):
        results = []
        for qi, entry_cands in enumerate(cands):
            query = entries[qi]
            raws = sw_score_batch(
                query.encoded, [entries[ci].encoded for ci, _ in entry_cands],
                table.matrix)
            scored = []
            for (ci, cosine), raw in zip(entry_cands, raws):
                r = _final_score(query, entries[ci], float(raw), cosine)
                scored.append((r["score"], ci, cosine))
            scored.sort(key=lambda t: -t[0])
            # traceback (pure Python) only for the kept pairs
            kept = []
            for _, ci, cosine in scored[:keep]:
                r = _rescore_with_path(query, entries[ci], cosine, table)
                kept.append((ci, r))
            results.append(kept)
        return results

    tune_results = score_all(tunes, tune_cands, TOP_TUNES)
    print(f"stage C: tunes aligned at {time.time() - t0:.1f}s")
    sec_results = score_all(sections, sec_cands, TOP_SECTIONS)
    print(f"stage C: sections aligned at {time.time() - t0:.1f}s")

    # ---- outputs ----------------------------------------------------------
    tunes_dir = out_dir / "tunes"
    tunes_dir.mkdir(parents=True, exist_ok=True)
    for old in tunes_dir.glob("*.json"):
        old.unlink()

    sec_by_tune: dict[str, list[dict]] = {stem: [] for stem in docs}
    for qi, kept in enumerate(sec_results):
        q = sections[qi]
        for ci, r in kept:
            c = sections[ci]
            sec_by_tune[q.stem].append({
                "section": q.section, "local_key": q.local_key,
                "start_bar": q.start_bar,
                "other": c.stem, "other_title": titles[c.stem],
                "other_section": c.section, "other_local_key": c.local_key,
                "other_start_bar": c.start_bar,
                "score": r["score"], "components": r["components"],
                "bars": r["bars"],
            })

    displayer: dict[str, dict] = {}
    for qi, kept in enumerate(tune_results):
        q = tunes[qi]
        doc = docs[q.stem]
        similar = []
        for ci, r in kept:
            c = tunes[ci]
            similar.append({
                "id": c.stem, "title": titles[c.stem],
                "family": families[c.stem],
                "score": r["score"], "components": r["components"],
                "bars": r["bars"],
            })
        sec_matches = sorted(sec_by_tune[q.stem], key=lambda m: -m["score"])
        out = {
            "id": q.stem, "title": titles[q.stem], "key": doc["key"],
            "family": families[q.stem], "form": doc.get("form"),
            "bar_count": len(q.tokens) // 2,
            "similar": similar,
            "section_matches": sec_matches[:TOP_SECTIONS],
        }
        (tunes_dir / f"{q.stem}.json").write_text(
            json.dumps(out, indent=1, ensure_ascii=False) + "\n", "utf-8")

        # compact displayer bundle: cross-tune only (spec §6.4). Repeated
        # sections (A, A1, A2 share a base name) list each tune-pair
        # relationship once — best score wins — so an AABA matching another
        # AABA doesn't fill the list with its A x A cross product.
        seen_pairs: set = set()
        dedup = []
        for m in sec_matches:
            key = (_section_base(m["section"]), m["other"],
                   _section_base(m["other_section"]))
            if m["other"] == q.stem or key in seen_pairs:
                continue
            seen_pairs.add(key)
            dedup.append(m)
        displayer[q.stem] = {
            "similar": [
                {"id": s["id"], "score": s["score"], "family": s["family"],
                 "bars": s["bars"]}
                for s in _displayer_cut(similar, DISPLAYER_TUNES)],
            "sections": [
                {"section": m["section"],
                 "local_key": m["local_key"],
                 "other": m["other"], "other_section": m["other_section"],
                 "other_local_key": m["other_local_key"],
                 "score": m["score"], "bars": m["bars"]}
                for m in _displayer_cut(dedup, DISPLAYER_SECTIONS)],
        }

    displayer_path = out_dir / "displayer_similar.json"
    displayer_path.write_text(
        json.dumps(displayer, separators=(",", ":"), ensure_ascii=False),
        "utf-8")
    size_mb = displayer_path.stat().st_size / 1e6
    print(f"displayer bundle: {size_mb:.2f} MB")

    index = {
        "built": datetime.datetime.now().isoformat(timespec="seconds"),
        "engine_version": ENGINE_VERSION,
        "corpus": len(tunes),
        "sections": len(sections),
        "elapsed_seconds": round(time.time() - t0, 1),
        "hash_groups": {
            "tunes": [[e.stem for e in g] for g in tune_groups],
            "sections": [[_entry_ref(e) for e in g] for g in section_groups],
        },
    }
    (out_dir / "index.json").write_text(
        json.dumps(index, indent=1, ensure_ascii=False) + "\n", "utf-8")
    print(f"engine done in {time.time() - t0:.1f}s -> {out_dir}")
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annotated", default=str(corpus.ANNOTATED_DIR))
    parser.add_argument("--out", default=str(corpus.SIMILARITY_DIR))
    parser.add_argument("--top-candidates", type=int, default=TOP_CANDIDATES)
    parser.add_argument("--eval", action="store_true",
                        help="rebuild, then run the §5.3 metrics harness and "
                             "record its metrics in index.json")
    args = parser.parse_args()

    docs = corpus.load_corpus(Path(args.annotated))
    if not docs:
        print("no annotated tunes with status agreed/verified", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    run_engine(docs, out_dir, args.top_candidates)

    if args.eval:
        from pipelines.chords.similarity import evaluate
        evaluate.run_harness(out_dir, update_index=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
