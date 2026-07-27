"""Build the demo video end to end: narration, terminal footage, and the final cut.

    uv run --group video python scripts/build_video.py        # everything
    uv run --group video python scripts/build_video.py 01 05  # just those shots

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
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
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
TAIL = 0.4
#: Typing speed. Every millisecond here is paid twice — once watching the command appear,
#: and again in the delay before narration can start — so it is brisk.
TYPING = "35ms"
TYPING_SECONDS = 0.035
#: The submission's hard ceiling. Not a style preference — a longer cut is not accepted.
LIMIT_SECONDS = 180.0

SHOT_ENV = {**os.environ, "PATH": f"{ROOT / '.venv' / 'bin'}:{os.environ['PATH']}"}

TITLE_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
TITLE_SIZE = 30
# ASS font sizes are relative to the subtitle canvas (288 lines by default), not to the
# video. 9 lands at roughly 34px on a 1080p frame; 21 filled a third of the screen.
CAPTION_SIZE = 9


@dataclass(frozen=True)
class Expect:
    """What one filmed command has to actually do for the narration to be true.

    A set of tolerated exit codes per *shot* was not enough. Three shots film commands that
    exit 2 on purpose alongside commands that exit 0, so the shot-wide set had to admit
    both — and then a clean verdict, a stale one, and a crash all satisfied it equally,
    while the voiceover named a specific outcome. Pinning the code per command, and the
    word the narration stakes itself on, is what makes the preflight an assertion rather
    than a smoke test.
    """

    code: int = 0
    #: A string the output must contain. The verdict state, for the shots that name one.
    contains: str = ""


@dataclass(frozen=True)
class Shot:
    """One shot: what is said, and what is on screen while it is said."""

    id: str
    #: Shown in the corner for the whole shot. Terminal output alone does not tell a
    #: viewer what they are being shown; this does.
    title: str
    narration: str
    #: vhs commands. `{prompt}` marks where the shell prompt should look clean.
    body: str
    #: Seconds to hold the narration back so it lands on a finished screen.
    #:
    #: This is the difference between a video whose length matches its audio and one that
    #: is actually in sync. The footage types its command before any output exists, so
    #: narration starting at zero talks about a result the viewer cannot see yet. Delay it
    #: past the typing and the command's own latency, and the words arrive with the thing
    #: they describe.
    audio_delay: float = 1.6
    #: A still to hold over the footage for the first `overlay_seconds`, for shots whose
    #: terminal output cannot show the thing being described. The trace hierarchy is the
    #: clearest example: a table of counts states the difference between two runs, but only
    #: a picture shows that one subtree is bigger than the other.
    overlay: str | None = None
    overlay_seconds: float = 0.0
    #: Lines to print in the empty lower half of the frame. Terminal output rarely fills
    #: 1080 lines, and a judge should not have to read the repository to learn what the
    #: thing is built out of.
    footer: tuple[str, ...] = ()
    #: What each of this shot's commands must do, in the order they are typed. Left empty,
    #: every command must exit 0 and its output is not inspected.
    expect: tuple[Expect, ...] = ()


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
        title="The result, first — this experiment failed its own check",
        narration=(
            "This is my own experiment failing its own check. MORROW measures whether a "
            "pull request makes the next change harder — then reported that it could not "
            "trust the measurement, under a rule fixed before the treatment was recorded."
        ),
        body='''Type "morrow verify cassettes/treatment-replace-cache"
Enter
Sleep 4s
''',
        audio_delay=2.1,
        expect=(Expect(code=2, contains="INVALID_EXPERIMENT"),),
    ),
    Shot(
        id="02-question",
        title="The question no existing gate asks",
        narration=(
            "Correctness, integration and security gates all answer whether the code "
            "works now. None tells you a change just made tomorrow's work more "
            "expensive. That question has no test, so nothing blocks on it."
        ),
        body='''Type "morrow --help"
Enter
Sleep 4s
''',
        audio_delay=1.4,
    ),
    Shot(
        id="03-instrument",
        title="What gets measured",
        narration=(
            "One registered future task, run against both sides with a coding agent as "
            "the instrument. Same model, same prompt, same limits, each in its own "
            "container. Counted: distinct files read, test cycles burned, lines changed. "
            "This prototype implements recording, evidence verification and gating. "
            "Pull-request orchestration is the next step."
        ),
        body='''Type "morrow show cassettes/treatment-replace-cache"
Enter
Sleep 5s
''',
        audio_delay=2.2,
        expect=(Expect(contains="INVALID_EXPERIMENT"),),
    ),
    Shot(
        id="04-result",
        title="The null control, for comparison",
        narration=(
            "And this is the null control — two clones of the same tree. Its churn ratios "
            "sit below one, against the treatment's three point seven five, five point "
            "one one and four point zero. Descriptive only: the experiment was "
            "invalidated, so none of this establishes the candidate caused the difference."
        ),
        body='''Type "morrow show cassettes/null-control-as-recorded"
Enter
Sleep 5s
''',
        audio_delay=2.3,
        expect=(Expect(contains="null-control-as-recorded"),),
    ),
    Shot(
        id="05-null",
        title="The null control — and the rule that fired",
        narration=(
            "The null compares a tree against itself, so anything it measures is "
            "run-to-run variation. But one-sided aggregation is not symmetric: "
            "relabelling which clone is baseline moves it from one point zero to one "
            "point seven four, past the band. The rule fixed beforehand is to report the "
            "experiment invalid, not widen it."
        ),
        body='''Type "morrow verify cassettes/null-control-as-recorded"
Enter
Sleep 4s
Type "morrow verify cassettes/null-control-arms-swapped"
Enter
Sleep 3s
''',
        audio_delay=2.1,
        expect=(
            Expect(contains="EVIDENCE_REPRODUCED"),
            Expect(code=2, contains="INVALID_EXPERIMENT"),
        ),
    ),
    Shot(
        id="06-signoz",
        title="SigNoz — not the judge, the audit trail",
        narration=(
            "The verdict is decided from the evidence before any of this is sent, so "
            "SigNoz is not in the decision path — the gate has to stay deterministic. "
            "What it is for is the human afterwards, auditing what the agent actually "
            "did: experiment, pair, run, and the two arms of a comparison side by side."
        ),
        body='''Type "python scripts/export_cassettes.py"
Enter
Sleep 8s
Type "python scripts/signoz_query.py --minutes 30 | head -22"
Enter
Sleep 5s
''',
        # The export takes several seconds; the diagram covers exactly that wait, and the
        # narration's opening line describes the hierarchy it draws.
        audio_delay=1.8,
        overlay="diagram.png",
        overlay_seconds=8.5,
    ),
    Shot(
        id="07-verify",
        title="Tamper with the evidence, and the report stops following",
        narration=(
            "The verdict is not something you take on faith. Verify re-derives it from "
            "the evidence and compares the report byte for byte. Edit the evidence and fix "
            "the digest to match — the report no longer follows from it. CI runs this on "
            "every pull request."
        ),
        body='''Type "python scripts/tamper_demo.py"
Enter
Sleep 7s
''',
        audio_delay=3.5,
        expect=(Expect(contains="EVIDENCE_STALE"),),
    ),
    Shot(
        id="08-built",
        title="How it was built, and what review found",
        narration=(
            "Claude Code wrote the implementation and this build script; ChatGPT, "
            "architecture and planning. Codex and the security and quality passes found "
            "ways a cassette could choose its own verdict — its own thresholds, its own "
            "success, one run counted as two. Each is now a test. I reviewed every "
            "change."
        ),
        # No `-q` here: pyproject already passes one via `addopts`, and a second suppresses
        # the summary line entirely — the shot showed a wall of progress dots and never
        # said how many tests passed.
        body='''Type "pytest 2>&1 | tail -2"
Enter
Sleep 4s
''',
        expect=(Expect(contains="passed"),),
        footer=(
            "Built with",
            "Python 3.12  ·  Typer  ·  Docker  ·  OpenTelemetry / OTLP",
            "SigNoz / ClickHouse  ·  Foundry  ·  GitHub Actions",
        ),
        audio_delay=2.2,
    ),
    Shot(
        id="09-close",
        title="MORROW",
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
        audio_delay=2.1,
        expect=(
            Expect(code=2, contains="INVALID_EXPERIMENT"),
            Expect(contains="github.com/ichiburn/morrow"),
        ),
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


def narrate(shot: Shot) -> tuple[Path, Path]:
    """Synthesise the voiceover and its timed subtitles.

    The subtitles are not an accessibility afterthought. Terminal footage is dense and
    unlabelled, and a viewer who cannot follow the narration has no way to work out what
    they are looking at — the words on screen are what make the picture legible.
    """
    audio = OUT / f"{shot.id}.mp3"
    captions = OUT / f"{shot.id}.srt"
    _run(
        [
            # `--rate=-4%` rather than `--rate -4%`: a negative value as a separate token
            # is parsed as another flag.
            "edge-tts", "--voice", VOICE, f"--rate={RATE}",
            "--text", shot.narration, "--write-media", str(audio),
            "--write-subtitles", str(captions),
        ]
    )
    _shift_captions(captions, shot.audio_delay)
    return audio, captions


def _shift_captions(path: Path, offset: float) -> None:
    """Move every cue later by ``offset`` seconds, matching the delayed narration."""

    def shift(stamp: str) -> str:
        hours, minutes, rest = stamp.split(":")
        seconds, millis = rest.split(",")
        total = (
            int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000
        ) + offset
        h, remainder = divmod(total, 3600)
        m, sec = divmod(remainder, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(sec):02d},{round(sec % 1 * 1000):03d}"

    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "-->" in line:
            start, end = (part.strip() for part in line.split("-->"))
            lines.append(f"{shift(start)} --> {shift(end)}")
        else:
            lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def preflight(shot: Shot) -> None:
    """Run the shot's commands for real and refuse to film ones that fail.

    vhs reports whether it recorded, not whether what it recorded worked. Without this the
    build happily ships footage of a traceback — which would also put an absolute path on
    screen — or of a command that silently produced nothing, while the narration explains
    the result it was supposed to have produced.

    `set -o pipefail` matters here: `pytest | tail` and `signoz_query | head` both return
    the *filter's* status, so a failed producer looks like success without it.
    """
    commands = re.findall(r'Type "([^"]+)"', shot.body)
    expectations = shot.expect or tuple(Expect() for _ in commands)
    if len(expectations) != len(commands):
        raise SystemExit(
            f"{shot.id}: {len(commands)} commands but {len(expectations)} expectations — "
            "every filmed command needs one, or the shot asserts nothing about the rest"
        )
    for command, want in zip(commands, expectations, strict=True):
        result = subprocess.run(
            ["bash", "-o", "pipefail", "-c", command],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=SHOT_ENV,
        )
        output = result.stdout + result.stderr
        if result.returncode != want.code:
            raise SystemExit(
                f"{shot.id}: `{command}` exited {result.returncode}, expected {want.code}\n"
                f"--- stdout ---\n{result.stdout[-1500:]}\n"
                f"--- stderr ---\n{result.stderr[-1500:]}"
            )
        if want.contains and want.contains not in output:
            raise SystemExit(
                f"{shot.id}: `{command}` did not print {want.contains!r}, which the "
                f"narration relies on\n--- output ---\n{output[-1500:]}"
            )
        if not output.strip():
            raise SystemExit(f"{shot.id}: `{command}` produced no output to film")


def record(shot: Shot) -> Path:
    """Record the terminal footage at the pace the tape sets.

    No attempt is made to stretch the recording to the narration's length. vhs does not
    reliably render a long trailing `Sleep` — it stops once the terminal goes quiet — so
    the padding is done in :func:`mux`, where the exact durations are known and ffmpeg can
    hold the final frame for as long as it takes.
    """
    video = OUT / f"{shot.id}.mp4"
    tape = OUT / f"{shot.id}.tape"
    prelude = PRELUDE.format(
        font=FONT_SIZE, width=WIDTH, height=HEIGHT, typing=TYPING, root=ROOT
    )
    tape.write_text(f'Output "{video}"\n{prelude}{shot.body}', encoding="utf-8")
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
            total += len(stripped) * TYPING_SECONDS
    return total


def mux(shot: Shot, video: Path, audio: Path, captions: Path, index: int) -> Path:
    """Compose the finished shot: footage, title, subtitles, and delayed narration.

    The picture is held on its final frame until the narration has finished, so a shot is
    always ``delay + narration + tail`` long regardless of how quickly its commands ran.
    """
    merged = OUT / f"{shot.id}.mixed.mp4"
    audio_seconds = duration_of(audio)
    filmed = duration_of(video)
    wanted = shot.audio_delay + audio_seconds + TAIL
    pad = max(0.0, wanted - filmed)
    delay_ms = int(shot.audio_delay * 1000)

    label = f"{index}/{len(SHOTS)}   {shot.title}".replace(":", r"\:").replace("'", "")
    style = (
        f"FontName=DejaVu Sans,FontSize={CAPTION_SIZE},PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00101018,BackColour=&HB0101018,BorderStyle=3,Outline=2,Shadow=0,"
        "Alignment=2,MarginV=16"
    )
    footer = ""
    for line, text in enumerate(shot.footer):
        safe = text.replace(":", r"\:").replace("'", "")
        weight = TITLE_SIZE + 8 if line == 0 else TITLE_SIZE
        colour = "0xcdd6f4" if line == 0 else "0x9399b2"
        footer += (
            f",drawtext=fontfile={TITLE_FONT}:text='{safe}':x=70:"
            f"y={620 + line * 58}:fontsize={weight}:fontcolor={colour}:borderw=0"
        )

    overlay = ""
    if shot.overlay:
        still = OUT / shot.overlay
        if not still.exists():
            raise SystemExit(f"{shot.id}: overlay {still} was not built")
        overlay = (
            f"movie='{still}'[still];"
            f"[base][still]overlay=0:0:enable='lt(t,{shot.overlay_seconds})'[base];"
        )

    chain = (
        # tpad clones the last frame so the terminal stays on screen instead of cutting to
        # black mid-sentence; the title sits in the corner for the whole shot; the burnt-in
        # captions carry the narration for anyone who cannot follow the audio.
        f"[0:v]tpad=stop_mode=clone:stop_duration={pad:.2f}[base];"
        + overlay
        + f"[base]drawtext=fontfile={TITLE_FONT}:text='{label}':x=60:y=36:"
        f"fontsize={TITLE_SIZE}:fontcolor=0x7d8299:borderw=0" + footer + ","
        f"subtitles='{captions}':force_style='{style}'[v];"
        f"[1:a]adelay={delay_ms}:all=1[a]"
    )
    _run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video), "-i", str(audio),
            "-filter_complex", chain,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-t", f"{wanted:.2f}", str(merged),
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

    # The diagram is a build product, not an input: `.video/` is gitignored, so a fresh
    # clone has no `diagram.png` and the shot that overlays it would fail on a missing
    # file. Regenerating it every run also keeps it from going stale — it reads its numbers
    # out of the cassettes, and a leftover copy from an older recording would put figures
    # on screen that no longer match the ones beside it.
    print("building the trace diagram from the cassettes")
    _run(
        [sys.executable, str(ROOT / "scripts" / "build_diagram.py")],
        cwd=ROOT,
        env=SHOT_ENV,
    )

    parts: list[Path] = []
    manifest: list[dict[str, object]] = []

    for shot in shots:
        preflight(shot)
        audio, captions = narrate(shot)
        seconds = duration_of(audio)
        video = record(shot)
        merged = mux(shot, video, audio, captions, SHOTS.index(shot) + 1)
        length = duration_of(merged)
        parts.append(merged)
        manifest.append(
            {"id": shot.id, "narration_s": round(seconds, 2), "shot_s": round(length, 2)}
        )
        print(f"{shot.id:14} narration {seconds:5.1f}s  shot {length:5.1f}s")

    total = sum(float(entry["shot_s"]) for entry in manifest)  # type: ignore[arg-type]
    print(f"{'total':14} {total:23.1f}s")
    over_limit = 0.0
    if len(shots) == len(SHOTS):
        final = concatenate(parts)
        length = duration_of(final)
        print(f"\n{final}  ({length:.1f}s)")
        over_limit = length - LIMIT_SECONDS
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    # A hard limit, so it fails the build. The submission is rejected at 3:00.1 exactly as
    # firmly as at 4:00, and a warning printed above a hundred lines of ffmpeg output is a
    # warning nobody reads.
    if over_limit > 0:
        raise SystemExit(
            f"the cut runs {over_limit:.1f}s over the {LIMIT_SECONDS:.0f}s submission "
            "limit — shorten a narration and rebuild"
        )


if __name__ == "__main__":
    main()
