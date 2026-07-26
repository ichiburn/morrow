# I built a CI gate that measures future change friction. It failed its own check.

*Submitted to Agents of SigNoz, Track 01: AI & Agent Observability. Publish on Dev.to,
Medium or Substack — the submission form requires a blog, and a LinkedIn post does not
count as one.*

---

Every CI gate you have ever added answers a question in the present tense. Do the unit
tests pass. Do the components fit together. Is a known vulnerability present. All useful,
all about now.

None of them can tell you that the pull request in front of you just made next month's work
more expensive. That question has no test, so nothing blocks on it, so it accumulates.

MORROW is an attempt to make it measurable. A future change task is registered in advance.
When a pull request arrives, the same task is run against **both** `main` and the candidate
— same model, same prompt, same resource limits, each run in its own container — and the
difference in what the agent had to do is the measurement.

The agent is the instrument. Three things get counted per run: how many distinct files it
had to read, how many test cycles it burned, how many lines it actually changed.

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
is the shape of the trace**, not a number you have to go look up. In the recording I
published, the baseline run reads 18 files, burns 1 test cycle and writes 129 lines; the
candidate reads 16, burns 4, and writes 487. You can see that without reading a single
number — one subtree is visibly denser.

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
of them.

**The collector cannot register until the first organisation exists.** Before you create
one, it loops on `cannot create agent without orgId` while looking otherwise healthy. I
spent a while debugging my exporter for that.

**Not every event deserves a span.** One run produced 108 events that carried no measurement
signal against 22 real actions. Those stay in the published evidence — a reader can count
them — but they do not become spans. A trace that is five-sixths noise is not observability.

I also left one field out on purpose. Replayed runs carry **no wall-clock duration**. Wall
time is a property of the machine that did the recording, not of the work the task required,
so it is not in the published evidence. Exporting a zero would have put a number on a
dashboard that nobody measured.

## The result, including the part that did not work

Ten container runs. One task, one model (`claude-sonnet-5`), identical limits.

**Churn separated, and it separated under either arm ordering.** Every treatment pair landed
between 3.75 and 5.11. The null control — two clones of the *same* tree, so the difference
between its arms is by construction zero — stayed under 2.22 in either ordering. Three of
three pairs clear of the noise floor.

**The aggregate did not separate, and this is where it gets interesting.** A null control is
symmetric: both arms are the same tree, so which clone you call "baseline" is arbitrary.
But MORROW aggregates one-sided — improvements are floored at 1, so a 10× regression on one
axis cannot be cancelled by a 0.1× improvement on another. One-sided aggregation is *not*
symmetric. Swapping the labels moved the null from **1.0000 to 1.7403**, straight past the
published tolerance band of 1.20.

The design had anticipated this exact failure mode, in writing, before the data existed:

> The cost of one-sided aggregation is that even symmetric noise biases upward. The null
> control is what cancels that bias.

The null did its job. What it told me is that at this sample size, my noise floor reaches
past my threshold.

The pre-registered rule for a null outside its band is to report the experiment as
`INVALID_EXPERIMENT` — not to widen the band. So that is what the published cassette
records, what CI asserts, and what the demo video opens on.

I committed **both** arm orderings, so neither can be the one I picked after seeing the data.

For completeness: the treatment's own aggregate works out to `FFR_gate` = 2.0801, past the
1.50 threshold. I am stating it here rather than in the report because it decided nothing —
the experiment was invalidated before it could. Publishing the number and publishing the
reason it went unused are the same obligation.

`files_read_distinct` showed no signal whatsoever (0.89, 0.81, 1.42 — the third pair points
the other way). It is in the report, not dropped.

## Making the verdict checkable rather than asserted

A dashboard you have to trust is not much better than a claim you have to trust. So the
evidence for each experiment is committed to the repository as a **cassette**: normalized
events, churn counts, launcher exit codes, the evaluator policy that was in force, and both
report surfaces.

`morrow verify` re-derives the verdict from those files in a fixed order — digests, closed
schemas, metrics recomputed from the events, the verdict recomputed, then the report
regenerated and compared byte for byte. Nothing in the recorded report's contents is an
input to that decision.

Which raises the question the whole design turns on: **a cassette arrives from the pull
request under review.** The person whose change is being measured controls its bytes.

Six rounds of adversarial review (OpenAI Codex, plus security and quality passes) went
looking for ways a cassette could choose its own verdict. They found, among others:

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

Every hole above is now a test that reproduces the attack and asserts the state that stops
it. 212 tests, and CI verifies all three published cassettes on every push with their exit
codes asserted per cassette — `0 / 2 / 2`, because two of them are supposed to fail.

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

Anthropic Claude Code wrote the implementation, the SigNoz and OpenTelemetry integration,
the tests, and the documentation. OpenAI Codex performed adversarial design review (four
rounds before implementation began) and code review (six rounds after). ChatGPT was used for
product architecture and planning. Every generated change was reviewed by me.

---

The thing I would want a reviewer to take from this is not the churn separation. It is that
the instrument was built so it could report a result I did not want, and then it did.
