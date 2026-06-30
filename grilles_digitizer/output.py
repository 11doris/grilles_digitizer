"""Deterministic output paths and atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import Config
from .manifest import WorkUnit


def json_path(config: Config, unit: WorkUnit) -> Path:
    return config.out_dir / f"{unit.stem}.json"


def error_path(config: Config, unit: WorkUnit) -> Path:
    return config.out_dir / f"{unit.stem}.error.json"


def _atomic_write(path: Path, data: str) -> None:
    """Write to a temp file in the same dir, then rename — a kill mid-write cannot
    leave a half-written 'present but invalid' file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_tune(config: Config, unit: WorkUnit, obj: dict) -> None:
    """Write the accepted tune as minified JSON, and clear any stale error stub."""
    _atomic_write(json_path(config, unit), json.dumps(obj, ensure_ascii=False))
    stale = error_path(config, unit)
    if stale.exists():
        stale.unlink()


def write_error_stub(
    config: Config, unit: WorkUnit, *, attempts: int, last_error: str, raw_excerpt: str
) -> None:
    stub = {
        "current_file": unit.current_file,
        "page": unit.page,
        "title": unit.title,
        "attempts": attempts,
        "last_error": last_error,
        "raw_excerpt": raw_excerpt[:500],
    }
    _atomic_write(error_path(config, unit), json.dumps(stub, ensure_ascii=False, indent=2))
