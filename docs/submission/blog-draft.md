# I built a CI gate that measures future change friction. It failed its own check.

*Submitted to Agents of SigNoz, Track 01: AI & Agent Observability. Publish on Dev.to,
Medium or Substack — the submission form requires a blog, and a LinkedIn post does not
count as one.*

---

Correctness, integration and security gates all answer a question in the present tense. Do
the unit tests pass. Do the components fit together. Is a known vulnerability present. All
useful, all about now.

None of them tells you that the pull request in front of you just made next month's work
more expensive. That question has no test, so nothing blocks on it, so it accumulates.

MORROW is an attempt to make it measurable. A future change task is registered in advance;
the same task is then run against **both** `main` and the candidate — same model, same
prompt, same resource limits, each run in its own container — and the difference in what
the agent had to do is the measurement.

**What is implemented today**, to be clear before anything else: the recording script, the
evidence format, and the verifier. Pull-request-time orchestration is not built — the
experiment below was recorded arm by arm with `scripts/record_one.py`, and the published CLI
verifies and gates on committed evidence rather than running agents itself.

The agent is the instrument. Three things get counted per run: how many distinct files it
had to read, how many test cycles it burned, how many lines of churn it produced.

And then the honest part, which is most of this post: **my experiment failed its own
validity check, and I shipped that result.**

```
$ morrow verify cassettes/treatment-replace-cache
ERROR · INVALID_EXPERIMENT · exit 2
report reproduced byte for byte, and the re-derived verdict is INVALID_EXPERIMENT:
null control FFR_gate 1.7403 exceeds maximum_ffr 1.2000
```

## How SigNoz fits in

The agent's trajectory is the raw material, and a trajectory is a trace. That correspondence
is exact enough that the mapping needed no cleverness:

```
morrow.experiment          ← one registered task, one candidate
 └── morrow.pair           ← one baseline/candidate comparison
      ├── morrow.run       ← one agent execution
      │    ├── file.read
      │    ├── patch.apply
      │    ├── command.run
      │    └── test.run
      └── morrow.run
```

Two arms of a pair sit side by side under the same parent, so **the difference between them
is the shape of the trace**, not a number you have to go look up. In the recorded pair, the
baseline run reads 18 files, burns 1 test cycle and changes 129 lines — 122 added, 7
deleted; the candidate reads 16, burns 4, and changes 487 — 324 added, 163 deleted. You can
see that without reading a single number: one subtree is visibly denser.

Three practical things I learned wiring this up.

**Exporting without an error is not evidence that anything landed.** My first export
"succeeded" and stored nothing I could find. I stopped trusting the exporter's return value
and wrote a readback that queries ClickHouse directly:

```bash
uv run python scripts/signoz_query.py --minutes 15
```

```
file.read        220
patch.apply       88
command.run       78
test.run          27
agent.session     14
agent.complete    14
morrow.run        14
morrow.pair        7
morrow.experiment  3
```

It goes to ClickHouse rather than through the SigNoz HTTP API deliberately: no session, no
token, nothing to keep in a file, and the answer is the stored rows rather than a rendering
of them. The query filters by service and time window, so those counts describe a clean
single-export window rather than being a fingerprint of the cassettes.

**The collector could not register until the first organisation existed.** In my Foundry
deployment it looped on `cannot create agent without orgId` while looking otherwise
healthy. I spent a while debugging my exporter for that.

**Not every event deserves a span.** Across the three published cassettes, 1,112 events
carry no measurement signal against 441 that do. The quiet ones stay in the published
evidence — a reader can count them — but they never become spans. A trace that is
five-sixths noise is not observability.

I also left one field out on purpose. Replayed runs omit the **measured** wall-clock
duration. Wall time is a property of the machine that did the recording, not of the work
the task required, so it is not in the published evidence. (Spans still carry ordinary
OpenTelemetry start and end times — those reflect the replay, not the original run.)

## The result, including the part that did not work

Ten container runs. One task, one model (`claude-sonnet-5`), identical limits.

**Churn came out separated under either arm ordering.** The treatment pairs produced 3.7538,
5.1111 and 4.0000. The null control — two clones of the *same* tree, so anything it measures
is run-to-run variation — produced at most 2.2167 across both orderings.

That is a **descriptive observation, not a finding.** The experiment was invalidated (below),
so its report deliberately carries no component ratios and no aggregate at all. Nothing here
establishes that the coupled candidate *caused* the extra churn; three pairs under an
invalidated null cannot carry that weight.

**The aggregate did not separate, and this is where it gets interesting.** A null control is
symmetric: both arms are the same tree, so which clone you call "baseline" is arbitrary.
But MORROW aggregates one-sided — improvements are floored at 1, so a 10× regression on one
axis cannot be cancelled by a 0.1× improvement on another. One-sided aggregation is *not*
symmetric. Swapping the labels moved the null from **1.0000 to 1.7403**, straight past the
published tolerance band of 1.20.

The design had anticipated this exact failure mode, in writing, before the data existed:

> The cost of one-sided aggregation is that even symmetric noise biases upward. The null
> control is what cancels that bias.

The null did its job. What it told me is that relabelling this two-pair null pushed it to
1.7403 — above the 1.2000 tolerance. That invalidates *this* experiment under the rule; it
is not an estimate of a general noise floor.

The rule for a null outside its band — fixed in the evaluator snapshot before the treatment
was recorded — is to report the experiment as `INVALID_EXPERIMENT`, not to widen the band.
So that is what the published cassette records, what CI asserts, and what the demo video
opens on.

I published **both** arm orderings, so neither is hidden. (Repository history is evidence of
chronology, not independently verifiable pre-registration — Git timestamps are mutable, and
the project's own design notes say not to claim more than that.)

For completeness: the treatment's own aggregate works out to `FFR_gate` = 2.0801, past the
1.50 threshold. I am stating it here rather than in the report because it decided nothing —
the experiment was invalidated before it could. Publishing the number and publishing the
reason it went unused are the same obligation.

`files_read_distinct` came out mixed in direction (0.8947, 0.8095, 1.4167 — the third pair
points the other way), so these runs show no consistent effect on it. It is in the report,
not dropped.

## Making the verdict checkable rather than asserted

A dashboard you have to trust is not much better than a claim you have to trust. So the
evidence for each experiment is committed to the repository as a **cassette**: normalized
events, churn counts, launcher exit codes, the evaluator policy that was in force, and both
report surfaces.

`morrow verify` re-derives the verdict from those files in a fixed order — digests, closed
schemas, metrics rebuilt from the normalized events, the churn records and the launcher exit
codes (with test counts cross-checked against the event stream), the verdict recomputed,
then the report regenerated and compared byte for byte. Nothing in the recorded report's
contents is an input to that decision. Byte reproduction is claimed within a platform:
`log`/`exp` may differ in the last bit across C libraries, which would show up as
`EVIDENCE_STALE` on an arithmetically correct reproduction.

Which raises the question the whole design turns on: **a cassette arrives from the pull
request under review.** The person whose change is being measured controls its bytes.

Six rounds of review — OpenAI Codex, security and quality passes, and my own rechecks —
went looking for ways a cassette could choose its own verdict. They found, among others:

- It supplied the **thresholds that judged it**. Set `friction_threshold` to 9 and a
  regression passed.
- It **asserted its own success**. Nothing tied `status: ok` to the launcher ever exiting
  zero.
- It could **reuse one run as two repetitions**, satisfying the minimum pair count on a
  single measurement.
- A `null_control_ffr_gate` of `0.0` reached `math.log` and **crashed** — a traceback where
  a verdict belongs, exiting 1, which in gate mode means "friction found".
- Retries were uncapped, so an arm could be run repeatedly with **the cheapest attempt
  adopted**.

By the fourth round the same shape kept recurring, so I stopped patching individually and
wrote down the rule they were converging on:

> **A manifest carries labels and pointers, not findings.**

Which arm a run belongs to, which pair, which files hold its evidence — structure, nothing
to derive it from. Everything that *decides* is re-derived: run success from the launcher's
exit codes, test cycles cross-checked against the event stream, the permitted file set from
the manifest's own shape.

One field could not be saved by that rule. `regression_detected` had no corresponding
artifact in a cassette — it could only ever be an assertion, and one that *suppresses* a
finding, which is the direction a candidate has reason to lie in. I deleted it.

One value still resists the rule and is disclosed rather than fixed: a treatment's recorded
`null_control_ffr_gate` comes from a *different* experiment, which is not shipped inside the
treatment cassette. It is checked against the published band but cannot be re-derived there.
Both null cassettes are published so you can recompute it — a reason to believe the number,
not a proof of it.

Every hole above is now a test that reproduces the attack and asserts the state that stops
it. 212 tests, and CI verifies all three published cassettes on every pull request and every
push to `main`, asserting both the exit code and the reported state — `0 / 2 / 2`, because
two of them are supposed to fail.

## What I am not claiming

- **No statistical significance.** Three compared pairs put a one-sided sign test's floor at
  1/8 = 0.125. The report says so in whichever form is true for its sample size.
- **No safe execution against untrusted repositories.** The container needs the model API,
  so `--network none` is not available.
- **No tamper resistance.** The digests detect accidental corruption and recomputation
  detects a report that no longer follows from its evidence. Neither is a signature.
- **A treatment's recorded null figure is not verifiable from that cassette alone.** It comes
  from a different experiment. Both null cassettes are published so you can recompute it —
  a reason to believe the number, not a proof of it.
- **Proxy validity is bounded to a fixed provider, model and prompt.** These are three
  counts that correlate with effort under those conditions, not a general measure of
  maintainability.
- **Reproducible deployment is not claimed.** The casting files are committed, but several
  components resolve to `latest`, so an identical stack is not guaranteed.

## Try it

```bash
git clone https://github.com/ichiburn/morrow && cd morrow
uv sync --all-groups --locked

uv run morrow verify cassettes/null-control-as-recorded    # exit 0: reproduced, in band
uv run morrow verify cassettes/null-control-arms-swapped   # exit 2: reproduced, out of band
uv run morrow verify cassettes/treatment-replace-cache     # exit 2

uv run python scripts/export_cassettes.py                  # → SigNoz on :4317
uv run python scripts/signoz_query.py --minutes 15         # read back what stored
```

## AI disclosure

**Anthropic Claude Code** wrote the implementation, the SigNoz and OpenTelemetry
integration, the tests, the documentation, and the script that builds the demo video.
**OpenAI Codex** performed adversarial design review before implementation began (three
review stages across four independent executions — R2 was run twice) and code review
afterwards, alongside security and quality review passes. **ChatGPT** contributed product
architecture, planning, and document preparation. This blog post was drafted by Claude Code
and fact-checked against the repository by Codex, which found several overstatements in the
first draft. Every generated change was reviewed and validated by me.

---

The thing I would want a reviewer to take from this is not the churn separation. It is that
the instrument was built so it could report a result I did not want, and then it did.
