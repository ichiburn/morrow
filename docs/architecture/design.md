# MORROW Implementation Design v4 — Implementation Baseline

* **Revised**: 2026-07-25 06:00 JST
* **History**: v1 spec → v2.0 → R1(15) → v2.1 → R2-a(19) / R2-b(16) → v3 → **R3(16)** → this document
* **Time remaining**: ~27 hours until the internal submission deadline of 2026-07-26 09:00 JST (solo)

---

## 0. What changed in v4

The 16 MUST_FIX items from R3 fell into two groups.

**Group A: spec bugs** (fixable) — all fixed.
**Group B: claims no implementation can support** — **the claim was withdrawn.**

| Group B claim | Why it can't be supported | v4 treatment |
|---|---|---|
| "The difference is distinguishable from noise" | A sign test at K=3 has a **one-sided p floor of 1/8 = 0.125**; even K=4 gives 1/16 = 0.0625 | **Claim no statistical significance.** Present the observation that, under a rule fixed in the published evaluator snapshot, the result exceeded a null control collected concurrently |
| "The threshold isn't arbitrary because it's derived from the null" | `floor=1.30` / `safety_factor=1.30` / `maximum_ffr=1.20` are themselves ungrounded. Judging the null by `FFR_null×1.30` passes it automatically (**circular**) | **Drop the "derivation."** The threshold is a decision rule registered before the treatment data is seen; the null sits alongside it on the same screen, leaving the reader to judge |
| "The agent cannot reach the evaluator domain" | Same UID, under the same `experiment_root`. Directly contradicts §7.2's "can reach the parent directory" | **Withdraw the trust-boundary claim.** Keeping assets outside the worktree is accident prevention; it does not stop an adversarial agent, and this is stated plainly |
| "A third party can verify the pre-registration" | A tag's timestamp can be set, and tags can be moved or deleted | **Do not use the term "pre-registration."** Call it a "published snapshot of evaluation assets" and state the limitation |
| "The published output contains no free-form strings at all" | `executable` / `raw_kind` / `counters` keys / the various IDs are all free-form strings | **Close it for real** with a discriminated union + enum + opaque ID (§6) |

**v4 design principle**: match the strength of a claim to the strength of the evidence that supports it.

---

## 0.1 Claims and their basis (with strength)

| # | Claim | Basis | Strength |
|---|---|---|---|
| C1 | The same future task can be run against two repository states under identical conditions and the difference in work extracted reproducibly | Implementation + recording + re-derivation via `verify` | **Strong** (mechanically verifiable) |
| C2 | In this setup, the candidate that introduced coupling required more file reads, attempts, and changed lines | Observed values plus the raw data for every pair | **Medium** (an observation for this environment, this task, this model) |
| C3 | The observed difference exceeded the range of a null control collected concurrently | Null and treatment collected by the same procedure | **Medium** (a comparison, not significance) |
| C4 | The decision is deterministic and reproducible from the evidence | `verify` runs on every CI run | **Strong** |
| C5 | Untrusted repositories can be run safely | — | **Not claimed** |
| C6 | The difference is statistically significant | — | **Not claimed** (impossible in principle at K=4) |
| C7 | The evidence resists tampering | — | **Not claimed** (no signatures; hashes detect corruption) |
| C8 | The metric is robust against an adversarial agent | — | **Not claimed** |

The README, submission text, and demo video **reproduce this table verbatim.**

---

## 1. Constraints found by measurement (verified)

### 1.1 Environment

| Item | Measured value | Impact |
|---|---|---|
| Python / uv / mise | 3.12.13 / 0.7.19 / 2026.7.7 | OK |
| Docker | Server 29.5.3 / 6 CPU / 15.62 GiB | **No socket access from the sandbox** → G1 is run by a human on the host |
| `foundryctl` | Not installed | Top priority for G1 |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | **Both unset** | Real agent runs are **local only**; impossible in CI |

### 1.2 Actual shape of Claude Code `stream-json` (full breakdown of the 76 lines collected)

Recomputed with `jq`, confirming the total matches.

| Count | `type` / `subtype` | Normalization |
|---:|---|---|
| 30 | `system` / `thinking_tokens` | `OPAQUE` |
| 22 | `assistant` | Split per `content[].tool_use` |
| 10 | `user` | `content[].tool_result` matched by `tool_use_id` |
| 4 | `system` / `commands_changed` | `OPAQUE` (does not appear after isolation) |
| 3 | `system` / `hook_started` | `OPAQUE` (does not appear after isolation) |
| 3 | `system` / `hook_response` | `OPAQUE` (does not appear after isolation) |
| 1 | `system` / `notification` | `OPAQUE` |
| 1 | `system` / `init` | `SESSION_START` |
| 1 | `result` / `success` | `COMPLETION` |
| 1 | `rate_limit_event` | `OPAQUE` |
| **76** | | |

These 76 lines are ingested as a fixture; a contract test pins the per-`raw_kind` counts and zero unclassified lines.

### 1.3 `--max-turns` does not exist

It is not in `claude --help`. `num_turns` appears only in the terminal `result`, so it cannot enforce a cap during a run.
`--bare` requires `ANTHROPIC_API_KEY` and is therefore unusable (observed: `terminal_reason: api_error`).
→ Only wall-clock time and budget are enforced. Step count is observed and recorded but not capped (§7.1).

### 1.4 Nested execution inherits the host environment and breaks → solved by isolation (demonstrated)

| | Before isolation | After isolation |
|---|---|---|
| `permission_denials` | Bash entirely blocked | **0** |
| hook-derived events | mixed in | **none** |
| result | "test execution is blocked" | `is_error=false, num_turns=9, cost=$0.159` |

**Byproduct (important)**: the agent hit a missing `pytest` → failure → **`pip install`** → re-run.
→ **The agent can rewrite its execution environment.** This is the direct basis for the venv design in §3.5.

---

---

## Document structure

The design is split by concern.

| Document | Contents |
|---|---|
| **design.md** (this document) | What changed / claims and their basis / constraints found by measurement |
| [measurement.md](measurement.md) | Trust boundary, paired runs, components, churn, success criteria, null control, numeric consistency |
| [evidence.md](evidence.md) | The decision state machine and exit codes, evidence verification, the normalized event model |
| [operations.md](operations.md) | Execution and isolation, scope, demo design, layer enforcement, SigNoz, published snapshot, test strategy, critical path, official requirements, risks |
| [review-log.md](review-log.md) | Record of the three adversarial review rounds run before implementation |
