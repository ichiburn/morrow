# MORROW

**Future Friction Gate for AI-native CI/CD**

> This pull request passes every test.
> But does it make the next change harder?

---

## What this is

MORROW uses an **AI coding agent as a measuring instrument**.

A future change task is registered in advance. MORROW then runs that same task against
**both** `main` and the candidate pull request, under identical model, prompt, and resource
constraints, repeated as four paired runs. The agent's work trajectory — how many distinct
files it had to read, how many test cycles it burned, how many lines it actually changed —
is exported to SigNoz over OpenTelemetry, and the **difference between the two sides** is
computed.

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
| C1 | The difference in work required for the same future task, across two repository states under identical conditions, can be extracted **reproducibly** | `morrow verify` re-derives the verdict from the recorded evidence | **Strong** — mechanically checkable |
| C2 | For a given experiment, MORROW reports the observed per-component difference between baseline and candidate, without selection | All paired-run ratios are published, not just the median | **Strong** — a capability claim, not a result claim |
| C3 | Any observed difference is reported alongside a null control acquired in the same recording session | Null and treatment run through an identical procedure | **Medium** — a comparison, not a significance test |
| C4 | The decision is deterministic and reproducible from the evidence | `verify` runs in CI on every push | **Strong** |

**C2 and C3 are claims about the procedure, not about the outcome.** Whether the coupled
candidate actually turns out to cost more is a *result*, and results are reported as measured
— including when they contradict the registered hypothesis.

### Not claimed

| # | Not claimed | Why |
|---|---|---|
| C5 | Safe execution against untrusted repositories | No OS-level isolation is implemented. **MORROW runs only against trusted repositories.** |
| C6 | Statistical significance | With four paired runs, the lower bound on a one-sided sign test is 1/16 = 0.0625. This **cannot** be claimed |
| C7 | Tamper resistance of the evidence | There is no signing and no provenance. The hashes detect **accidental corruption**, nothing more |
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

**In development.**
Agents of SigNoz hackathon, 2026-07-20 – 07-26 · Track 01: AI & Agent Observability
