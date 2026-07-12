#!/usr/bin/env python3
"""Cross-check each tune's printed `form` string against its stored sections.

Usage
-----
    python pipelines/chords/tools/check_form.py [dir ...]
    # default dirs: data/chords/04_verified data/chords/03_wip

The printed form ("32 A A' B A") carries the prime information the mechanical
section keys (A, A1, B, A2) throw away. This tool aligns the form's per-strain
label sequences against the section groups (verse_* keys <-> the verse strain,
sN_* <-> strain sN, plain letters <-> the chorus) and reports:

  HARD  a real disagreement — a label/section count mismatch, an unstored
        strain repeat, or a section group with no matching form strain.
  SOFT  a review note — a verse form only recoverable from notation_notes
        prose, or not recoverable at all (labels then derived from keys).

For every tune it also prints the derived per-section labels (primes kept), the
data that feeds the displayer and the verifier.

Exit code 1 if any tune has a HARD mismatch, 0 otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root on path
from pipelines.chords.similarity.normalize import HARD, derive_labels  # noqa: E402

IGNORED_STEMS = {"verification_state", "run_report", "run_state"}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    repo = Path(__file__).resolve().parents[3]
    dirs = sys.argv[1:] or [str(repo / "data" / "chords" / "04_verified"),
                            str(repo / "data" / "chords" / "03_wip")]
    any_hard = False
    for dirname in dirs:
        print(f"===== {dirname} =====")
        n_files = hard_files = soft_files = 0
        for p in sorted(Path(dirname).glob("*.json")):
            if p.stem in IGNORED_STEMS or p.stem.endswith("_opus"):
                continue
            n_files += 1
            tune = json.loads(p.read_text("utf-8"))
            _struct, labels, warnings = derive_labels(tune)
            hard = [m for lv, m in warnings if lv == HARD]
            soft = [m for lv, m in warnings if lv != HARD]
            if not warnings:
                continue
            printed = tune.get("form")
            printed = printed.get("printed") if isinstance(printed, dict) else printed
            print(f"{p.name}  form={printed!r}")
            for m in hard:
                print(f"    HARD  {m}")
            for m in soft:
                print(f"    soft  {m}")
            print(f"    labels: {labels}")
            hard_files += bool(hard)
            soft_files += bool(soft) and not hard
        if hard_files:
            any_hard = True
        print(f"  ({n_files} files scanned; {hard_files} hard, "
              f"{soft_files} soft-only)")
    return 1 if any_hard else 0


if __name__ == "__main__":
    sys.exit(main())
