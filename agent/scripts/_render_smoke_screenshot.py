"""Render the captured T-B-043 smoke log as a terminal-style PNG.

T-B-043 — Money Shot generator. Reads the smoke's stdout capture
(``reports/sprint13/boot_smoke_stdout.txt``) and rasterises it into
``reports/sprint13/screenshots/real_loop_sse_stream.png`` at ≥ 1280 px
wide, matching the brief's SUBMISSION visibility requirement.

Renders with PIL only — no headless browser, no external image lib.
The rasterised result is a high-contrast terminal aesthetic (dark
background + green / yellow / cyan accents) so the SSE event lines pop
in the submission feed.

Usage::

    python agent/scripts/sprint13_boot_smoke.py   # produces the .txt
    python agent/scripts/_render_smoke_screenshot.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Locked: ≥ 1280 px wide is the brief's gate. We render at 1600 to leave
# headroom for the longest line (the SANDBOX_STATE_DIR Windows tempdir).
TARGET_WIDTH = 1600
PADDING_X = 32
PADDING_Y = 24
LINE_HEIGHT = 22
FONT_SIZE = 16

# Terminal-ish colour palette (sRGB).
BG_COLOR = (12, 16, 24)        # near-black with slight blue
FG_COLOR = (220, 226, 232)     # off-white
HEADER_COLOR = (97, 175, 239)  # cyan
VERDICT_PASS_COLOR = (152, 195, 121)   # green
VERDICT_FAIL_COLOR = (224, 108, 117)   # red
DIM_COLOR = (110, 122, 138)    # subtle metadata
KEY_COLOR = (229, 192, 123)    # yellow — env-var keys, kind=loop_boot


def _find_mono_font() -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Pick a monospaced font that exists on the host."""
    candidates = [
        # Windows
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\lucon.ttf",
        # macOS
        "/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, FONT_SIZE)
        except OSError:
            continue
    # PIL bundles a default bitmap font — last-resort fallback so the
    # smoke can still emit a Money Shot on a stripped-down image.
    return ImageFont.load_default()


def _classify(line: str) -> tuple[str, tuple[int, int, int]]:
    """Return ``(rendered_line, fg_color)`` for one input line."""
    stripped = line.rstrip("\n")
    # Section dividers (=== ... ===).
    if stripped.startswith("=== VERDICT"):
        return stripped, HEADER_COLOR
    if stripped.startswith("==="):
        return stripped, HEADER_COLOR
    # The verdict itself.
    if stripped.startswith("PASS —"):
        return stripped, VERDICT_PASS_COLOR
    if stripped.startswith("FAIL —"):
        return stripped, VERDICT_FAIL_COLOR
    # SSE event lines (highlight loop_boot).
    if "kind=loop_boot" in stripped:
        return stripped, KEY_COLOR
    # NO_BET / BET decision lines.
    if "kind=NO_BET" in stripped or "kind=BET" in stripped:
        return stripped, (198, 165, 213)   # purple/lavender
    # Indented sub-info.
    if stripped.startswith("  "):
        return stripped, DIM_COLOR
    return stripped, FG_COLOR


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    log_path = repo_root / "reports" / "sprint13" / "boot_smoke_stdout.txt"
    out_path = (
        repo_root
        / "reports"
        / "sprint13"
        / "screenshots"
        / "real_loop_sse_stream.png"
    )
    if not log_path.exists():
        print(
            f"FATAL: smoke stdout capture not found at {log_path} — "
            f"run sprint13_boot_smoke.py first",
            file=sys.stderr,
        )
        return 2

    raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    if not raw_lines:
        print("FATAL: smoke stdout file is empty", file=sys.stderr)
        return 2

    font = _find_mono_font()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    height = PADDING_Y * 2 + LINE_HEIGHT * len(raw_lines)
    img = Image.new("RGB", (TARGET_WIDTH, height), BG_COLOR)
    draw = ImageDraw.Draw(img)

    y = PADDING_Y
    for raw in raw_lines:
        text, color = _classify(raw)
        draw.text((PADDING_X, y), text, fill=color, font=font)
        y += LINE_HEIGHT

    img.save(out_path, format="PNG", optimize=True)
    print(f"wrote {out_path} ({img.size[0]}x{img.size[1]} px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
