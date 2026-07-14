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
        n_files = 0
        flagged = []  # (has_hard, name, printed, hard, soft, labels)
        for p in sorted(Path(dirname).glob("*.json")):
            if p.stem in IGNORED_STEMS or p.stem.endswith("_opus"):
                continue
            n_files += 1
            tune = json.loads(p.read_text("utf-8"))
            _struct, labels, warnings = derive_labels(tune)
            if not warnings:
                continue
            hard = [m for lv, m in warnings if lv == HARD]
            soft = [m for lv, m in warnings if lv != HARD]
            printed = tune.get("form")
            printed = printed.get("printed") if isinstance(printed, dict) else printed
            flagged.append((bool(hard), p.name, printed, hard, soft, labels))

        # Hard cases first, then soft-only; each group alphabetical by name.
        flagged.sort(key=lambda f: (not f[0], f[1]))
        hard_files = sum(1 for f in flagged if f[0])
        soft_files = len(flagged) - hard_files
        last_group = None
        for has_hard, name, printed, hard, soft, labels in flagged:
            group = "HARD" if has_hard else "SOFT"
            if group != last_group:
                print(f"\n----- {group} "
                      f"({hard_files if has_hard else soft_files}) -----")
                last_group = group
            print(f"{name}  form={printed!r}")
            for m in hard:
                print(f"    HARD  {m}")
            for m in soft:
                print(f"    soft  {m}")
            print(f"    labels: {labels}")
        if hard_files:
            any_hard = True
        print(f"\n  ({n_files} files scanned; {hard_files} hard, "
              f"{soft_files} soft-only)")
    return 1 if any_hard else 0


if __name__ == "__main__":
    sys.exit(main())
