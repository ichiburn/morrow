# MORROW demo video

**Built, not recorded.** `scripts/build_video.py` produces the finished file:

```bash
mise use -g vhs ttyd          # terminal recorder
uv tool install edge-tts      # narration
uv run --group video python scripts/build_video.py
# → .video/out/morrow-demo.mp4
```

Result: **2:56** (176.2s), 1920×1080, under the 3:00 submission limit. The build enforces
that limit rather than warning about it — a cut that runs over exits non-zero.

The `video` dependency group carries Pillow, which `build_diagram.py` needs. `build_video.py`
runs the diagram build itself, so a fresh clone produces that file rather than requiring one.

## Why it is generated rather than filmed

Every command in the video is a command in `build_video.py`, really executed at build time
against the committed cassettes. The footage cannot drift from what the tool does: if a
command starts failing, the build fails instead of quietly shipping stale footage. Re-run it
after any change and the video is current.

Each shot is a `vhs` tape plus one line of narration. The narration is synthesised first,
its duration measured, and the tape's trailing pause stretched to match — so audio and
picture end together without hand-syncing. Shots are then concatenated.

To iterate on one shot: `uv run --group video python scripts/build_video.py 05`.

## Shot list

| # | Shot | On screen | Length |
|---|---|---|---|
| 1 | `01-verdict` | `morrow verify` on the treatment cassette → `INVALID_EXPERIMENT`, exit 2 | 15.8s |
| 2 | `02-question` | `morrow --help` — the three modes that exist | 14.7s |
| 3 | `03-instrument` | `morrow show` on the treatment: the per-pair ratio table | 26.4s |
| 4 | `04-result` | `morrow show` on the null control, for comparison | 22.2s |
| 5 | `05-null` | Both null controls verified, one in band and one out | 24.0s |
| 6 | `06-signoz` | Export to SigNoz, the trace-shape diagram, then the ClickHouse readback | 21.8s |
| 7 | `07-verify` | `tamper_demo.py`: edit the evidence, fix the digest, get `EVIDENCE_STALE` | 20.2s |
| 8 | `08-built` | 212 tests passing; the tech stack; how Claude Code and Codex were used | 23.2s |
| 9 | `09-close` | Back to the failing verdict, with the repository URL | 8.0s |

Lengths are the shot durations the build writes to `.video/out/manifest.json`; they sum to
the 176.2s cut.

The narration text is in `SHOTS` in `build_video.py` — that is the single source, so the
script and the audio cannot disagree. Each command's expected exit code *and* a word its
narration depends on are asserted before filming, so a shot cannot ship footage of an
outcome other than the one being described.

### The opening shot is the failure, on purpose

It leads with this project's own experiment being reported invalid. That is the honest lead:
the instrument said it could not trust its own measurement, under a rule published before
any data existed. Burying it behind a feature tour would misrepresent what was built.

### Shot 6 shows stored rows, not a dashboard

The SigNoz UI needs an authenticated session, which cannot be automated cleanly and would
have to be filmed by hand. The ClickHouse readback is better evidence anyway: it is the rows
SigNoz actually stored, not a rendering of them, and it is the same check that caught an
early export "succeeding" while storing nothing findable.

If you want UI footage for the submission, record the trace view separately and cut it in
over Shot 6's narration — the timing has room.

## What must not be claimed on camera

The instrument's honesty is the submission. A recording that overstates it is worse than no
recording. These are enforced by the narration text in `build_video.py`; if you edit it,
keep them.

- The treatment result is **not** a demonstration that the coupled candidate costs more.
  The experiment was invalidated; `final_churn` is the only component that separated.
- Never "statistically significant". Three compared pairs floor a one-sided sign test
  at 0.125.
- PR-time orchestration does not exist yet. Shot 3 says so in as many words: the prototype
  implements recording, evidence verification and gating.
- `files_read_distinct` showed no signal. Shot 4 states it rather than omitting it.

## Before uploading

- [ ] **Check what is in SigNoz's recent window first.** Shot 6 reads back everything the
      `morrow` service stored in the last few minutes. If an unpublished live experiment
      was exported just before the build, its rows appear on camera. Only the three
      published cassettes should be in the window.
- [ ] Watch it through once with sound
- [ ] Confirm no absolute path, hostname or token is legible in any frame (the tapes `cd`
      into the repo with output hidden, so the prompt stays clean — but check)
- [ ] Upload to YouTube, public or unlisted
- [ ] **Play it back in an incognito window** — a silent upload is a failed submission
- [ ] Paste the URL into `form-answers.md`
