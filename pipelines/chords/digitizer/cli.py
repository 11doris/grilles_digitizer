"""Command-line entry point. See README for the full option reference."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anthropic

from .config import Config
from .runner import run
from .vlm import MissingCredentials


def _page_range(value: str) -> tuple[int, int]:
    try:
        lo, hi = value.split(":")
        return int(lo), int(hi)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("page-range must be A:B (e.g. 7:120)") from exc


def _parse_args(argv: list[str] | None) -> Config:
    p = argparse.ArgumentParser(
        prog="transcribe",
        description="Transcribe cropped jazz chord grids into JSON via a VLM.",
    )
    p.add_argument("--crops", type=Path, default=Path("crops"), help="crops directory")
    p.add_argument(
        "--manifest", type=Path, default=None,
        help="manifest.csv (default: <crops>/manifest.csv)",
    )
    p.add_argument("--out", type=Path, default=Path("tunes"), help="output directory")
    p.add_argument("--model", default="claude-opus-4-8", help="VLM model id")
    p.add_argument("--workers", type=int, default=1, help="parallel calls (remote API only)")
    p.add_argument("--retries", type=int, default=3, help="per-unit validation retries")
    p.add_argument("--dilate", type=int, default=1, help="ink-thickening iterations (0=off)")
    p.add_argument("--max-long-edge", type=int, default=1100, help="downscale long edge to")
    p.add_argument("--max-output-tokens", type=int, default=2500, help="output token cap (billed by actual use, not the cap)")
    p.add_argument("--page-range", type=_page_range, default=None, help="limit to pages A:B")
    p.add_argument("--delay", type=float, default=0.0, help="seconds to sleep between units")
    p.add_argument("--only", default=None, help="restrict to one current_file (debugging)")
    p.add_argument(
        "--sample", type=int, default=None, metavar="N",
        help="randomly select at most N crops whose tune is not yet decoded into --out",
    )
    p.add_argument("--seed", type=int, default=None, help="RNG seed for --sample (reproducible selection)")
    p.add_argument("--debug", action="store_true", help="verbose errors")
    args = p.parse_args(argv)

    manifest = args.manifest or (args.crops / "manifest.csv")
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
        sample=None if args.sample is None else max(0, args.sample),
        seed=args.seed,
        debug=args.debug,
    )


def main(argv: list[str] | None = None) -> int:
    config = _parse_args(argv)
    if not config.crops_dir.is_dir():
        print(f"error: crops dir not found: {config.crops_dir}", file=sys.stderr)
        return 2
    if not config.manifest.is_file():
        print(f"error: manifest not found: {config.manifest}", file=sys.stderr)
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
