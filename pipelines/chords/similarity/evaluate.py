"""Phase 2 — evaluation set and metrics harness (tune_similarity_spec §5).

    python -m pipelines.chords.similarity.evaluate                  # harness on 06_similarity
    python -m pipelines.chords.similarity.evaluate --seed-eval      # candidates (offline sources 1+2)
    python -m pipelines.chords.similarity.evaluate --seed-llm       # source 3: one Claude call (needs API)
    python -m pipelines.chords.similarity.evaluate --ingest-ratings # merge explorer rating exports

Ground truth lives in data/chords/eval/similarity_groundtruth.json — a
curated, un-numbered file: candidates are machine-proposed, every promotion
to "confirmed" is a human act (explorer confirmation mode or ratings).
Exact-hash groups are contrafacts *by construction* and enter as confirmed.

Every scoring-relevant change to Phase 3 must be accompanied by a harness
run in the PR/commit message (spec §5.3).
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

from pipelines.chords.similarity import corpus  # noqa: E402
from pipelines.chords.similarity.normalize import tonic_relative  # noqa: E402

GROUNDTRUTH = corpus.EVAL_DIR / "similarity_groundtruth.json"
RATINGS_DIR = corpus.EVAL_DIR / "ratings"
TITLE_INDEX = corpus.REPO / "data" / "title_index.csv"

RECALL_KS = (5, 10)
VIOLATION_THRESHOLD = 0.5   # a non_matches pair scoring above this is a bug
MIN_SECTION_SLOTS = 8       # mirror compute.py's section floor

# Tags distinctive enough to seed candidate families (spec §5.1 source 1),
# with the level they imply. Bridge-flavored tags become section-level
# candidates anchored on the B section (a heuristic — a human confirms).
DISTINCTIVE_TAGS = {
    "blues-form": ("tune", None),
    "minor-blues": ("tune", None),
    "rhythm-changes-a": ("tune", None),
    "rhythm-changes-bridge": ("section", "B"),
    "montgomery-ward-bridge": ("section", "B"),
    "sears-roebuck-bridge": ("section", "B"),
    "dominant-cycle-bridge": ("section", "B"),
}
# Family strings that are pure form descriptors group half the corpus and
# mean nothing for similarity; they are skipped.
_GENERIC_FAMILY = re.compile(r"standard|ballad|song-form|verse|-bar tune|form\b")


# ---------------------------------------------------------------------------
# Ground-truth file
# ---------------------------------------------------------------------------

def load_groundtruth(path: Path = GROUNDTRUTH) -> dict:
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    return {"families": [], "non_matches": []}


def save_groundtruth(gt: dict, path: Path = GROUNDTRUTH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(gt, indent=1, ensure_ascii=False) + "\n", "utf-8")


def _member_key(member) -> tuple:
    if isinstance(member, dict):
        return (member["tune"], member["section"])
    return (member,)


def _family_pairs(family) -> set[frozenset]:
    keys = [_member_key(m) for m in family["members"]]
    return {frozenset((a, b)) for i, a in enumerate(keys) for b in keys[i + 1:]}


# ---------------------------------------------------------------------------
# Seeding (spec §5.1)
# ---------------------------------------------------------------------------

def _merge_families(gt: dict, new_families: list[dict], replace_source: str | None = None):
    """Add families idempotently: existing names are kept (confirmed entries
    are never touched); `replace_source` regenerates machine-derived entries
    of that source that are still candidates/auto-confirmed."""
    if replace_source:
        gt["families"] = [f for f in gt["families"]
                          if f.get("source") != replace_source
                          or f.get("human_confirmed")]
    existing = {f["name"] for f in gt["families"]}
    added = 0
    for fam in new_families:
        if fam["name"] not in existing:
            gt["families"].append(fam)
            added += 1
    return added


def seed_hash_groups(gt: dict, docs: dict[str, dict]) -> int:
    """Source 2 — exact-hash groups: contrafacts by construction, enter
    directly as confirmed."""
    seqs = {stem: tonic_relative(doc) for stem, doc in docs.items()}
    families = []

    by_full: dict[tuple, list[str]] = defaultdict(list)
    for stem, s in seqs.items():
        by_full[s.full_seq].append(stem)
    for members in by_full.values():
        if len(members) >= 2:
            families.append({
                "name": "hash-tune-" + "-".join(sorted(members))[:60],
                "level": "tune", "status": "confirmed", "source": "hash",
                "members": sorted(members)})

    by_sec: dict[tuple, list[dict]] = defaultdict(list)
    for stem, s in seqs.items():
        for name, sec in s.section_seqs.items():
            if len(sec.tokens) >= MIN_SECTION_SLOTS:
                by_sec[sec.tokens].append({"tune": stem, "section": name})
    for members in by_sec.values():
        if len({m["tune"] for m in members}) >= 2:
            key = "-".join(sorted(f"{m['tune']}.{m['section']}" for m in members))
            families.append({
                "name": ("hash-section-" + key)[:80],
                "level": "section", "status": "confirmed", "source": "hash",
                "members": members})
    return _merge_families(gt, families, replace_source="hash")


def seed_fingerprint_groups(gt: dict, docs: dict[str, dict]) -> int:
    """Source 1 — fingerprint family / distinctive-tag groupings, as
    candidates for human confirmation."""
    def norm_family(f: str) -> str:
        return re.sub(r"\s*\(.*\)$", "", f.strip().lower())

    by_family: dict[str, list[str]] = defaultdict(list)
    by_tag: dict[str, list[str]] = defaultdict(list)
    for stem, doc in docs.items():
        fp = doc.get("harmonic_fingerprint") or {}
        fam = fp.get("family")
        if fam and not _GENERIC_FAMILY.search(norm_family(fam)):
            by_family[norm_family(fam)].append(stem)
        for tag in fp.get("tags", []):
            if tag in DISTINCTIVE_TAGS:
                by_tag[tag].append(stem)

    families = []
    for fam, members in sorted(by_family.items()):
        if len(members) >= 2:
            slug = re.sub(r"[^a-z0-9]+", "-", fam).strip("-")
            families.append({
                "name": f"family-{slug}", "level": "tune",
                "status": "candidate", "source": "fingerprint",
                "members": sorted(members)})
    for tag, members in sorted(by_tag.items()):
        level, section = DISTINCTIVE_TAGS[tag]
        if level == "section":
            mem = [{"tune": s, "section": section} for s in sorted(members)
                   if section in (docs[s].get("sections") or {})]
        else:
            mem = sorted(members)
        if len(mem) >= 2:
            families.append({
                "name": f"tag-{tag}", "level": level,
                "status": "candidate", "source": "fingerprint",
                "members": mem})
    return _merge_families(gt, families)


# ---------------------------------------------------------------------------
# Source 3 — title-index LLM call (one Claude request; costs cents, runs once)
# ---------------------------------------------------------------------------

TITLE_SCHEMA = {
    "type": "object",
    "properties": {
        "families": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["contrafact", "progression-family"]},
                    "titles": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
                "required": ["name", "kind", "titles", "note"],
                "additionalProperties": False,
            },
        },
        "modulating_bridge_examples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"},
                               "note": {"type": "string"}},
                "required": ["title", "note"],
                "additionalProperties": False,
            },
        },
        "non_matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["families", "modulating_bridge_examples", "non_matches"],
    "additionalProperties": False,
}

TITLE_PROMPT = """\
You are a jazz repertoire expert. Below is the complete title list of a
French anthology of jazz chord charts (swing-era and bebop standards).

From THESE TITLES ONLY (never invent a title that is not in the list):

1. "families": groups of tunes you know to be contrafacts of each other
   (same chord changes, e.g. blues heads, rhythm changes heads) or to share
   a well-known progression family ("Honeysuckle Rose changes", "Indiana
   changes", ...). Use kebab-case names, cite only titles from the list,
   and say in "note" why they belong together. Only include groups you are
   reasonably sure about.
2. "modulating_bridge_examples": tunes in this list whose bridge (or
   another section) famously sits in a different local key than the tune
   (e.g. a bridge in the IV). These seed the modulating-section evaluation
   family.
3. "non_matches": 5-10 pairs of tunes from the list that are obviously
   harmonically UNRELATED (different form, different progression family) —
   they guard the evaluation against everything-matches degeneracy.

Copy titles character-for-character from the list.
"""


def _title_map(docs: dict[str, dict]) -> dict[str, str]:
    """normalized title -> stem, for digitized tunes."""
    def norm(t: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", t.upper())
    return {norm(doc.get("title", stem)): stem for stem, doc in docs.items()}


def seed_llm(gt: dict, docs: dict[str, dict], digitized_only: bool = False) -> int:
    """One structured-outputs Claude call over the title index (§5.1 source
    3). Candidates only — a human confirms them in the explorer.

    `digitized_only` restricts the title list to the digitized corpus: while
    most of the book is undigitized, families named over the full index
    mostly can't be mapped to members yet — the restricted call trades
    breadth for answers that are usable in the harness today.
    """
    import csv

    import anthropic

    from pipelines.chords.key_annotation.llm import MODEL, _parse_response

    if digitized_only:
        titles = sorted({doc.get("title", stem) for stem, doc in docs.items()})
        print(f"asking about the {len(titles)} digitized titles ...")
    else:
        titles = []
        with TITLE_INDEX.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                t = row["chords_title"].replace("_", " ").strip()
                if t and t not in titles:
                    titles.append(t)
        print(f"asking about {len(titles)} titles from {TITLE_INDEX.name} ...")

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=[{"type": "text", "text": TITLE_PROMPT}],
        output_config={"format": {"type": "json_schema", "schema": TITLE_SCHEMA}},
        messages=[{"role": "user", "content": "\n".join(titles)}],
    )
    reply = _parse_response(message)

    tmap = _title_map(docs)

    def to_stem(title: str) -> str | None:
        return tmap.get(re.sub(r"[^A-Z0-9]+", "", title.upper()))

    families, skipped = [], []
    for fam in reply["families"]:
        members = sorted({s for s in map(to_stem, fam["titles"]) if s})
        if len(members) >= 2:
            slug = re.sub(r"[^a-z0-9]+", "-", fam["name"].lower()).strip("-")
            families.append({
                "name": f"llm-{slug}", "level": "tune",
                "status": "candidate", "source": "llm",
                "note": fam["note"], "members": members})
        else:
            skipped.append(fam["name"])
    added = _merge_families(gt, families)

    existing_nm = {frozenset(nm["pair"]) for nm in gt["non_matches"]}
    nm_added = 0
    for nm in reply["non_matches"]:
        a, b = to_stem(nm["a"]), to_stem(nm["b"])
        if a and b and a != b and frozenset((a, b)) not in existing_nm:
            gt["non_matches"].append(
                {"pair": sorted((a, b)), "status": "candidate", "source": "llm"})
            existing_nm.add(frozenset((a, b)))
            nm_added += 1

    bridges = gt.get("llm_modulating_bridge_examples") or []
    seen_titles = {b["title"] for b in bridges}
    for e in reply["modulating_bridge_examples"]:
        if e["title"] not in seen_titles:
            bridges.append({"title": e["title"], "stem": to_stem(e["title"]),
                            "note": e["note"]})
            seen_titles.add(e["title"])
    gt["llm_modulating_bridge_examples"] = bridges

    print(f"llm seed: +{added} candidate families, +{nm_added} non-match "
          f"candidates, {len(bridges)} modulating-bridge pointers"
          + (f"; skipped (undigitized): {', '.join(skipped)}" if skipped else ""))
    return added


# ---------------------------------------------------------------------------
# Ratings ingestion (spec §5.2)
# ---------------------------------------------------------------------------

def _rating_key(entry) -> tuple:
    return _member_key(entry)


def ingest_ratings(gt: dict, ratings_dir: Path = RATINGS_DIR) -> dict:
    """Merge explorer rating exports: good extends families / confirms
    candidates, bad extends non_matches / deletes 2-member candidates."""
    stats = {"good": 0, "bad": 0, "promoted": 0, "deleted": 0, "created": 0}
    files = sorted(ratings_dir.glob("*.json")) if ratings_dir.exists() else []
    for path in files:
        data = json.loads(path.read_text("utf-8"))
        for r in data.get("ratings", []):
            a, b = _rating_key(r["query"]), _rating_key(r["candidate"])
            pair = frozenset((a, b))
            level = r["level"]
            containing = [f for f in gt["families"] if f["level"] == level
                          and pair <= {_member_key(m) for m in f["members"]}]
            if r["rating"] == "good":
                stats["good"] += 1
                for fam in containing:
                    if fam["status"] == "candidate":
                        fam["status"] = "confirmed"
                        fam["human_confirmed"] = True
                        stats["promoted"] += 1
                if not containing:
                    stats["created"] += 1
                    gt["families"].append({
                        "name": f"rated-{'-'.join('/'.join(k) for k in sorted((a, b)))}"[:80],
                        "level": level, "status": "confirmed",
                        "source": "rating", "human_confirmed": True,
                        "members": [
                            m if len(k) == 1 else {"tune": k[0], "section": k[1]}
                            for k, m in ((a, a[0]), (b, b[0]))],
                    })
            else:
                stats["bad"] += 1
                for fam in containing:
                    if fam["status"] == "candidate" and len(fam["members"]) == 2:
                        gt["families"].remove(fam)
                        stats["deleted"] += 1
                if level == "tune":
                    existing = {frozenset(nm["pair"]) for nm in gt["non_matches"]}
                    if frozenset((a[0], b[0])) not in existing:
                        gt["non_matches"].append({
                            "pair": sorted((a[0], b[0])),
                            "status": "confirmed", "source": "rating"})
    print(f"ratings: {len(files)} file(s); {stats}")
    return stats


# ---------------------------------------------------------------------------
# Metrics harness (spec §5.3)
# ---------------------------------------------------------------------------

def _load_similarity(similarity_dir: Path) -> dict[str, dict]:
    out = {}
    for path in sorted((similarity_dir / "tunes").glob("*.json")):
        out[path.stem] = json.loads(path.read_text("utf-8"))
    return out


def _tune_recall(gt: dict, sim: dict, status: str) -> dict:
    per_k = {k: [] for k in RECALL_KS}
    families = members = 0
    for fam in gt["families"]:
        if fam["level"] != "tune" or fam["status"] != status:
            continue
        present = [m for m in fam["members"] if m in sim]
        if len(present) < 2:
            continue
        families += 1
        for m in present:
            members += 1
            ranked = [s["id"] for s in sim[m]["similar"]]
            others = [o for o in present if o != m]
            for k in RECALL_KS:
                topk = set(ranked[:k])
                per_k[k].append(sum(o in topk for o in others) / len(others))
    return {"families": families, "members": members,
            **{f"recall@{k}": round(sum(v) / len(v), 4) if v else None
               for k, v in per_k.items()}}


def _section_recall(gt: dict, sim: dict, status: str) -> dict:
    per_k = {k: [] for k in RECALL_KS}
    families = members = 0
    for fam in gt["families"]:
        if fam["level"] != "section" or fam["status"] != status:
            continue
        present = [m for m in fam["members"] if m["tune"] in sim]
        if len(present) < 2:
            continue
        families += 1
        for m in present:
            members += 1
            matches = [x for x in sim[m["tune"]]["section_matches"]
                       if x["section"] == m["section"]]
            ranked = [(x["other"], x["other_section"]) for x in matches]
            others = [(o["tune"], o["section"]) for o in present if o != m]
            for k in RECALL_KS:
                topk = set(ranked[:k])
                per_k[k].append(sum(o in topk for o in others) / len(others))
    return {"families": families, "members": members,
            **{f"recall@{k}": round(sum(v) / len(v), 4) if v else None
               for k, v in per_k.items()}}


def _load_rated_pairs(ratings_dir: Path) -> dict[frozenset, str]:
    rated: dict[frozenset, str] = {}
    files = sorted(ratings_dir.glob("*.json")) if ratings_dir.exists() else []
    for path in files:
        for r in json.loads(path.read_text("utf-8")).get("ratings", []):
            if r["level"] == "tune":
                rated[frozenset((r["query"], r["candidate"]))] = r["rating"]
    return rated


def _precision10(sim: dict, rated: dict[frozenset, str]) -> dict | None:
    if not rated:
        return None
    hits = misses = 0
    for stem, doc in sim.items():
        for s in doc["similar"][:10]:
            verdict = rated.get(frozenset((stem, s["id"])))
            if verdict == "good":
                hits += 1
            elif verdict == "bad":
                misses += 1
    total = hits + misses
    return {"rated_in_top10": total,
            "precision@10": round(hits / total, 4) if total else None}


def _non_match_violations(gt: dict, sim: dict) -> list[dict]:
    out = []
    for nm in gt["non_matches"]:
        a, b = nm["pair"]
        for q, c in ((a, b), (b, a)):
            if q not in sim:
                continue
            for s in sim[q]["similar"]:
                if s["id"] == c and s["score"] > VIOLATION_THRESHOLD:
                    out.append({"pair": [q, c], "score": s["score"],
                                "status": nm.get("status", "candidate")})
    return out


def run_harness(similarity_dir: Path = corpus.SIMILARITY_DIR, *,
                update_index: bool = False,
                groundtruth_path: Path = GROUNDTRUTH,
                ratings_dir: Path = RATINGS_DIR) -> dict:
    gt = load_groundtruth(groundtruth_path)
    sim = _load_similarity(similarity_dir)
    if not sim:
        print(f"no similarity output in {similarity_dir}", file=sys.stderr)
        return {}

    metrics = {
        "computed": datetime.datetime.now().isoformat(timespec="seconds"),
        "tunes_in_output": len(sim),
        "tune_recall": {s: _tune_recall(gt, sim, s)
                        for s in ("confirmed", "candidate")},
        "section_recall": {s: _section_recall(gt, sim, s)
                           for s in ("confirmed", "candidate")},
        "precision": _precision10(sim, _load_rated_pairs(ratings_dir)),
        "non_match_violations": _non_match_violations(gt, sim),
    }

    print("\n=== similarity harness (candidate metrics are a relative "
          "signal only, spec §5.1) ===")
    for level in ("tune_recall", "section_recall"):
        for status in ("confirmed", "candidate"):
            m = metrics[level][status]
            print(f"{level:15s} {status:9s} families={m['families']:2d} "
                  f"members={m['members']:3d} "
                  + " ".join(f"recall@{k}={m[f'recall@{k}']}" for k in RECALL_KS))
    print(f"precision:      {metrics['precision']}")
    v = metrics["non_match_violations"]
    print(f"non-match violations (> {VIOLATION_THRESHOLD}): {len(v)}"
          + (f" {v}" if v else ""))

    # delta report vs the metrics stored by the previous build (spec §5.3)
    index_path = similarity_dir / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text("utf-8"))
        previous = index.get("metrics")
        if previous:
            _print_delta(previous, metrics)
        if update_index:
            index["metrics"] = metrics
            index_path.write_text(
                json.dumps(index, indent=1, ensure_ascii=False) + "\n", "utf-8")
            print("metrics recorded in index.json")
    return metrics


def _print_delta(prev: dict, cur: dict) -> None:
    lines = []
    for level in ("tune_recall", "section_recall"):
        for status in ("confirmed", "candidate"):
            for k in RECALL_KS:
                key = f"recall@{k}"
                a = (prev.get(level, {}).get(status, {}) or {}).get(key)
                b = cur[level][status][key]
                if a is not None and b is not None and a != b:
                    lines.append(f"  {level}.{status}.{key}: {a} -> {b}"
                                 f" ({'+' if b >= a else ''}{round(b - a, 4)})")
    pv = len(prev.get("non_match_violations") or [])
    cv = len(cur["non_match_violations"])
    if pv != cv:
        lines.append(f"  non_match_violations: {pv} -> {cv}")
    print("delta vs previous build:" if lines else
          "delta vs previous build: no metric changes")
    for line in lines:
        print(line)


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--similarity", default=str(corpus.SIMILARITY_DIR))
    parser.add_argument("--annotated", default=str(corpus.ANNOTATED_DIR))
    parser.add_argument("--seed-eval", action="store_true",
                        help="generate candidate families from the offline "
                             "sources (fingerprints + exact hashes)")
    parser.add_argument("--seed-llm", action="store_true",
                        help="source 3: one Claude call over the title index "
                             "(requires ANTHROPIC_API_KEY; costs cents)")
    parser.add_argument("--digitized-only", action="store_true",
                        help="with --seed-llm: send only digitized titles, so "
                             "every proposed family/non-match maps to members")
    parser.add_argument("--ingest-ratings", action="store_true",
                        help="merge data/chords/eval/ratings/*.json into the "
                             "ground truth")
    parser.add_argument("--update-index", action="store_true",
                        help="record harness metrics in 06_similarity/index.json")
    args = parser.parse_args()

    if args.seed_eval or args.seed_llm or args.ingest_ratings:
        docs = corpus.load_corpus(Path(args.annotated))
        gt = load_groundtruth()
        if args.seed_eval:
            n_hash = seed_hash_groups(gt, docs)
            n_fp = seed_fingerprint_groups(gt, docs)
            print(f"seeded: {n_hash} hash families (confirmed by construction),"
                  f" {n_fp} fingerprint candidates")
        if args.seed_llm:
            seed_llm(gt, docs, digitized_only=args.digitized_only)
        if args.ingest_ratings:
            ingest_ratings(gt)
        save_groundtruth(gt)
        counts = defaultdict(int)
        for f in gt["families"]:
            counts[(f["level"], f["status"])] += 1
        print(f"ground truth now: " + ", ".join(
            f"{n} {level}/{status}" for (level, status), n in sorted(counts.items()))
            + f"; {len(gt['non_matches'])} non-matches -> {GROUNDTRUTH}")
        return 0

    run_harness(Path(args.similarity), update_index=args.update_index)
    return 0


if __name__ == "__main__":
    sys.exit(main())
