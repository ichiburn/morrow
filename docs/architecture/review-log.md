# Design Review Log

Before implementation began, the MORROW design went through **three rounds of
adversarial review**. The reviewer was OpenAI Codex (`codex exec --sandbox
read-only`). Each round ran under the instruction "list only the defects; no
praise, no summary."

| Round | Target | MUST_FIX | SHOULD_FIX | Outcome |
|---|---|---:|---:|---|
| R1 | design v2.0 | 15 | 5 | All applied in v2.1 |
| R2-a | design v2.1 | 19 | 9 | **Changed the design approach itself** (v3) |
| R2-b | design v2.1 (independent run) | 16 | 9 | 5 findings not in R2-a additionally applied |
| R3 | design v3 | 16 | 5 | **Matched claim strength to the evidence** (v4) |

R2 ran twice independently against the same document. Most findings coincided,
but 5 defects were found by only one of the two runs.

---

## What changed in each round

### R1 → v2.1

The first version's issues centered on holes in the measurement model.

- The geometric mean's denominator diverged at 0 or 1 → introduced smoothing and clamping
- `duration` was included in the friction ratio → API latency is noise, so it was excluded
- Concealment was limited to the command string → unified the projection boundary
- We depended on `--max-turns` → **it was a flag that does not exist**

### R2 → v3 (change of design approach)

The 19 findings were not individual defects; they all derived from **a single
structural impossibility**.

> "Measure an untrusted PR with a single run of a non-deterministic agent, and
> block the PR on that result."

This cannot be built. The problems it produced:

- Replaying a fixed cassette does not evaluate the current PR
- Evaluating a fork PR runs untrusted code
- If the PR itself rewrites the policy and tests, it can grade itself
- `INVALID_RUN` and `BASELINE_FAILED` exited 0, so an evaluator failure turned green

→ We **separated the evaluator domain from the measured domain**, redefined
replay as "a verifier of whether the decision reproduces from the evidence," and
narrowed the scope of the claim to "an opt-in gate for trusted repositories."

### R3 → v4 (match claim strength to the evidence)

The 16 findings split into "specification bugs" and "claims that no
implementation could support."

**Specification bugs** (all fixed):

- The pre snapshot was held as `(size, hash)`, but **a line-level diff cannot be reconstructed from that**
- We took the median per variant before computing the ratio, which **lost the meaning of the paired run**
- `measure` mode always exited 0, so the fail-open closed in R2 had revived on a different path
- The repetition count and minimum-pair setting made `ADAPTATION_REGRESSION` unreachable
- Making schema validation per-variant necessarily rejects correct evidence repeated K times
- Acceptance tests and regression tests were placed in the same location (acceptance is expected to fail before execution)
- The agent could install packages into the venv and pass acceptance (a `pip install` was observed empirically)

**Unsupportable claims** (withdrawn):

| Claim | Why it is unsupportable |
|---|---|
| "We can distinguish the difference from noise" | Even a K=4 sign test has a one-sided p lower bound of 1/16 = 0.0625 |
| "The threshold is not arbitrary because it is derived from the null control" | The safety margin itself is ungrounded. Judging the null by the same rule passes it automatically (circular) |
| "The agent cannot reach the evaluator's artifacts" | It runs under the same UID, so it can |
| "A third party can verify the pre-registration" | A tag's timestamp can be set, and tags can be moved or deleted |
| "The published artifacts contain no free strings" | The executable names and identifiers were strings too |

→ We withdrew the claims and instead **tabulated "what we claim and what we do
not claim" with the strength of the grounds** (`design.md` §0.1).

---

## Why we keep this record

MORROW calls itself an "instrument." An instrument is not trusted unless it can
state its measurement range and the range it cannot measure. Keeping a record of
what we gave up at the design stage is part of that.
