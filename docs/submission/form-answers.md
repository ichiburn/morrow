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
MORROW is a CI gate for a question no existing gate asks: not "does this code work now",
but "can this codebase still absorb the next change efficiently".

A future change task is registered in advance. When a pull request arrives, MORROW runs
that same task against both `main` and the candidate — same model, same prompt, same
resource limits, each run in its own container — and measures the difference in what the
coding agent had to do. Three components per run: how many distinct files it had to read,
how many test cycles it burned, how many lines it actually changed. The ratio is taken
inside each pair and then median'd, and improvements are floored at 1 so a regression on
one axis cannot be cancelled by an improvement on another.

The agent's trajectory is exported to SigNoz as a trace — experiment, pair, run, then one
span per agent action — so the two arms of a comparison sit side by side and the difference
between them is the shape of the trace rather than a number in a table.

The result of the recorded experiment is that it FAILED ITS OWN VALIDITY CHECK, and that is
what ships. The null control (two clones of the same tree) drifted past its published
tolerance band when the arm labels were swapped, so the pre-registered rule reported the
experiment as INVALID_EXPERIMENT rather than widening the band. Both arm orderings are
committed so neither could be the one chosen after seeing the data. Churn did separate
cleanly — every treatment pair between 3.75 and 5.11 against a null under 2.22 — and that is
reported as the one finding that survives, alongside the component that showed no signal
at all.

Evidence for each experiment ships as a "cassette" committed to the repo. `morrow verify`
re-derives the verdict from it — digests, closed schemas, metrics recomputed from the
events, then the report regenerated and compared byte-for-byte — so a reader who does not
trust the report can recompute it. Six rounds of adversarial review hardened this against a
cassette choosing its own verdict, since a cassette arrives from the pull request under
review. CI verifies all three published cassettes on every push and asserts their exit
codes: 0 / 2 / 2, because two of them are supposed to fail.
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

**TODO** — record from `docs/submission/video-script.md` (target 2:40) and paste the URL.

## How did you use SigNoz?

```
SigNoz is where the measurement becomes readable. An agent's trajectory *is* a trace, so
the mapping needed no invention:

  morrow.experiment      one registered task against one candidate
   └── morrow.pair       one baseline/candidate comparison
        ├── morrow.run   one agent execution
        │    ├── file.read / patch.apply / command.run / test.run
        └── morrow.run

Both arms of a pair hang off the same parent, so the difference between them is visible as
the shape of the subtree. In the published recording the baseline reads 18 files, burns 1
test cycle and writes 129 lines; the candidate reads 16, burns 4, writes 487. One subtree is
visibly denser before you read a single number.

SigNoz was self-hosted through Foundry (foundryctl v0.2.16, pinned rather than latest;
`casting.yaml` and its lock are committed so the deployment is reproducible from the repo
alone). Traces go over OTLP gRPC on :4317, and the MCP server is explicitly enabled.

Three things I learned wiring it up:

1. Exporting without an error is not evidence that anything landed. My first export
   "succeeded" and stored nothing findable. I now read back from ClickHouse directly
   (`scripts/signoz_query.py`) — no session, no token, and the answer is the stored rows
   rather than a rendering of them. That readback is what supports the numbers I quote:
   3 experiment, 7 pair and 14 run spans over ~1,500 action spans.

2. The collector cannot register until the first organisation exists. Until then it loops
   on "cannot create agent without orgId" while looking otherwise healthy — I spent a while
   debugging my own exporter for that.

3. Not every event deserves a span. One run produced 108 events carrying no measurement
   signal against 22 real actions. Those stay in the published evidence so a reader can
   count them, but they never become spans. A trace that is five-sixths noise is not
   observability.

One field is deliberately absent: replayed runs carry no wall-clock duration. Wall time is a
property of the machine that recorded them, not of the work the task required, so it is not
in the published evidence — exporting a zero would have put a number on a dashboard that
nobody measured. Replayed spans are also tagged evidence_mode=replay so a view filtered to
live measurements does not pick them up.
```

## Project blog link

**TODO** — publish `docs/submission/blog-draft.md` on Dev.to, Medium or Substack and paste
the URL. (A LinkedIn post does not count — the hackathon page says so explicitly.)

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
| Demo video ≤ 3 minutes | Script ready (`video-script.md`, 2:40); **needs recording** |
| AI usage disclosed | In the README, the blog, and this form |
