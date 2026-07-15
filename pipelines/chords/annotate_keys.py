#!/usr/bin/env python3
"""Phase 0 key annotation: data/chords/04_verified -> data/chords/05_annotated.

Usage
-----
    python pipelines/chords/annotate_keys.py                 # annotate everything pending
    python pipelines/chords/annotate_keys.py --status        # per-status counts, no work
    python pipelines/chords/annotate_keys.py --scorer-only   # skip the LLM voter (dev/offline)
    python pipelines/chords/annotate_keys.py --reuse-annotation  # refresh 05 from an
        # edited 04_verified without re-voting: carry each existing key
        # decision forward, skip tunes that have no annotation yet
    python pipelines/chords/annotate_keys.py --set-key <stem> <tonic> <major|minor>
    python pipelines/chords/annotate_keys.py --resume-batch [BATCH_ID]  # pick up an
        # interrupted Batches-API run (id read from data/chords/key_annotation_batch.json
        # when omitted)

Each tune gets two independent key votes — a deterministic functional scorer
and one Claude call (structured outputs) — adjudicated into `agreed` /
`needs_review`; humans verify in apps/key_verifier. Idempotent: a tune is
skipped while its annotated file matches the source's sha256, so `verified`
annotations survive reruns and a changed source re-triggers annotation.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from pipelines.chords.key_annotation import core  # noqa: E402
from pipelines.chords.key_annotation.llm import LLMVoteError  # noqa: E402
from pipelines.chords.key_annotation.scorer import score_tune  # noqa: E402

VERIFIED_DIR = _REPO / "data" / "chords" / "04_verified"
ANNOTATED_DIR = _REPO / "data" / "chords" / "05_annotated"

_IGNORED_STEMS = {"verification_state", "run_report", "run_state"}


def _tune_paths(verified_dir: Path) -> list[Path]:
    return [p for p in sorted(verified_dir.glob("*.json"))
            if p.stem not in _IGNORED_STEMS and not p.stem.endswith("_opus")]


def _batch_state_path(annotated_dir: Path) -> Path:
    # Sibling of verification_state.json, outside both tune directories so
    # no *.json glob (corpus load, displayer build) ever picks it up.
    return annotated_dir.parent / "key_annotation_batch.json"


def _orphan_paths(verified_dir: Path, annotated_dir: Path) -> list[Path]:
    """Annotated files whose 04_verified source no longer exists — without a
    sweep they would keep flowing into the similarity corpus and displayer."""
    if not annotated_dir.exists():
        return []
    verified = {p.name for p in _tune_paths(verified_dir)}
    return [p for p in _tune_paths(annotated_dir) if p.name not in verified]


def sweep_orphans(verified_dir: Path, annotated_dir: Path) -> int:
    orphans = _orphan_paths(verified_dir, annotated_dir)
    for path in orphans:
        path.unlink()
        print(f"  removed orphan annotation (source gone from "
              f"{verified_dir.name}): {path.name}")
    return len(orphans)


def _refresh_derived(annotated: dict) -> bool:
    """Recompute the deterministic derived fields of one annotated document
    — `harmonic_analysis`, `opening` and the building-block-derived
    `harmonic_fingerprint.tags` — in place. True when anything changed."""
    from pipelines.chords.harmonic_analysis import analyze_annotated
    from pipelines.chords.similarity.normalize import compute_opening

    def snapshot():
        return (annotated.get("harmonic_analysis"), annotated.get("opening"),
                (annotated.get("harmonic_fingerprint") or {}).get("tags"))

    before = snapshot()
    annotated["harmonic_analysis"] = analyze_annotated(annotated)
    key = annotated["key"]
    annotated["opening"] = compute_opening(annotated, key["tonic"], key["mode"])
    core.apply_derived_tags(annotated)
    return snapshot() != before


def refresh_derived_fields(annotated_dir: Path) -> int:
    """Deterministic sweep (harmonic_analysis_spec §6): recompute every
    annotated file's derived fields and write the ones that changed — files
    predating a field, an analyzer/catalog change, or a version bump. Free
    (no LLM), so it simply runs on every annotate invocation.
    """
    if not annotated_dir.exists():
        return 0
    refreshed = 0
    for path in _tune_paths(annotated_dir):
        annotated = core.read_json(path)
        if "key" not in annotated:
            continue
        if _refresh_derived(annotated):
            core.write_annotated(path, annotated)
            refreshed += 1
    if refreshed:
        print(f"{refreshed} derived field set(s) refreshed "
              "(analysis / opening / tags)")
    return refreshed


def _stale_fingerprint_paths(annotated_dir: Path) -> list[Path]:
    """Annotated files flagged for the key-pinned fingerprint refresh (§3.5)."""
    out = []
    for path in sorted(annotated_dir.glob("*.json")):
        if path.stem in _IGNORED_STEMS:
            continue
        annotated = core.read_json(path)
        if (annotated.get("harmonic_fingerprint") or {}).get("stale"):
            out.append(path)
    return out


def cmd_status(verified_dir: Path, annotated_dir: Path) -> int:
    counts: Counter[str] = Counter()
    for path in _tune_paths(verified_dir):
        ann_path = annotated_dir / path.name
        if core.is_pending(path, ann_path):
            print(ann_path)
            counts["pending"] += 1
        else:
            counts[core.read_json(ann_path)["key_annotation"]["status"]] += 1
    total = sum(counts.values())
    print(f"{total} tunes: " + ", ".join(
        f"{counts[k]} {k}" for k in ("verified", "agreed", "needs_review", "pending")
        if counts[k]))
    stale = _stale_fingerprint_paths(annotated_dir) if annotated_dir.exists() else []
    if stale:
        print(f"{len(stale)} stale fingerprint(s) awaiting key-pinned refresh: "
              + ", ".join(p.stem for p in stale))
    orphans = _orphan_paths(verified_dir, annotated_dir)
    if orphans:
        print(f"{len(orphans)} orphan annotation(s) (source deleted; the next "
              "annotate run removes them): "
              + ", ".join(p.stem for p in orphans))
    if annotated_dir.exists():
        # Derived-field health (spec §6): outdated files (analyzer, catalog
        # or tag/opening rules changed since they were written) and the
        # spot-check flags.
        outdated, flags = 0, []
        for path in _tune_paths(annotated_dir):
            annotated = core.read_json(path)
            if "key" not in annotated:
                continue
            if _refresh_derived(annotated):
                outdated += 1
            flags += [f"{path.stem}: {f}"
                      for f in annotated["harmonic_analysis"].get("flags", [])]
        if outdated:
            print(f"{outdated} outdated derived field set(s) (analysis / "
                  "opening / tags); the next annotate run refreshes them")
        if flags:
            print(f"{len(flags)} harmonic-analysis spot-check flag(s):")
            for f in flags:
                print(f"  {f}")
    state_path = _batch_state_path(annotated_dir)
    if state_path.exists():
        batch_id = core.read_json(state_path).get("batch_id")
        print(f"unfinished batch {batch_id} on record — fetch it with "
              "--resume-batch")
    return 0


def refresh_stale_fingerprints(annotated_dir: Path) -> int:
    """Key-pinned LLM refresh for fingerprints flagged stale by a key
    correction (spec §3.5). Never touches `key`, `opening` or `status`;
    on failure the stale flag stays so the next run retries.
    """
    paths = _stale_fingerprint_paths(annotated_dir)
    if not paths:
        return 0
    print(f"{len(paths)} stale fingerprint(s); running key-pinned refresh")
    from pipelines.chords.key_annotation import llm
    docs = {p.stem: core.read_json(p) for p in paths}
    items = {stem: (d, d["key"], d.get("section_keys"))
             for stem, d in docs.items()}
    results = llm.refresh_fingerprints(items)
    refreshed = 0
    for path in paths:
        stem, annotated = path.stem, docs[path.stem]
        result = results[stem]
        if isinstance(result, LLMVoteError):
            print(f"  {stem}: refresh failed, fingerprint stays stale ({result})")
            continue
        annotated["harmonic_fingerprint"] = core.fingerprint_json(result)
        core.apply_derived_tags(annotated)  # LLM tags never land on disk
        core.write_annotated(path, annotated)
        refreshed += 1
        print(f"  {stem}: fingerprint refreshed")
    return refreshed


def _make_writer(pending: list[Path], annotated_dir: Path
                 ) -> tuple[dict[str, dict], Counter, "callable"]:
    """(tunes, counts, record) for a set of pending tunes.

    `record(stem, llm_result)` builds and writes one annotation immediately —
    it is handed to llm.run as the on_result callback so every paid result
    hits disk the moment it exists, whatever happens to the rest of the run.
    """
    tunes = {p.stem: core.read_json(p) for p in pending}
    shas = {p.stem: core.source_sha256(p) for p in pending}
    names = {p.stem: p.name for p in pending}
    scorer_votes = {stem: score_tune(tune) for stem, tune in tunes.items()}
    counts: Counter[str] = Counter()

    def record(stem: str, llm_result) -> None:
        annotated = core.build_annotation(
            tunes[stem], shas[stem], scorer_votes[stem], llm_result)
        core.write_annotated(annotated_dir / names[stem], annotated)
        status = annotated["key_annotation"]["status"]
        counts[status] += 1
        key = annotated["key"]
        extra = ""
        if annotated.get("section_keys"):
            extra = "  sections: " + ", ".join(
                f"{n}={d['tonic']} {d['mode']}"
                for n, d in annotated["section_keys"].items())
        print(f"  {stem:55s} {key['tonic']:>2s} {key['mode']:5s} [{status}]{extra}")

    return tunes, counts, record


def cmd_annotate(verified_dir: Path, annotated_dir: Path, *,
                 scorer_only: bool, limit: int | None,
                 interactive: bool = False, workers: int = 1) -> int:
    sweep_orphans(verified_dir, annotated_dir)
    paths = _tune_paths(verified_dir)
    pending = [p for p in paths if core.is_pending(p, annotated_dir / p.name)]
    if limit is not None:
        pending = pending[:limit]
    print(f"{len(paths)} tunes in {verified_dir.name}; {len(pending)} pending")
    refresh_derived_fields(annotated_dir)
    if not pending:
        if not scorer_only:
            refresh_stale_fingerprints(annotated_dir)
        return 0

    tunes, counts, record = _make_writer(pending, annotated_dir)

    if scorer_only:
        for stem in tunes:
            record(stem, LLMVoteError("llm pass not run (--scorer-only)"))
    else:
        from pipelines.chords.key_annotation import llm
        llm.run(tunes, force_interactive=interactive, workers=workers,
                on_result=record,
                state_path=_batch_state_path(annotated_dir))

    print("done: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    if not scorer_only:
        refresh_stale_fingerprints(annotated_dir)
    return 0


def cmd_reuse(verified_dir: Path, annotated_dir: Path, *,
              limit: int | None) -> int:
    """Refresh 05_annotated from 04_verified without re-running the LLM.

    For every tune whose annotated copy is out of date with its (edited)
    source, rebuild the annotated file from the new source while carrying the
    existing key decision forward — no scorer vote, no paid LLM call. Tunes
    with no annotation yet are skipped: they need a real annotation run.
    """
    sweep_orphans(verified_dir, annotated_dir)
    paths = _tune_paths(verified_dir)
    pending = [p for p in paths if core.is_pending(p, annotated_dir / p.name)]
    print(f"{len(paths)} tunes in {verified_dir.name}; {len(pending)} out of date")
    refresh_derived_fields(annotated_dir)
    reused = skipped = 0
    for path in pending:
        if limit is not None and reused >= limit:
            break
        ann_path = annotated_dir / path.name
        if not ann_path.exists():
            print(f"  {path.stem:55s} [skipped — no annotation yet]")
            skipped += 1
            continue
        old = core.read_json(ann_path)
        if "key" not in old or "key_annotation" not in old:
            print(f"  {path.stem:55s} [skipped — annotation incomplete]")
            skipped += 1
            continue
        source = core.read_json(path)
        annotated = core.carry_annotation(source, old, core.source_sha256(path))
        core.write_annotated(ann_path, annotated)
        reused += 1
        key = annotated["key"]
        status = annotated["key_annotation"].get("status", "?")
        print(f"  {path.stem:55s} {key['tonic']:>2s} {key['mode']:5s} "
              f"[{status}, reused]")
    print(f"done: {reused} refreshed from existing annotation, "
          f"{skipped} skipped (no annotation yet)")
    return 0


def cmd_resume_batch(verified_dir: Path, annotated_dir: Path,
                     batch_id: str) -> int:
    """Fetch (polling first if still running) a previously submitted batch
    and write its annotations — the recovery path when a batch run was
    interrupted after submission."""
    import anthropic

    from pipelines.chords.key_annotation import llm

    state_path = _batch_state_path(annotated_dir)
    if not batch_id:
        if not state_path.exists():
            print(f"error: no {state_path.name} on record — pass the batch id "
                  "explicitly: --resume-batch msgbatch_...", file=sys.stderr)
            return 1
        batch_id = core.read_json(state_path)["batch_id"]

    paths = _tune_paths(verified_dir)
    pending = [p for p in paths if core.is_pending(p, annotated_dir / p.name)]
    if not pending:
        print("nothing pending; batch results (if any) are no longer needed")
        if state_path.exists():
            state_path.unlink()
        return 0
    print(f"resuming batch {batch_id} ({len(pending)} pending tunes)")

    tunes, counts, record = _make_writer(pending, annotated_dir)
    results = llm.poll_batch(anthropic.Anthropic(), batch_id, set(tunes))
    for stem, result in results.items():
        record(stem, result)
    skipped = len(tunes) - len(results)
    if skipped:
        print(f"  {skipped} pending tune(s) were not in this batch; "
              "they stay pending for the next run")
    if state_path.exists():
        state_path.unlink()

    print("done: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    refresh_stale_fingerprints(annotated_dir)
    return 0


def cmd_set_key(annotated_dir: Path, stem: str, tonic: str, mode: str) -> int:
    path = annotated_dir / f"{stem}.json"
    if not path.exists():
        print(f"error: {path} does not exist — run the annotation pass first",
              file=sys.stderr)
        return 1
    annotated = core.read_json(path)
    core.update_annotation(annotated, tonic=tonic, mode=mode)
    core.write_annotated(path, annotated)
    key = annotated["key"]
    human = annotated["key_annotation"]["human"]
    print(f"{stem}: key set to {key['tonic']} {key['mode']}"
          f" (verified, corrected={human['corrected']})")
    proposals = annotated["key_annotation"].get("section_key_proposals")
    if proposals:
        print("  re-detected local keys under the new key — review in the "
              "key verifier app (accept or dismiss):")
        for name, d in proposals.items():
            print(f"    section {name}: {d['tonic']} {d['mode']}"
                  f" (margin {d.get('margin', 0):.2f})")
    if (annotated.get("harmonic_fingerprint") or {}).get("stale"):
        print("  fingerprint flagged stale; the next annotate_keys.py run "
              "refreshes it (key-pinned LLM call)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verified", default=str(VERIFIED_DIR),
                        help="source tune directory (read-only)")
    parser.add_argument("--annotated", default=str(ANNOTATED_DIR),
                        help="output directory")
    parser.add_argument("--limit", type=int, default=None,
                        help="annotate at most N pending tunes")
    parser.add_argument("--scorer-only", action="store_true",
                        help="skip the LLM voter; tunes land in needs_review "
                             "and are retried on the next full run")
    parser.add_argument("--reuse-annotation", action="store_true",
                        help="refresh 05_annotated from an edited 04_verified "
                             "without re-running the voters — carry each "
                             "existing key decision forward and skip tunes "
                             "with no annotation yet")
    parser.add_argument("--interactive", action="store_true",
                        help="force interactive messages.create calls even at "
                             ">= 50 pending tunes (skips the Batches API)")
    parser.add_argument("--workers", type=int, default=None,
                        help="parallel interactive LLM calls (default 4 with "
                             "--interactive, else 1)")
    parser.add_argument("--status", action="store_true",
                        help="print per-status counts and exit")
    parser.add_argument("--set-key", nargs=3,
                        metavar=("STEM", "TONIC", "MODE"),
                        help="scripted human override for one tune")
    parser.add_argument("--resume-batch", nargs="?", const="", default=None,
                        metavar="BATCH_ID",
                        help="fetch an interrupted Batches-API run and write "
                             "its annotations (id read from "
                             "data/chords/key_annotation_batch.json when "
                             "omitted)")
    args = parser.parse_args()

    verified_dir, annotated_dir = Path(args.verified), Path(args.annotated)
    if not verified_dir.exists():
        parser.error(f"verified directory not found: {verified_dir}")

    if args.set_key:
        stem, tonic, mode = args.set_key
        if mode not in ("major", "minor"):
            parser.error("mode must be 'major' or 'minor'")
        return cmd_set_key(annotated_dir, stem, tonic, mode)
    if args.status:
        return cmd_status(verified_dir, annotated_dir)
    if args.resume_batch is not None:
        return cmd_resume_batch(verified_dir, annotated_dir, args.resume_batch)
    if args.reuse_annotation:
        return cmd_reuse(verified_dir, annotated_dir, limit=args.limit)
    workers = args.workers if args.workers else (4 if args.interactive else 1)
    return cmd_annotate(verified_dir, annotated_dir,
                        scorer_only=args.scorer_only, limit=args.limit,
                        interactive=args.interactive, workers=workers)


if __name__ == "__main__":
    sys.exit(main())
