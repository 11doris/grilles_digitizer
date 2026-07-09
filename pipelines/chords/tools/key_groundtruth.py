#!/usr/bin/env python3
"""Key ground truth (tune_similarity_spec §3.7): export and scorer check.

Usage
-----
    python pipelines/chords/tools/key_groundtruth.py --export
        Write data/chords/eval/key_groundtruth.json from the human-VERIFIED
        tunes in data/chords/05_annotated (merging into any existing file;
        existing entries win over re-exports only if --force is not given).
        Only status "verified" qualifies — the ground truth is human
        judgment, so machine-only statuses (agreed/needs_review) are skipped.

    python pipelines/chords/tools/key_groundtruth.py --check
        Run the deterministic scorer alone against the ground truth and
        report accuracy. Exit code 1 below the 80% acceptance bar. This is
        the regression gate to run whenever scorer weights change.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO))

from pipelines.chords.similarity.normalize import pitch_class  # noqa: E402

VERIFIED_DIR = _REPO / "data" / "chords" / "04_verified"
ANNOTATED_DIR = _REPO / "data" / "chords" / "05_annotated"
GROUNDTRUTH = _REPO / "data" / "chords" / "eval" / "key_groundtruth.json"

ACCEPTANCE = 0.80  # spec §3.7: scorer alone must reach 80% on the labeled set


def _load_groundtruth() -> dict:
    if GROUNDTRUTH.exists():
        return json.loads(GROUNDTRUTH.read_text("utf-8"))
    return {}


def cmd_export(force: bool) -> int:
    truth = _load_groundtruth()
    added = kept = 0
    for path in sorted(ANNOTATED_DIR.glob("*.json")):
        doc = json.loads(path.read_text("utf-8"))
        if (doc.get("key_annotation") or {}).get("status") != "verified":
            continue
        entry = {"tonic": doc["key"]["tonic"], "mode": doc["key"]["mode"]}
        if path.stem in truth and not force:
            kept += 1
            continue
        truth[path.stem] = entry
        added += 1
    if not truth:
        print("no verified tunes yet — verify keys in apps/key_verifier first")
        return 1
    GROUNDTRUTH.parent.mkdir(parents=True, exist_ok=True)
    GROUNDTRUTH.write_text(
        json.dumps(dict(sorted(truth.items())), indent=2, ensure_ascii=False) + "\n",
        "utf-8")
    print(f"{GROUNDTRUTH.relative_to(_REPO)}: {len(truth)} entries "
          f"({added} written, {kept} pre-existing kept)")
    return 0


def cmd_check() -> int:
    from pipelines.chords.key_annotation.scorer import score_tune
    truth = _load_groundtruth()
    if not truth:
        print(f"{GROUNDTRUTH} is missing or empty — nothing to check")
        return 1
    correct, wrong = 0, []
    for stem, expected in sorted(truth.items()):
        src = VERIFIED_DIR / f"{stem}.json"
        if not src.exists():
            print(f"  warning: {stem} in ground truth but not in 04_verified")
            continue
        vote = score_tune(json.loads(src.read_text("utf-8")))
        if (pitch_class(vote.tonic) == pitch_class(expected["tonic"])
                and vote.mode == expected["mode"]):
            correct += 1
        else:
            wrong.append((stem, expected, vote))
    total = correct + len(wrong)
    for stem, expected, vote in wrong:
        print(f"  WRONG {stem}: scorer {vote.tonic} {vote.mode} "
              f"(margin {vote.margin:.2f}) vs truth {expected['tonic']} {expected['mode']}")
    acc = correct / total if total else 0.0
    print(f"scorer alone: {correct}/{total} correct = {acc:.1%} "
          f"(acceptance: >= {ACCEPTANCE:.0%})")
    return 0 if acc >= ACCEPTANCE else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", action="store_true",
                       help="write ground truth from verified annotations")
    group.add_argument("--check", action="store_true",
                       help="report scorer-alone accuracy against ground truth")
    parser.add_argument("--force", action="store_true",
                        help="with --export: overwrite existing entries")
    args = parser.parse_args()
    return cmd_export(args.force) if args.export else cmd_check()


if __name__ == "__main__":
    sys.exit(main())
