# Submission form — answers ready to paste

Form: https://forms.gle/xv1TXSiC54MEWujRA
Each heading below is a field on that form. Paste verbatim; nothing here needs editing
except the two links marked **TODO**.

---

## Team name (or your name if solo)

```
ichiburn
```

## Track

```
AI & Agent Observability
```

## Project description

```
MORROW asks a question conventional gates do not: not "does this code work now", but "can
this codebase still absorb the next change efficiently".

A future change task is registered in advance and run against both `main` and the candidate
— same model, same prompt, same resource limits, each run in its own container — measuring
the difference in what the coding agent had to do. Three components per run: how many
distinct files it had to read, how many test cycles it burned, how many lines of churn it
produced. The ratio is taken inside each pair and then median'd, and improvements are
floored at 1 so a regression on one axis cannot be cancelled by an improvement on another.

WHAT IS IMPLEMENTED: the recording script, the evidence format, and the verifier/gate CLI.
Pull-request-time orchestration is NOT built — the published experiment was recorded arm by
arm with a script, and the CLI verifies and gates on committed evidence rather than running
agents itself.

The agent's trajectory is exported to SigNoz as a trace — experiment, pair, run, then one
span per agent action — so the two arms of a comparison sit side by side and the difference
between them is the shape of the trace rather than a number in a table.

The result of the recorded experiment is that it FAILED ITS OWN VALIDITY CHECK, and that is
what ships. The null control (two clones of the same tree) drifted past its published
tolerance band when the arm labels were swapped — 1.0000 one way, 1.7403 the other, against
a band of 1.2000 — so the rule fixed beforehand reported INVALID_EXPERIMENT rather than
widening the band. Both arm orderings are published so neither is hidden.

The churn ratios came out 3.7538 / 5.1111 / 4.0000 against a largest null ratio of 2.2167.
That is a descriptive observation, NOT a finding: the experiment was invalidated, its report deliberately
carries no ratio medians and no aggregate FFR (the per-pair ratios above and the raw
component medians are published),
and nothing here establishes that the candidate caused the difference. Files-read came out mixed in direction (0.8947 / 0.8095 /
1.4167) and is reported rather than dropped.

Evidence for each experiment ships as a "cassette" committed to the repo. `morrow verify`
re-derives the verdict from it — digests, closed schemas, metrics rebuilt from the events,
churn records and launcher exit codes, then the report regenerated and compared
byte-for-byte — so a reader who does not trust the report can recompute it. Six rounds of
review (Codex, security, quality, and my own rechecks) hardened this against a cassette
choosing its own verdict, since a cassette arrives from the pull request under review. CI
verifies all three published cassettes on every pull request and every push to main,
asserting both exit code and reported state: 0 / 2 / 2, because two are supposed to fail.

LIMITS, stated up front: three compared pairs are not statistically significant (a one-sided
sign test floors at 0.125); execution is for trusted repositories only (no network
isolation); digests detect corruption but are not signatures; proxy validity is bounded to a
fixed provider, model and prompt; report-byte reproduction is claimed within a platform
(libm differences can surface as EVIDENCE_STALE); a treatment cassette cannot independently
prove the null figure it imports; and the committed casting files leave several components
unpinned, so an identical deployment is not guaranteed.
```

## GitHub repository link

```
https://github.com/ichiburn/morrow
```

`casting.yaml` and `casting.yaml.lock` are committed at the repository root, as required.

## Deployed project link (optional)

Leave blank. MORROW is a CI gate and a CLI, not a hosted service. The published cassettes
make the result reproducible without a deployment:

```
uv run morrow verify cassettes/treatment-replace-cache
```

## YouTube demo video (max 3 minutes)

**TODO** — upload `.video/out/morrow-demo.mp4` (2:56, already built) and paste the URL.

## How did you use SigNoz?

```
SigNoz is where the measurement becomes investigable — deliberately NOT where it is decided.

MORROW re-derives the verdict from the evidence and only then exports, because a gate that
blocks a build must be deterministic and reproducible with no telemetry backend running.
`morrow verify` reaches the same verdict offline. What SigNoz adds is the part a verdict
cannot carry: the trajectory a human audits when they want to argue with the result. An agent's trajectory *is* a trace, so
the mapping needed no invention:

  morrow.experiment      one registered task against one candidate
   └── morrow.pair       one baseline/candidate comparison
        ├── morrow.run   one agent execution
        │    ├── file.read / patch.apply / command.run / test.run
        └── morrow.run

Both arms of a pair hang off the same parent, so the difference between them is visible as
the shape of the subtree. In the recorded pair the baseline reads 18 files, burns 1 test
cycle and changes 129 lines (122 added, 7 deleted); the candidate reads 16, burns 4, changes
487 (324 added, 163 deleted). One subtree is visibly denser before you read a number.

SigNoz was self-hosted through Foundry (foundryctl v0.2.16 in my environment). `casting.yaml`
and its lock are committed as required; note that several components in the lock resolve to
`latest`, so I am not claiming byte-identical redeployment. Traces go over OTLP gRPC on
:4317, and the MCP server is explicitly enabled. The committed stack carries SigNoz's
default credentials and is intended for localhost only.

Three things I learned wiring it up:

1. Exporting without an error is not evidence that anything landed. My first export
   "succeeded" and stored nothing findable. I now read back from ClickHouse directly
   (`scripts/signoz_query.py`) — no session, no token, and the answer is the stored rows
   rather than a rendering of them. One export of the three cassettes produces 3 experiment,
   7 pair, 14 run and 441 event spans (413 action spans plus 28 session/completion spans).

2. In my Foundry deployment the collector could not register until the first organisation
   existed. Until then it looped on "cannot create agent without orgId" while looking
   otherwise healthy — I spent a while debugging my own exporter for that.

3. Not every event deserves a span. Across the three cassettes, 1,112 events carry no
   measurement signal against 441 that do. The quiet ones stay in the published evidence so
   a reader can count them, but they never become spans. A trace that is seventy per cent noise
   is not observability.

One field is deliberately absent: replayed runs omit the measured wall-clock duration. Wall
time is a property of the machine that recorded them, not of the work the task required, so
it is not in the published evidence — exporting a zero would have put a number on a
dashboard nobody measured. (Spans still carry ordinary OpenTelemetry start/end times, which
reflect the replay rather than the original run.) Experiment, pair and run spans are tagged
`morrow.evidence_mode=replay` so a view filtered to live measurements does not pick them up.
```

## Project blog link

**TODO** — publish `docs/submission/blog-draft.md` on Dev.to, Medium or Substack and paste
the URL. (A LinkedIn post does not count — the hackathon page says so explicitly.)

## AI disclosure

Include this wherever the form asks, and note it is also in the README and the blog.
Non-disclosure is disqualifying, so it is stated in full rather than summarised.

```
Anthropic Claude Code produced the implementation, the SigNoz/OpenTelemetry integration,
the tests, the documentation, and the script that builds the demo video.

OpenAI Codex performed adversarial design review before implementation began (three review
stages across four independent executions) and code review afterwards, alongside security
and quality review passes. Codex also fact-checked this submission's public claims against
the repository and found several overstatements, which were corrected.

ChatGPT contributed product architecture, planning, and document preparation.

The demo video's narration is synthetic speech (Microsoft edge-tts, en-US-AndrewNeural)
generated from a script in the repository; no human voice was recorded.

I reviewed and validated every generated change.
```

## Hackathon experience feedback

```
The most useful constraint turned out to be one I set on myself before collecting any data:
publish the decision rule first, then report whatever it says. It fired against me — the
null control drifted out of band and the experiment was reported invalid — and having
written the rule down beforehand is the only reason that outcome is worth anything.

On tooling: self-hosting SigNoz through Foundry was straightforward once past the
orgId/collector ordering issue, which cost me real time because the failure looks like a
healthy system. A note about it in the setup docs would help.

Building the exporter against ClickHouse readback rather than against exporter return codes
changed how much I trusted my own dashboard, and I would do that first next time.
```

---

## Mandatory requirements — status

| Requirement | Status |
|---|---|
| Usage of SigNoz | Done — self-hosted, traces landing, readback verified |
| `casting.yaml` in the repo | Done — plus `casting.yaml.lock` |
| Detailed blog on SigNoz usage | Draft ready (`blog-draft.md`); **needs publishing** |
| Blog newly written for this hackathon | Yes — not reused from the pre-event challenge |
| Demo video ≤ 3 minutes | Built (`video-script.md`, 2:57) — `.video/out/morrow-demo.mp4`; **needs uploading** |
| AI usage disclosed | In the README, the blog, the video narration, and the "AI disclosure" block above — all four naming Claude Code, Codex and ChatGPT with their distinct roles |
