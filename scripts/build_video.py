"""Build the demo video end to end: narration, terminal footage, and the final cut.

    uv run python scripts/build_video.py           # everything
    uv run python scripts/build_video.py 01 05     # just those shots, for iteration

Nothing here is recorded by hand. Each shot is a `vhs` tape (real commands, really
executed, typed on camera) plus a line of narration; the narration is synthesised, its
duration measured, and the tape's trailing pause stretched to match so audio and picture
end together. The shots are then concatenated.

That matters beyond convenience: the commands in the video are the commands in this file,
so the footage cannot drift from what the tool actually does. If a command starts failing,
the build fails rather than quietly shipping stale footage.

Requires `vhs` (with `ttyd`), `ffmpeg`, and `edge-tts` on PATH.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".video"
OUT = BUILD / "out"

#: A neutral, unhurried narrator. Deliberately not an enthusiastic one — the video's whole
#: point is a result that did not go the author's way.
VOICE = "en-US-AndrewNeural"
RATE = "-4%"

FONT_SIZE = 30
WIDTH, HEIGHT = 1920, 1080
#: Seconds of stillness after the narration ends, so a cut never lands on the last syllable.
TAIL = 0.9
#: Typing speed. Slow enough to read, fast enough not to waste a three-minute budget.
TYPING = "45ms"


@dataclass(frozen=True)
class Shot:
    """One shot: what is said, and what is on screen while it is said."""

    id: str
    narration: str
    #: vhs commands. `{prompt}` marks where the shell prompt should look clean.
    body: str
    #: Extra seconds of footage before the narration starts, for shots that need the
    #: viewer to read something before being told about it.
    lead_in: float = 0.0
    env: dict[str, str] = field(default_factory=dict)


PRELUDE = """Set Shell "bash"
Set FontSize {font}
Set Width {width}
Set Height {height}
Set Padding 50
Set Theme "Catppuccin Mocha"
Set TypingSpeed {typing}
Hide
Type "cd {root}"
Enter
Type "export PATH=$PWD/.venv/bin:$PATH"
Enter
Type "clear"
Enter
Sleep 2s
Show
"""
# Each setup command is typed separately rather than chained with `&&`. Chained, a failing
# `cd` would skip the `clear` too, and every visible frame would then carry the shell's
# error — including the absolute path it could not enter.


SHOTS: list[Shot] = [
    Shot(
        id="01-verdict",
        narration=(
            "This is my own experiment failing its own check. MORROW measures whether a "
            "pull request makes the next change harder. Then it reported that it could not "
            "trust the measurement — under a decision rule fixed in the evaluator snapshot "
            "before the treatment was recorded."
        ),
        body='''Type "morrow verify cassettes/treatment-replace-cache"
Enter
Sleep 4s
''',
        lead_in=1.2,
    ),
    Shot(
        id="02-question",
        narration=(
            "Correctness, integration and security gates all answer whether the code "
            "works now. None tells you a change just made tomorrow's work more "
            "expensive. That question has no test, so nothing blocks on it."
        ),
        body='''Type "sed -n '24,29p' README.md"
Enter
Sleep 4s
''',
    ),
    Shot(
        id="03-instrument",
        narration=(
            "The design runs one registered future task against both sides, using a "
            "coding agent as the instrument. Same model, same prompt, same limits, each "
            "run in its own container. Three things are counted: distinct files read, "
            "test cycles burned, lines changed. Built so far: the recorder and the "
            "verifier — not pull-request orchestration."
        ),
        body='''Type "sed -n '/^| Component/,/^$/p' README.md | head -8"
Enter
Sleep 5s
''',
    ),
    Shot(
        id="04-result",
        narration=(
            "Ten container runs. Churn ratios three point seven five, five point one one "
            "and four point zero, against a largest null ratio of two point two two. "
            "Descriptive only — the experiment was invalidated, so this does not "
            "establish the candidate caused it. Files read came out mixed, and that is "
            "reported too."
        ),
        body='''Type "sed -n '/## Per-pair/,/^$/p' cassettes/treatment-replace-cache/report.md"
Enter
Sleep 6s
''',
    ),
    Shot(
        id="05-null",
        narration=(
            "The null compares a tree against itself, so anything it measures is "
            "run-to-run variation. But one-sided aggregation is not symmetric: "
            "relabelling which clone is baseline moves it from one point zero to one "
            "point seven four, past the tolerance band. Both orderings are published. "
            "The rule fixed beforehand is to report the experiment invalid, not to widen "
            "the band."
        ),
        body='''Type "morrow verify cassettes/null-control-as-recorded"
Enter
Sleep 3s
Type "morrow verify cassettes/null-control-arms-swapped"
Enter
Sleep 4s
''',
    ),
    Shot(
        id="06-signoz",
        narration=(
            "The agent's trajectory is a trace: experiment, pair, run, then one span per "
            "action. This is read back from ClickHouse rather than taken from the "
            "exporter's return value — exporting without an error is not evidence "
            "anything landed."
        ),
        body='''Type "python scripts/export_cassettes.py"
Enter
Sleep 6s
Type "python scripts/signoz_query.py --minutes 5 | head -24"
Enter
Sleep 7s
''',
    ),
    Shot(
        id="07-verify",
        narration=(
            "The verdict is not something you take on faith. Verify re-derives it from the "
            "evidence, then regenerates the report and compares it byte for byte. Change "
            "the evidence and cover your tracks in the manifest, and the report no longer "
            "follows from it. Continuous integration runs this on every pull request, and "
            "asserts both the exit code and the state."
        ),
        body='''Type "python scripts/tamper_demo.py"
Enter
Sleep 7s
''',
    ),
    Shot(
        id="08-built",
        narration=(
            "Claude Code wrote the implementation and this video's build script. ChatGPT "
            "contributed architecture and planning. Codex, with security and quality "
            "passes, found ways a cassette could have chosen its own verdict — supplying "
            "its own thresholds, asserting its own success, reusing one run as two. Each "
            "is now a test. I reviewed every change."
        ),
        body='''Type "pytest -q 2>&1 | tail -3"
Enter
Sleep 5s
''',
    ),
    Shot(
        id="09-close",
        narration=(
            "An instrument you can only trust when it agrees with you is not an instrument. "
            "MORROW."
        ),
        body='''Type "morrow verify cassettes/treatment-replace-cache"
Enter
Sleep 2s
Type "echo github.com/ichiburn/morrow"
Enter
Sleep 3s
''',
    ),
]


def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)  # type: ignore[call-overload]
    if result.returncode != 0:
        raise SystemExit(
            f"{command[0]} failed ({result.returncode})\n"
            f"--- stdout ---\n{result.stdout[-2000:]}\n--- stderr ---\n{result.stderr[-2000:]}"
        )
    return result


def duration_of(path: Path) -> float:
    probe = _run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ]
    )
    return float(probe.stdout.strip())


def narrate(shot: Shot) -> Path:
    """Synthesise the voiceover and return its path."""
    target = OUT / f"{shot.id}.mp3"
    _run(
        [
            # `--rate=-4%` rather than `--rate -4%`: a negative value as a separate token
            # is parsed as another flag.
            "edge-tts", "--voice", VOICE, f"--rate={RATE}",
            "--text", shot.narration, "--write-media", str(target),
        ]
    )
    return target


def record(shot: Shot, audio_seconds: float) -> Path:
    """Record the terminal footage, padded so it outlasts the narration.

    The tape's own sleeps set the pace of the commands; whatever is left over after the
    narration finishes becomes a still hold at the end. If the commands take *longer* than
    the narration, nothing is trimmed — the picture is what is real, and the audio simply
    finishes early.
    """
    video = OUT / f"{shot.id}.mp4"
    tape = OUT / f"{shot.id}.tape"
    prelude = PRELUDE.format(
        font=FONT_SIZE, width=WIDTH, height=HEIGHT, typing=TYPING, root=ROOT
    )
    lead = f"Sleep {shot.lead_in}s\n" if shot.lead_in else ""
    hold = max(1.0, audio_seconds + TAIL - _tape_seconds(shot.body) - shot.lead_in)
    tape.write_text(
        f'Output "{video}"\n{prelude}{lead}{shot.body}Sleep {hold:.1f}s\n', encoding="utf-8"
    )
    _run(["vhs", str(tape)], cwd=ROOT)
    return video


def _tape_seconds(body: str) -> float:
    """Rough length of the scripted sleeps, so the trailing hold can be computed."""
    total = 0.0
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("Sleep ") and stripped.endswith("s"):
            total += float(stripped[len("Sleep ") : -1])
        elif stripped.startswith("Type "):
            # Typing is animated, so long commands cost real time.
            total += len(stripped) * 0.045
    return total


def mux(shot: Shot, video: Path, audio: Path) -> Path:
    """Lay the narration over the footage, holding the last frame if the audio runs on."""
    merged = OUT / f"{shot.id}.mixed.mp4"
    _run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video), "-i", str(audio),
            # tpad freezes the final frame rather than cutting to black if the narration
            # outlasts the recording; -shortest then ends on whichever finishes last.
            "-vf", "tpad=stop_mode=clone:stop_duration=3",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-shortest", str(merged),
        ]
    )
    return merged


def concatenate(parts: list[Path]) -> Path:
    listing = OUT / "concat.txt"
    listing.write_text(
        "".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8"
    )
    final = OUT / "morrow-demo.mp4"
    _run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(final),
        ],
        cwd=OUT,
    )
    return final


def main() -> None:
    for tool in ("vhs", "ffmpeg", "ffprobe", "edge-tts"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is not on PATH")

    wanted = sys.argv[1:]
    shots = [s for s in SHOTS if not wanted or any(s.id.startswith(w) for w in wanted)]
    if not shots:
        raise SystemExit(f"no shot matches {wanted}")

    OUT.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    manifest: list[dict[str, object]] = []

    for shot in shots:
        audio = narrate(shot)
        seconds = duration_of(audio)
        video = record(shot, seconds)
        merged = mux(shot, video, audio)
        length = duration_of(merged)
        parts.append(merged)
        manifest.append(
            {"id": shot.id, "narration_s": round(seconds, 2), "shot_s": round(length, 2)}
        )
        print(f"{shot.id:14} narration {seconds:5.1f}s  shot {length:5.1f}s")

    total = sum(float(entry["shot_s"]) for entry in manifest)  # type: ignore[arg-type]
    print(f"{'total':14} {total:23.1f}s")
    if len(shots) == len(SHOTS):
        final = concatenate(parts)
        print(f"\n{final}  ({duration_of(final):.1f}s)")
        if duration_of(final) > 180:
            print("WARNING: over the 3:00 submission limit", file=sys.stderr)
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
