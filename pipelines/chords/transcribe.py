#!/usr/bin/env python3
"""Convenience entry point (chords pipeline, stage 2). Defaults read
data/chords/01_crops/ and write data/chords/02_raw/, both resolved from the repo root:
`python pipelines/chords/transcribe.py`.
`python pipelines/chords/transcribe.py --only 207_03_IT_MIGHT_AS_WELL_BE_SPRING.png --debug`
`python pipelines/chords/transcribe.py --sample 10 --seed 42 --debug`
`python pipelines/chords/transcribe.py --files my_list.txt --batch`  (batch a <50 list at 50% price)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for direct invocation

from pipelines.chords.digitizer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
