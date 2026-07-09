"""Run configuration — the resolved set of options for one transcription run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SOURCE_CONSTANT = "Anthologie des grilles de jazz"

# Models that reject sampling parameters (temperature/top_p/top_k) — see the
# Anthropic API: on these, passing temperature returns a 400.
_NO_SAMPLING_MODELS = {
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-fable-5",
    "claude-mythos-5",
}

@dataclass(frozen=True)
class Config:
    crops_dir: Path
    # Optional: only consulted to restore original title spellings (apostrophes
    # etc.) that filename slugs cannot encode. A missing file is fine.
    manifest: Path
    out_dir: Path
    model: str = "claude-opus-4-8"
    workers: int = 1
    retries: int = 3
    dilate: int = 1
    max_long_edge: int = 1100
    # A cap, not a charge — billing is by tokens actually generated. The spec's
    # ~1200 truncates dense/multi-strain grids (60+ expanded bars), so default higher;
    # normal tunes finish well under this, so it costs nothing extra.
    max_output_tokens: int = 2500
    page_range: tuple[int, int] | None = None
    delay: float = 0.0
    only: str | None = None  # restrict to a single current_file (debugging)
    # Randomly pick at most `sample` crops whose tune is not yet decoded into
    # `out_dir` (i.e. still to do). None = no sampling (process all).
    sample: int | None = None
    seed: int | None = None  # RNG seed for --sample; None = nondeterministic
    debug: bool = False

    @property
    def supports_temperature(self) -> bool:
        return self.model not in _NO_SAMPLING_MODELS

    @property
    def state_path(self) -> Path:
        return self.out_dir / "run_state.jsonl"

    @property
    def report_path(self) -> Path:
        return self.out_dir / "run_report.json"
