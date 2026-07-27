# MORROW

**Future Friction Gate for AI-native CI/CD**

> This pull request passes every test.
> But does it make the next change harder?

---

## What this is

MORROW uses an **AI coding agent as a measuring instrument**.

A future change task is registered in advance. MORROW then runs that same task against
**both** `main` and the candidate pull request, under identical model, prompt, and resource
constraints, repeated as paired runs. The agent's work trajectory — how many distinct
files it had to read, how many test cycles it burned, how many lines it actually changed —
is exported to SigNoz over OpenTelemetry, and the **difference between the two sides** is
computed.

The evidence from a recording is committed as a **cassette**, and `morrow verify`
re-derives the verdict from it. [Results](#results-recorded-2026-07-25) below are published
that way: three cassettes, verifiable without trusting this README.

**This prototype implements recording, evidence verification and gating. Pull-request
orchestration is the next step** — the published experiment was recorded arm by arm with
`scripts/record_one.py`, and the CLI decides from committed evidence rather than running
agents itself.

Existing CI gates answer "does the code work now."
MORROW answers **"can this codebase still absorb the next change efficiently."**

| Existing gate | Question |
|---|---|
| Unit tests | Does the current logic work? |
| Integration tests | Do the current components fit together? |
| Security scan | Is a known vulnerability present? |
| **MORROW** | **Can the next change still be absorbed efficiently?** |

---

## What MORROW claims, and what it does not

An instrument is not trustworthy unless it states both its measurement range **and the range
it cannot measure**. This table is the contract.

### Claimed

| # | Claim | Basis | Strength |
|---|---|---|---|
| C1 | The difference in work required for the same future task, across two repository states under identical conditions, can be extracted **reproducibly** | `morrow verify` re-derives the verdict from the recorded evidence and compares the regenerated report byte-for-byte with the recorded one | **Strong** — mechanically checkable |
| C2 | For a given experiment, MORROW reports the observed per-component difference between baseline and candidate, without selection | All paired-run ratios are published, not just the median | **Strong** — a capability claim, not a result claim |
| C3 | Any observed difference is reported alongside a null control acquired in the same recording session | Null and treatment run through an identical procedure, and **both arm orderings of the null are published** | **Medium** — a comparison, not a significance test |
| C4 | The decision is deterministic and reproducible from the evidence | `verify` runs in CI on every push, over every committed cassette, with the expected exit code asserted per cassette | **Strong** |

**C2 and C3 are claims about the procedure, not about the outcome.** Whether the coupled
candidate actually turns out to cost more is a *result*, and results are reported as measured
— including when they contradict the registered hypothesis.

### Not claimed

| # | Not claimed | Why |
|---|---|---|
| C5 | Safe execution against untrusted repositories | No OS-level isolation is implemented. **MORROW runs only against trusted repositories.** |
| C6 | Statistical significance | At the sample sizes here a one-sided sign test cannot reach it: over three compared pairs its p floor is 2⁻³ = 0.125. At larger K the floor drops below 0.05 and that argument stops applying — but significance is still not claimed, because one recording session on one model does not supply the independence such a test assumes. The report states whichever of the two is true for its K |
| C7 | Tamper resistance of the evidence | There is no signing and no provenance. The hashes detect **accidental corruption**; recomputation detects a report that no longer follows from its evidence. Neither is a signature |
| C9 | That a treatment's recorded null control is verifiable from that cassette alone | It is a number produced by a **different** experiment, which is not shipped inside the treatment cassette. `verify` checks it against the published tolerance band, but cannot confirm it was the number that experiment actually produced. Both null cassettes are published so a reader can recompute it themselves — that is a reason to believe it, not a proof |
| C8 | Robustness against an adversarial agent | These are proxy metrics valid under a fixed provider, model, and prompt |

---

## What is measured

Three components, compared **per paired run**.

| Component | What it stands for | Primary source |
|---|---|---|
| `files_read_distinct` | How much of the codebase had to be understood | Agent tool calls |
| `test_cycles` | How much trial and error was required | A fixed test launcher's execution log |
| `final_churn` | The physical volume of the implementation | Real file diff against the pre-run tree |

```
per pair p:      r[i,p] = clamp( (candidate[i,p] + α) / (baseline[i,p] + α),  1/R,  R )
per component i: r[i]   = median_p( r[i,p] )
                 FFR    = exp( Σ wᵢ · ln( max(1, r[i]) ) / Σ wᵢ )
```

**Improvements are floored at 1 (one-sided aggregation).** A two-sided geometric mean lets a
10× regression on one axis be cancelled by a 0.1× improvement on another, and slip through.
Friction is not a fungible quantity.

**Every `r[i,p]` appears in the report.** The median alone is not shown.

---

## The null control

Two independent clones of the *same* tree — zero difference between them — are run through
exactly the same procedure as the treatment, in the same recording session.

Whatever difference shows up there is pure run-to-run variance. That is the **noise floor of
the measurement**.

If the null control exceeds its published tolerance, the entire day's experiment is reported
as invalid. The threshold is not loosened to make the result pass.

The threshold itself is fixed in the published evaluator snapshot **before** any treatment data
is collected. It is **not** computed from the null control — doing so would let the null pass
itself under its own rule, which is circular.

---

## Results (recorded 2026-07-25)

Ten agent runs, one future task (`replace-cache`), one model (`claude-sonnet-5`), one
prompt, identical resource limits (`--cpus 4 --memory 8g`), each run in its own container.
Three cassettes are committed under [`cassettes/`](cassettes), and each one re-derives its
own verdict:

```
$ morrow verify cassettes/null-control-as-recorded
PASS · EVIDENCE_REPRODUCED · exit 0
verdict OK re-derived from the evidence; both report surfaces match byte for byte

$ morrow verify cassettes/null-control-arms-swapped
ERROR · INVALID_EXPERIMENT · exit 2
report reproduced byte for byte, and the re-derived verdict is INVALID_EXPERIMENT:
null control FFR_gate 1.7403 exceeds maximum_ffr 1.2000

$ morrow verify cassettes/treatment-replace-cache
ERROR · INVALID_EXPERIMENT · exit 2
report reproduced byte for byte, and the re-derived verdict is INVALID_EXPERIMENT:
null control FFR_gate 1.7403 exceeds maximum_ffr 1.2000
```

**Two of the three exit 2, and that is the result.** Reading the same tree against itself
produced a difference the published rule calls too large to measure through, so the day's
experiment is reported as invalid rather than as a finding. The treatment carries that same
figure — the worse of the two arm orderings, not the flattering one — and fails with it.

### The measured runs

| Pair | Baseline (`main`) | Candidate (coupled) | reads | test cycles | churn |
|---|---|---|---|---|---|
| 0 | `r0` | `r1` | 18 → 16 | 1 → 4 | 129 → 487 |
| 1 | `r2` | `r3` | 20 → 16 | 1 → 3 | 62 → 321 |
| 2 | `r4` | `r5` | 11 → 16 | 2 → 2 | 63 → 255 |

The null control is two clones of the *same* tree, so its two arms differ by nothing at
all: `r90` 15/3/132, `r91` 15/1/61, `r20` 16/1/132, `r21` 11/2/59.

### Per-pair ratios, as published in each cassette's report

| | pair | `files_read_distinct` | `test_cycles` | `final_churn` |
|---|---|---|---|---|
| **treatment** | 0 | 0.8947 | 2.5000 | **3.7538** |
| | 1 | 0.8095 | 2.0000 | **5.1111** |
| | 2 | 1.4167 | small-sample | **4.0000** |
| **null, as recorded** | 0 | 1.0000 | 0.5000 | 0.4662 |
| | 1 | 0.7059 | small-sample | 0.4511 |
| **null, arms swapped** | 0 | 1.0000 | 2.0000 | 2.1452 |
| | 1 | 1.4167 | small-sample | **2.2167** |

### What this says

**`final_churn` came out separated under either arm ordering.** The smallest treatment
ratio (3.7538) is above the largest ratio the null produces (2.2167), in all three pairs.
This is the strongest observation in the recording — and it is an observation: the
experiment was invalidated, so it establishes nothing about causation.

`test_cycles` points the same way but does **not** separate: the treatment median is 2.25,
and the null's is 0.50 as recorded but 2.00 with the arms swapped. Two of the three pairs
fell below the small-sample floor and were dropped rather than counted, which is most of
why so little is left.

### What this does not say

**The aggregate `FFR_gate` does not separate.** The null control is symmetric by
construction: both arms are the same tree, so which clone is labelled "baseline" is
arbitrary. One-sided aggregation is *not* symmetric, and swapping the labels moves the
null from **1.0000** to **1.7403** — across the published tolerance band of 1.20.

The pre-registered rule for a null outside its band is to report the experiment as
`INVALID_EXPERIMENT`, not to widen the band. Both null orderings are committed, so neither
can be the one that was picked after seeing the data, and the treatment carries the worse
of the two. All three verdicts and their exit codes are asserted in CI.

For completeness: the treatment's own aggregate works out to **`FFR_gate` = 2.0801**, past
the 1.50 threshold. That number is stated here rather than in the report because it did not
decide anything — the experiment was invalidated before it could. Publishing it and
publishing the reason it was not used are the same obligation.

`files_read_distinct` carries no signal here: 0.8947, 0.8095, 1.4167 — the third pair
points the other way. It is reported rather than dropped.

**No statistical significance is claimed.** Three pairs put the floor of a one-sided sign
test at 1/8 = 0.125.

*(The design anticipated this failure mode: "the cost of one-sided aggregation is that
even symmetric noise biases upward — the null control is what cancels that bias." The
null did its job. The instrument reported that it could not separate the aggregate, which
is the outcome it was built to be able to report.)*

### Reproducing this

```bash
uv sync --all-groups --locked
uv run morrow verify cassettes/null-control-as-recorded    # exits 0: reproduced, in band
uv run morrow verify cassettes/null-control-arms-swapped   # exits 2: reproduced, out of band
uv run morrow verify cassettes/treatment-replace-cache     # exits 2, as CI asserts
```

`verify` reads only the cassette. It checks every digest, parses every event under the
closed schema, recomputes the metrics from the events and the churn records, recomputes
the verdict with the policy embedded in the manifest, regenerates both report surfaces and
compares them byte-for-byte. Nothing in the recorded report's *contents* is an input to
that decision.

### Seeing it in SigNoz

```bash
uv run python scripts/export_cassettes.py          # → localhost:4317
uv run python scripts/signoz_query.py --minutes 15 # read back what actually landed
```

Each experiment becomes one trace — `morrow.experiment` → `morrow.pair` → `morrow.run` →
one span per agent action — so the two arms of a pair sit side by side and the difference
between them is the shape of the trace, not a number in a table. The three published
cassettes produce 3 experiment, 7 pair and 14 run spans over 441 event spans — 413
actions plus 28 session/completion.

Every span is **re-derived by the verifier before it is sent**: the verdict on the trace is
the one `morrow verify` gives for the same directory. Experiment, pair and run spans are tagged
`morrow.evidence_mode=replay` so a dashboard filtered to live measurements does not pick
them up (event spans inherit the trace rather than the attribute), and they carry no
wall-clock duration — that is a property of the machine that recorded them, not of the work
the task required, so it is not in the published evidence and a replay has none to report.

> **The committed SigNoz deployment is local-only.** `casting.yaml.lock` is the resolved
> configuration `foundryctl` generated, committed so the deployment is reproducible from
> this repository alone — which means it also carries that stack's default credentials in
> plain sight. Do not expose this configuration beyond localhost without changing them.

`gate` runs the same steps but stops before the report comparison, and it will not decide
under a policy the cassette supplied: the thresholds, the metric parameters **and the
sample-size floors** have to match the evaluator's own, or it returns
`GATE_PRECONDITION_UNMET`. **A candidate does not get to choose the bar it is measured
against** — including by declaring that one pair is enough. Only `runs_per_variant` is
exempt, because that is how many pairs were planned rather than how many the decision
requires.

It also decides only on a cassette that declares itself a treatment, and only on one that
carries a null control result. Both are gate preconditions in the design, and both were
reachable by editing a single field before that was enforced — a treatment relabelled as a
null control skipped the null check entirely, because `verify` catches the relabelling only
via the report and `gate` never reads the report.

More generally, a manifest carries **labels and pointers, not findings**. Which arm a run
belongs to and which files hold its evidence are structure; there is nothing to derive them
from. Everything that *decides* is re-derived: run success from the launcher log, test
cycles cross-checked against the event stream, the permitted file set from the manifest's
own shape, and a run cannot be adopted unless its stream ends with the completion event
that proves it is whole. Retries are capped and a discarded attempt has to be one that did
not finish, so an arm cannot be run repeatedly with the cheapest result adopted.

Two caveats worth stating rather than discovering:

- **Reproduction is claimed within a platform, not across all of them.** An FFR is
  `exp(Σ w·ln r / Σ w)`, and `log`/`exp` may differ in the last bit between C libraries, so
  a byte comparison on a different platform could report `EVIDENCE_STALE` for a
  reproduction that is arithmetically correct. The verdict itself is unaffected: threshold
  comparisons go through `Decimal` with an epsilon.
- **A cassette is untrusted input.** It is read with limits on file size, total size and
  file count, symlinks are refused, and the set of files it may contain is derived from
  the manifest's own structure rather than from its digest table. What is *not* claimed is
  tamper resistance (C7) — the digests detect accidental corruption, and recomputation
  detects a report that no longer follows from its evidence, but neither is a signature.

---

## Design

| Document | Contents |
|---|---|
| [design.md](docs/architecture/design.md) | Claims and their basis, measured constraints |
| [measurement.md](docs/architecture/measurement.md) | Trust boundary, paired runs, components, churn, null control |
| [evidence.md](docs/architecture/evidence.md) | Decision state machine, evidence validation, event model |
| [operations.md](docs/architecture/operations.md) | Execution and isolation, scope, demo, critical path |
| [review-log.md](docs/architecture/review-log.md) | Four rounds of adversarial review, run before implementation began |

The review log is worth reading. Five claims were **withdrawn** during design review because
no implementation could have supported them.

---

## AI disclosure

This project is built with the following AI tools.

| Tool | Use |
|---|---|
| Anthropic Claude Code | Implementation, SigNoz / OpenTelemetry integration, testing, documentation |
| OpenAI Codex | Adversarial design review (four rounds), code review |
| ChatGPT | Product architecture, planning, document preparation |

Every generated change is reviewed and validated by the author.

---

## Status

**Working, with one measured experiment published.** The gate, the verifier and the
cassette format are implemented and covered by tests; the recording driver
(`scripts/record_one.py`) is still a script rather than a `morrow measure` subcommand.

Agents of SigNoz hackathon, 2026-07-20 – 07-26 · Track 01: AI & Agent Observability
