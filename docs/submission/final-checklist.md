# Final submission checklist

Form: https://forms.gle/xv1TXSiC54MEWujRA (confirmed open · deadline time and time zone are
not stated publicly — the form itself accepting responses is the only reliable signal)

## Done — verified, with the evidence

- [x] **Repository public** — https://github.com/ichiburn/morrow
- [x] **`casting.yaml` + `casting.yaml.lock` at the repo root** (mandatory)
- [x] **SigNoz self-hosted and receiving** — verified by reading back from ClickHouse, not
      by the exporter returning cleanly: 3 experiment, 7 pair, 14 run and 441 event
      spans (413 actions plus 28 session/completion), run attributes carrying the measured counts
- [x] **Tests green** — 212 passed
- [x] **Lint and types** — ruff clean, mypy strict clean
- [x] **CI green**, including the step that verifies all three published cassettes and
      asserts their exit codes (0 / 2 / 2)
- [x] **Everything merged to `main`** via PR (no direct commits)
- [x] **README states what is not claimed before it states any result**
- [x] **AI usage disclosed** in the README (non-disclosure is disqualifying)
- [x] **Six rounds of adversarial review closed** — final round returned no CRITICAL/HIGH
      and no MUST_FIX from either reviewer
- [x] **Blog drafted** — `docs/submission/blog-draft.md`
- [x] **Video built** — `.video/out/morrow-demo.mp4`, 2:56, from `scripts/build_video.py`.
      Not filmed by hand: every command in it is executed at build time, and the verdict and
      figures its narration names are re-derived from the cassette's evidence rather than
      read out of the terminal text — so the build fails rather than shipping footage of a
      different outcome. Figures the voiceover does not speak are not pinned.
- [x] **Form answers written** — `docs/submission/form-answers.md`

## Remaining — human only

- [ ] **Watch the built video through once with sound**, and confirm no absolute path or
      hostname is legible in any frame.
- [ ] **Upload to YouTube**, unlisted or public. Check the audio plays in an incognito
      window — a silent upload is a failed submission.
- [ ] **Publish the blog** on Dev.to, Medium or Substack. Not LinkedIn: the hackathon page
      says a social post does not count as a blog.
- [ ] **Paste both URLs** into `form-answers.md` where marked TODO, then submit the form.

## Before rebuilding the video, re-run these

The build asserts each command's exit code, the state its narration names, and the figures
that narration speaks. Re-run these anyway rather than trusting this file — SigNoz has to be
up for the export shot, and it is outside what the build can check:

```bash
uv run pytest -q                                           # 212 passed
uv run morrow verify cassettes/null-control-as-recorded    # exit 0
uv run morrow verify cassettes/null-control-arms-swapped   # exit 2
uv run morrow verify cassettes/treatment-replace-cache     # exit 2
uv run python scripts/export_cassettes.py                  # → SigNoz
uv run python scripts/signoz_query.py --minutes 15         # confirm it stored
```

## Do not say, on camera or in the form

The instrument's honesty is the submission. Overstating it costs more than it gains.

- The treatment result is **not** a demonstration that the coupled candidate costs more.
  The experiment was invalidated; `final_churn` is the only component that separated.
- **Never** "statistically significant". Three compared pairs floor a one-sided sign test
  at 0.125.
- `morrow measure` does not exist yet — the recording replays committed evidence and says
  so. Do not imply a live measurement.
- `files_read_distinct` showed no signal. Say it rather than omitting it.
