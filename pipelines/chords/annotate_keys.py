#!/usr/bin/env python3
"""Phase 0 key annotation: data/chords/04_verified -> data/chords/05_annotated.

Usage
-----
    python pipelines/chords/annotate_keys.py                 # annotate everything pending
    python pipelines/chords/annotate_keys.py --status        # per-status counts, no work
    python pipelines/chords/annotate_keys.py --scorer-only   # skip the LLM voter (dev/offline)
    python pipelines/chords/annotate_keys.py --set-key <stem> <tonic> <major|minor>

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
    return 0


def cmd_annotate(verified_dir: Path, annotated_dir: Path, *,
                 scorer_only: bool, limit: int | None,
                 interactive: bool = False, workers: int = 1) -> int:
    paths = _tune_paths(verified_dir)
    pending = [p for p in paths if core.is_pending(p, annotated_dir / p.name)]
    if limit is not None:
        pending = pending[:limit]
    print(f"{len(paths)} tunes in {verified_dir.name}; {len(pending)} pending")
    if not pending:
        return 0

    tunes = {p.stem: core.read_json(p) for p in pending}
    scorer_votes = {stem: score_tune(tune) for stem, tune in tunes.items()}

    if scorer_only:
        llm_results = {stem: LLMVoteError("llm pass not run (--scorer-only)")
                       for stem in tunes}
    else:
        from pipelines.chords.key_annotation import llm
        llm_results = llm.run(tunes, force_interactive=interactive,
                              workers=workers)

    counts: Counter[str] = Counter()
    for path in pending:
        stem = path.stem
        annotated = core.build_annotation(
            tunes[stem], core.source_sha256(path),
            scorer_votes[stem], llm_results[stem])
        core.write_annotated(annotated_dir / path.name, annotated)
        status = annotated["key_annotation"]["status"]
        counts[status] += 1
        key = annotated["key"]
        extra = ""
        if annotated.get("section_keys"):
            extra = "  sections: " + ", ".join(
                f"{n}={d['tonic']} {d['mode']}"
                for n, d in annotated["section_keys"].items())
        print(f"  {stem:55s} {key['tonic']:>2s} {key['mode']:5s} [{status}]{extra}")

    print("done: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
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
    workers = args.workers if args.workers else (4 if args.interactive else 1)
    return cmd_annotate(verified_dir, annotated_dir,
                        scorer_only=args.scorer_only, limit=args.limit,
                        interactive=args.interactive, workers=workers)


if __name__ == "__main__":
    sys.exit(main())
