#!/usr/bin/env python3
"""Convenience entry point: 
`python transcribe.py --crops crops/ --out tunes/`.
`python transcribe.py --crops crops/ --out tunes/ --only 207_03_IT_MIGHT_AS_WELL_BE_SPRING.png --debug`
`python transcribe.py --crops crops/ --out tunes/ --sample 10 --seed 42 --debug`
"""

from grilles_digitizer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
