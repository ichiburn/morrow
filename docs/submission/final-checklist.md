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
- [x] **Video scripted** — `docs/submission/video-script.md`, 2:57
- [x] **Form answers written** — `docs/submission/form-answers.md`

## Remaining — human only

- [ ] **Record the video** from the script. Activate the venv first so the commands read as
      a shipped CLI: `source .venv/bin/activate`. Hide absolute paths and hostnames.
- [ ] **Upload to YouTube**, unlisted or public. Check the audio plays in an incognito
      window — a silent upload is a failed submission.
- [ ] **Publish the blog** on Dev.to, Medium or Substack. Not LinkedIn: the hackathon page
      says a social post does not count as a blog.
- [ ] **Paste both URLs** into `form-answers.md` where marked TODO, then submit the form.

## Before recording, re-run these

Every number spoken in the video must appear on screen in the same shot, so re-run rather
than trusting this file:

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
