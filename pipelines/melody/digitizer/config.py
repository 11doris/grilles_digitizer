"""Run configuration — resolved paths and options for one melody run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

SOURCE_CONSTANT = "Anthologie des grilles de jazz"

_REPO = Path(__file__).resolve().parents[3]

# Models that reject sampling parameters (temperature/top_p/top_k) — passing
# temperature to these returns a 400 (same list as the chords digitizer).
_NO_SAMPLING_MODELS = {
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-fable-5",
    "claude-mythos-5",
}


@dataclass(frozen=True)
class Config:
    repo: Path = _REPO
    crops_dir: Path = _REPO / "data" / "melody" / "01_crops"
    title_index: Path = _REPO / "data" / "title_index.csv"
    chords_dir: Path = _REPO / "data" / "chords" / "05_annotated"
    overrides_dir: Path = _REPO / "data" / "melody" / "overrides"
    wip_dir: Path = _REPO / "data" / "melody" / "03_wip"
    verified_dir: Path = _REPO / "data" / "melody" / "04_verified"
    debug_dir: Path = _REPO / "data" / "melody" / "debug"
    leadsheets_dir: Path = _REPO / "data" / "melody" / "leadsheets"
    abcjs_path: Path = _REPO / "apps" / "displayer" / "vendor" / "abcjs-basic-min.js"

    model: str = "claude-opus-4-8"
    workers: int = 2
    retries: int = 3
    # Hard budget guard: < $0.20/tune. 2 reads + repair + 1 re-ask + 1 spare.
    max_calls_per_tune: int = 5
    max_output_tokens: int = 2500
    # Melody crops are clean ~2274x2500 1-bit scans; pitch reading needs the
    # detail, so keep the long edge near native (Opus 4.8 high-res vision) and
    # do NOT thicken ink by default (dilation blurs line-vs-space).
    max_long_edge: int = 2000
    dilate: int = 0
    only: str | None = None  # restrict to one melody stem (debugging)
    interactive: bool = False
    debug: bool = False

    @property
    def supports_temperature(self) -> bool:
        return self.model not in _NO_SAMPLING_MODELS

    @property
    def state_path(self) -> Path:
        return self.wip_dir / "run_state.jsonl"

    @property
    def report_path(self) -> Path:
        return self.wip_dir / "run_report.json"
