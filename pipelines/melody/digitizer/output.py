"""Assemble the final ABC file from skeleton headers + the model's body, and
write it to data/melody/03_wip, with debug artifacts alongside (plan §1)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .skeleton import Skeleton


@dataclass
class ReadResult:
    printed_key: str
    abc_body: str
    flags: list[dict]  # [{"bar": int, "reason": str}]
    cost: float = 0.0


def _headers_with_key(skeleton: Skeleton, printed_key: str) -> list[str]:
    """Skeleton headers, but with K: replaced by the model's printed signature."""
    lines = []
    for line in skeleton.header_lines:
        if line.startswith("K:") and printed_key:
            lines.append(f"K:{printed_key}")
        else:
            lines.append(line)
    return lines


def assemble_abc(skeleton: Skeleton, result: ReadResult) -> str:
    """Full ABC text: headers + body + `% flag:` lines for the scorer/reviewer."""
    headers = _headers_with_key(skeleton, result.printed_key)
    body = result.abc_body.strip("\n")
    parts = ["\n".join(headers), body]
    if result.flags:
        flag_lines = [
            f"% flag: {f['bar']} {f.get('reason', '').strip()}".rstrip()
            for f in sorted(result.flags, key=lambda f: f.get("bar", 0))
        ]
        parts.append("\n".join(flag_lines))
    return "\n".join(parts) + "\n"


def wip_path(cfg: Config, stem: str) -> Path:
    return cfg.wip_dir / f"{stem}.abc"


def write_tune(cfg: Config, stem: str, abc_text: str) -> Path:
    cfg.wip_dir.mkdir(parents=True, exist_ok=True)
    path = wip_path(cfg, stem)
    path.write_text(abc_text, encoding="utf-8")
    return path


def write_debug(cfg: Config, stem: str, name: str, payload: dict | str) -> Path:
    d = cfg.debug_dir / stem
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return path
