#!/usr/bin/env python3
"""Convenience entry point: `python transcribe.py --crops crops/ --out tunes/`."""

from grilles_digitizer.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
