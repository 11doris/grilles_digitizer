"""Lead-sheet rendering + render check (plan §5).

Builds a standalone HTML page around the vendored abcjs
(apps/displayer/vendor/abcjs-basic-min.js), screenshots it with the
repo-venv Playwright on the Edge channel (headless raw Edge as fallback),
and asserts the result is non-blank with a plausible number of systems.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .config import Config

_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="{abcjs_uri}"></script>
<style>body{{background:#fff;margin:20px;font-family:Georgia,serif}} #paper{{width:1150px}}</style>
</head>
<body>
<div id="paper"></div>
<script>
const abc = {abc_js};
ABCJS.renderAbc("paper", abc, {{ staffwidth: 1100, wrap: undefined, scale: 1.1 }});
</script>
</body>
</html>
"""

_EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def write_leadsheet(abc_text: str, html_path: Path, cfg: Config) -> Path:
    """Write the abcjs page for one tune; returns html_path."""
    import json as _json

    title = "lead sheet"
    for line in abc_text.splitlines():
        if line.startswith("T:"):
            title = line[2:].strip()
            break
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(_PAGE.format(
        title=html.escape(title),
        abcjs_uri=cfg.abcjs_path.resolve().as_uri(),
        abc_js=_json.dumps(abc_text),
    ), encoding="utf-8")
    return html_path


def screenshot(html_path: Path, png_path: Path,
               width: int = 1300, height: int = 1600) -> Path:
    """Screenshot via Playwright (channel=msedge); raw headless Edge fallback."""
    png_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(html_path.resolve().as_uri())
            page.wait_for_timeout(300)
            page.screenshot(path=str(png_path), full_page=True)
            browser.close()
        return png_path
    except Exception:
        pass  # fall back to raw headless Edge
    edge = next((p for p in _EDGE_CANDIDATES if Path(p).is_file()), None)
    if edge is None:
        raise RuntimeError("no Playwright msedge channel and no msedge.exe found")
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(
            [edge, "--headless=new", "--disable-gpu",
             "--allow-file-access-from-files", f"--user-data-dir={profile}",
             f"--window-size={width},{height}",
             f"--screenshot={png_path.resolve()}",
             html_path.resolve().as_uri()],
            check=True, capture_output=True, timeout=60,
        )
    return png_path


def render_check(png_path: Path, min_systems: int = 1) -> tuple[bool, str]:
    """Non-blank + at least `min_systems` horizontal ink bands."""
    a = np.asarray(Image.open(png_path).convert("L"))
    ink = a < 128
    frac = ink.mean()
    if frac < 0.002:
        return False, f"page is blank (ink fraction {frac:.4f})"
    dark = ink.sum(axis=1)
    rows = np.where(dark > 0.25 * dark.max())[0]
    bands = 0
    prev = None
    for y in rows:
        if prev is None or y - prev > 30:
            bands += 1
        prev = y
    if bands < min_systems:
        return False, f"only {bands} staff bands rendered, expected >= {min_systems}"
    return True, f"ok ({bands} bands, ink {frac:.3f})"


def render_tune(abc_text: str, stem: str, cfg: Config,
                min_systems: int = 1) -> tuple[Path, Path, bool, str]:
    """Convenience: HTML + screenshot + check for one tune."""
    html_path = write_leadsheet(abc_text, cfg.leadsheets_dir / f"{stem}.html", cfg)
    png_path = cfg.leadsheets_dir / f"{stem}.png"
    screenshot(html_path, png_path)
    ok, reason = render_check(png_path, min_systems=min_systems)
    return html_path, png_path, ok, reason
