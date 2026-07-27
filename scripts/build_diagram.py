"""Draw the trace-shape diagram the terminal footage cannot show.

    uv run --group video python scripts/build_diagram.py

`build_video.py` runs this itself before filming, so it rarely needs to be called by hand.

The whole argument for exporting to SigNoz is that the two arms of a pair hang off the same
parent, so the difference between them is the *shape* of the subtree rather than a number
you have to look up. A table of counts does not show a shape. This does.

Every number here is read from the published cassette at draw time, so the diagram cannot
drift from the evidence the rest of the video is about.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))

from morrow.adapters.cassette.checks import parse_run
from morrow.adapters.cassette.store import read_cassette
from morrow.adapters.cassette.verify import verify_path
from morrow.domain.events import EventKind
from morrow.domain.metrics import ComponentName, Variant

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".video" / "out" / "diagram.png"
CASSETTE = ROOT / "cassettes" / "treatment-replace-cache"

W, H = 1920, 1080
BG = (30, 30, 46)          # Catppuccin Mocha base — matches the terminal footage
INK = (205, 214, 244)
DIM = (127, 132, 156)
BASE = (137, 180, 250)     # blue: baseline
CAND = (243, 139, 168)     # red: candidate
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def _pair_zero() -> dict[str, dict[str, int]]:
    """The counts for pair 0's two runs, straight from the published cassette."""
    outcome = verify_path(CASSETTE)
    assert outcome.manifest is not None
    files = read_cassette(CASSETTE).files

    out: dict[str, dict[str, int]] = {}
    for entry in outcome.manifest.adopted_runs:
        if entry.pair_id != 0:
            continue
        parsed = parse_run(entry, files)
        counts = parsed.counts()  # type: ignore[union-attr]
        exported = [
            event
            for event in parsed.events  # type: ignore[union-attr]
            if event.kind is not EventKind.OPAQUE
        ]
        lifecycle = {EventKind.SESSION_START, EventKind.COMPLETION}
        actions = sum(1 for event in exported if event.kind not in lifecycle)
        out[entry.variant.value] = {
            "run_id": entry.run_id,
            "files": int(counts[ComponentName.FILES_READ_DISTINCT]),
            "tests": int(counts[ComponentName.TEST_CYCLES]),
            "churn": int(counts[ComponentName.FINAL_CHURN]),
            "spans": len(exported),
            "actions": actions,
        }
    return out


def _dots(draw: ImageDraw.ImageDraw, x: int, y: int, count: int, colour: tuple) -> int:
    """A block of action spans, drawn as dots. Returns the bottom edge."""
    per_row, radius, gap = 16, 5, 17
    for index in range(count):
        cx = x + (index % per_row) * gap
        cy = y + (index // per_row) * gap
        draw.ellipse([cx, cy, cx + radius, cy + radius], fill=colour)
    rows = (count + per_row - 1) // per_row
    return y + rows * gap


def main() -> None:
    data = _pair_zero()
    base, cand = data[Variant.BASELINE.value], data[Variant.CANDIDATE.value]

    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    h1 = _font("DejaVuSans-Bold.ttf", 40)
    mono = _font("DejaVuSansMono.ttf", 26)
    mono_small = _font("DejaVuSansMono.ttf", 22)
    label = _font("DejaVuSans.ttf", 24)

    draw.text((70, 60), "One trace per experiment", font=h1, fill=INK)
    draw.text(
        (70, 116),
        "the two arms of a pair hang off the same parent, so the difference is the shape",
        font=label,
        fill=DIM,
    )

    # root
    draw.text((70, 200), "morrow.experiment", font=mono, fill=INK)
    draw.line([(84, 236), (84, 276)], fill=DIM, width=2)
    draw.text((110, 264), "morrow.pair  0", font=mono, fill=INK)
    draw.line([(124, 300), (124, 356)], fill=DIM, width=2)
    draw.line([(124, 356), (1024, 356)], fill=DIM, width=2)

    for column, (arm, colour, info) in enumerate(
        (("baseline", BASE, base), ("candidate", CAND, cand))
    ):
        x = 124 + column * 900
        draw.line([(x, 356), (x, 400)], fill=DIM, width=2)
        draw.text((x, 408), f"morrow.run   {arm}  {info['run_id']}", font=mono, fill=colour)

        rows = [
            f"files_read_distinct   {info['files']:>4}",
            f"test_cycles           {info['tests']:>4}",
            f"final_churn           {info['churn']:>4}",
        ]
        for line, text in enumerate(rows):
            draw.text((x + 24, 456 + line * 34), text, font=mono_small, fill=INK)

        draw.text(
            (x + 24, 578),
            f"{info['spans']} spans   ({info['actions']} actions + session and completion)",
            font=label,
            fill=DIM,
        )
        _dots(draw, x + 24, 620, info["spans"], colour)

        # The counts alone flatten the thing the trace makes obvious, so churn also gets a
        # bar: 129 against 487 is the difference this pair is actually about.
        width = int(info["churn"] / max(base["churn"], cand["churn"]) * 760)
        draw.rectangle([x + 24, 740, x + 24 + width, 786], fill=colour)
        draw.text((x + 24, 796), f"{info['churn']} lines changed", font=label, fill=INK)

    ratio = (cand["churn"] + 1) / (base["churn"] + 1)
    draw.text(
        (70, 940),
        f"same task, same model, same limits — churn ratio {ratio:.4f}",
        font=label,
        fill=INK,
    )
    draw.text(
        (70, 984),
        "descriptive only: this experiment was invalidated by its null control",
        font=label,
        fill=DIM,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT)
    print(f"{OUT}  ({base['spans']} vs {cand['spans']} spans)")


if __name__ == "__main__":
    main()
