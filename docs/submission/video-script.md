# MORROW demo video — final recording script

Target: **2:40.0**, English neural voiceover, real terminal and SigNoz interaction for the
product flow. Every number spoken is one the committed cassettes actually produce — the
recording must never show a figure that `morrow verify` does not. The hard limit on the
submission form is 3:00.

## Assembly method

1. Generate one English voiceover block per shot from the exact TTS text below. The stated
   timings leave only 0.3–0.8 seconds of visual breathing room.
2. Record Shots 1, 5, 7 and 8 as terminal footage at 1920×1080, monospace at a size that
   is readable when YouTube downscales to 720p. Clear the scrollback before each take.
   **Activate the environment before recording** so the commands read as a shipped CLI
   rather than as a script invocation:

   ```bash
   source .venv/bin/activate   # then `morrow verify …` works directly
   ```
3. Record Shot 6 as one uninterrupted SigNoz interaction — no cuts inside the trace view.
4. Hide the prompt's absolute path, any hostname, and the notification area. `HOME`-relative
   prompts only.
5. Hard cuts between terminal footage and report/README stills. Mild, uniform speech-speed
   correction only. **Do not delete the scope qualifiers in Shots 5 and 9.**

## What must not be claimed on camera

The instrument's own honesty is the submission. A recording that overstates it is worse
than no recording.

- Never call the treatment result a demonstration that the coupled candidate costs more.
  The experiment was **invalidated**; only `final_churn` separated.
- Never say "statistically significant", or imply the null control passed.
- Never show a live `morrow measure` — it does not exist yet. The recording replays
  committed evidence and says so.

## Shot list

### Shot 1 — 0:00–0:15 (15 seconds): the result first, and it is a failure

**Screen recording:** A clean terminal. Type and run:

```
morrow verify cassettes/treatment-replace-cache
```

The output is two lines — verified verbatim:

```
ERROR · INVALID_EXPERIMENT · exit 2
report reproduced byte for byte, and the re-derived verdict is INVALID_EXPERIMENT: null control FFR_gate 1.7403 exceeds maximum_ffr 1.2000
```

Let the first line sit on screen, then move the cursor slowly to `1.7403` and `1.2000`.
Note there is no FFR line here: an invalidated experiment does not report one.

**TTS text:**

> This is my own experiment failing its own check. MORROW measured whether a pull request
> makes the next change harder, then reported that it could not trust the measurement. The
> rule that says so was published before any data was collected.

### Shot 2 — 0:15–0:31 (16 seconds): the question

**Screen recording:** The README title block, framing the two lines under the heading:
"This pull request passes every test. But does it make the next change harder?" Then the
four-row comparison table, holding on the MORROW row.

**TTS text:**

> Every existing gate answers whether the code works now. Unit tests, integration tests, a
> security scan — all present tense. None of them can tell you that a change just made
> tomorrow's work more expensive. That question has no test, so it does not block anything.

### Shot 3 — 0:31–0:50 (19 seconds): the instrument

**Screen recording:** The README "What is measured" section. Hold on the three-component
table, then on the formula block, then on the sentence about one-sided aggregation.

**TTS text:**

> MORROW registers a future change task in advance and runs it against both sides — main
> and the candidate — using a coding agent as the measuring instrument. Same model, same
> prompt, same resource limits, each run in its own container. Three things are counted:
> distinct files read, test cycles burned, lines actually changed.

### Shot 4 — 0:50–1:12 (22 seconds): what ten runs produced

**Screen recording:** `cassettes/treatment-replace-cache/report.md`, scrolled to the
per-pair ratio table. Hold on the `final_churn` column. Then cut to the two null control
reports side by side, framing their churn columns.

**TTS text:**

> Ten container runs, one task, one model. Churn separated cleanly: every treatment pair
> landed between three-point-seven and five-point-one, while the null control — two clones
> of the same tree — stayed under two-point-three in either arm ordering. Three of three
> pairs clear of the noise floor. Files read showed nothing at all, and that is reported
> too, not dropped.

### Shot 5 — 1:12–1:36 (24 seconds): the null control, and the rule that fired

**Screen recording:** Run both null cassettes in sequence:

```
morrow verify cassettes/null-control-as-recorded
morrow verify cassettes/null-control-arms-swapped
```

Frame `exit 0` on the first and `INVALID_EXPERIMENT · exit 2` on the second. Point to
`1.7403` and to the `1.2000` band beside it.

**TTS text:**

> The null control compares a tree against itself, so its two arms differ by nothing. But
> one-sided aggregation is not symmetric: relabelling which clone is the baseline moves the
> null from one-point-zero to one-point-seven-four, past the published tolerance band. Both
> orderings are committed, so neither could be the one chosen after seeing the data. The
> pre-registered rule for a null out of band is to report the experiment invalid — not to
> widen the band. That is why the run you saw first exits two.

### Shot 6 — 1:36–1:58 (22 seconds): the trajectories in SigNoz

**Screen recording:** One uninterrupted SigNoz interaction. Open the trace list filtered to
service `morrow`; open the treatment trace. Expand `morrow.experiment` → `morrow.pair` →
the two `morrow.run` spans. Hover the baseline run to show `files_read_distinct 18`,
`test_cycles 1`, `final_churn 129`; then the candidate showing `16`, `4`, `487`. Scroll the
action spans below.

**TTS text:**

> Each experiment becomes one trace in SigNoz: experiment, pair, run, then one span per
> action the agent took. The two arms of a pair sit side by side, so the difference between
> them is the shape of the trace rather than a number in a table. Here the candidate read
> fewer files but burned four test cycles instead of one, and wrote four hundred and
> eighty-seven lines against a hundred and twenty-nine.

### Shot 7 — 1:58–2:18 (20 seconds): the evidence is checkable

**Screen recording:** Edit one byte of a committed churn record, update its digest in the
manifest, then run `morrow verify` on that cassette. Show `EVIDENCE_STALE`. Undo. Then show
the CI step "Verify the published cassettes" passing with its asserted exit codes.

**TTS text:**

> The verdict is not something you have to take on faith. Verify re-derives it from the
> evidence: digests, closed schemas, metrics recomputed from the events, then the report
> regenerated and compared byte for byte. Change the evidence and cover your tracks in the
> manifest, and the report no longer follows from it. CI runs this on every push and
> asserts the exit code of all three cassettes.

### Shot 8 — 2:18–2:34 (16 seconds): how it was built

**Screen recording:** `git log --oneline` scrolled to show the fix commits, then a terminal
showing `212 passed`. Optionally a sanitized view of one review round's findings list.

**TTS text:**

> Claude Code wrote the implementation and the containerised recording. OpenAI Codex
> reviewed it adversarially across six rounds and found ways a cassette could have chosen
> its own verdict — supplying the thresholds that judged it, asserting its own success,
> reusing one run as two repetitions. Each of those is now a test that reproduces the
> attack.

### Shot 9 — 2:34–2:40 (6 seconds): close

**Screen recording:** Return to the `INVALID_EXPERIMENT` output from Shot 1, with the
repository URL on screen.

**TTS text:**

> An instrument you can only trust when it agrees with you is not an instrument. MORROW.

## Recording acceptance gate

- [ ] Final runtime is under 3:00 and the opening shot is the failing verdict
- [ ] Every spoken number appears on screen in the same shot
- [ ] `INVALID_EXPERIMENT` is spoken as the actual result, never as a demonstration mode
- [ ] The null control is shown in **both** arm orderings
- [ ] "Statistically significant" is never spoken; the sample size is not implied to be adequate
- [ ] `files_read_distinct` showing no signal is stated out loud, not omitted
- [ ] SigNoz footage is one uninterrupted interaction showing real stored spans
- [ ] The tamper demo restores the cassette before the take ends
- [ ] AI tool usage is stated explicitly (Claude Code: implementation · Codex: review)
- [ ] No absolute paths, hostnames, tokens, or email addresses are visible in any frame
- [ ] Audio plays on upload, checked in an incognito window
