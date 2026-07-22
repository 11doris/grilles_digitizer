"""Command-line entry point. See README for the full option reference."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anthropic

from .config import Config
from .runner import run
from .vlm import MissingCredentials

_REPO = Path(__file__).resolve().parents[3]  # repo root


def _page_range(value: str) -> tuple[int, int]:
    try:
        lo, hi = value.split(":")
        return int(lo), int(hi)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("page-range must be A:B (e.g. 7:120)") from exc


def _normalize_crop_name(entry: str) -> str:
    """A requested crop as a `current_file`: strip whitespace, drop a leading
    `#` comment/blank (returns ""), and append `.png` if not already present."""
    entry = entry.strip()
    if not entry or entry.startswith("#"):
        return ""
    return entry if entry.lower().endswith(".png") else f"{entry}.png"


def _load_file_list(path: Path) -> tuple[str, ...]:
    """Read a newline-delimited list of crop stems/filenames (one per line;
    blank lines and `#` comments ignored; `.png` optional), preserving order
    and de-duplicating."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise argparse.ArgumentTypeError(f"cannot read --files list {path}: {exc}") from exc
    seen: dict[str, None] = {}  # ordered set
    for line in lines:
        name = _normalize_crop_name(line)
        if name:
            seen.setdefault(name, None)
    if not seen:
        raise argparse.ArgumentTypeError(f"--files list {path} has no usable entries")
    return tuple(seen)


def _parse_args(argv: list[str] | None) -> Config:
    p = argparse.ArgumentParser(
        prog="transcribe",
        description="Transcribe cropped jazz chord grids into JSON via a VLM.",
    )
    p.add_argument("--crops", type=Path, default=_REPO / "data" / "chords" / "01_crops",
                   help="crops directory")
    p.add_argument(
        "--manifest", type=Path, default=None,
        help="optional manifest.csv used only to restore original title spellings "
             "(default: <crops>/manifest.csv; missing is fine)",
    )
    p.add_argument("--out", type=Path, default=_REPO / "data" / "chords" / "02_raw",
                   help="output directory")
    p.add_argument("--model", default="claude-opus-4-8", help="VLM model id")
    p.add_argument("--workers", type=int, default=1, help="parallel calls (remote API only)")
    p.add_argument("--retries", type=int, default=3, help="per-unit validation retries")
    p.add_argument("--dilate", type=int, default=1, help="ink-thickening iterations (0=off)")
    p.add_argument("--max-long-edge", type=int, default=1100, help="downscale long edge to")
    p.add_argument("--max-output-tokens", type=int, default=4000, help="output token cap (billed by actual use, not the cap)")
    p.add_argument("--page-range", type=_page_range, default=None, help="limit to pages A:B")
    p.add_argument("--delay", type=float, default=0.0, help="seconds to sleep between units")
    p.add_argument("--only", default=None, help="restrict to one current_file (debugging)")
    p.add_argument(
        "--files", type=Path, default=None, metavar="LIST",
        help="restrict to the crops named in LIST, a text file with one crop "
             "stem/filename per line (.png optional; blank lines and #-comments "
             "ignored). Pair with --batch to batch a list smaller than 50.",
    )
    p.add_argument(
        "--sample", type=int, default=None, metavar="N",
        help="randomly select at most N crops whose tune is not yet decoded into --out",
    )
    p.add_argument("--seed", type=int, default=None, help="RNG seed for --sample (reproducible selection)")
    p.add_argument(
        "--interactive", action="store_true",
        help="force per-call mode even at >= 50 pending crops (batch mode is "
             "automatic otherwise: Batches API, 50%% price, results within hours)",
    )
    p.add_argument(
        "--batch", action="store_true",
        help="force batch mode even below 50 pending crops (Batches API is "
             "always 50%% price; the threshold is only about latency). Ignored "
             "when --interactive is also set.",
    )
    p.add_argument("--debug", action="store_true", help="verbose errors")
    args = p.parse_args(argv)

    manifest = args.manifest or (args.crops / "manifest.csv")
    files = _load_file_list(args.files) if args.files is not None else None
    return Config(
        crops_dir=args.crops,
        manifest=manifest,
        out_dir=args.out,
        model=args.model,
        workers=max(1, args.workers),
        retries=max(1, args.retries),
        dilate=max(0, args.dilate),
        max_long_edge=args.max_long_edge,
        max_output_tokens=args.max_output_tokens,
        page_range=args.page_range,
        delay=max(0.0, args.delay),
        only=args.only,
        files=files,
        sample=None if args.sample is None else max(0, args.sample),
        seed=args.seed,
        interactive=args.interactive,
        force_batch=args.batch,
        debug=args.debug,
    )


def main(argv: list[str] | None = None) -> int:
    config = _parse_args(argv)
    if not config.crops_dir.is_dir():
        print(f"error: crops dir not found: {config.crops_dir}", file=sys.stderr)
        return 2
    try:
        run(config)
    except MissingCredentials as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        print(
            f"error: authentication rejected by the API ({exc.__class__.__name__}). "
            "Check that ANTHROPIC_API_KEY is valid and has access to the model.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
