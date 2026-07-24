# MORROW Execution, Scope, and Operations

> For the full design overview, see [design.md](design.md).

## 7. Execution and isolation

```
<work_root>/<run_id>/            worktree (agent's cwd)
<state_root>/<experiment_id>/
    plan.json  policy/  pack/  regression-tests/  acceptance-tests/
    agent-home/<run_id>/         ← ★ isolated per run (cloned from a read-only template)
    venvs/<run_id>/              ← outside the worktree
    snapshots/<run_id>.pre/
    launcher-log/<run_id>.jsonl
```

| Item | Implementation |
|---|---|
| Stopping the process group | `setsid` → `killpg(SIGTERM)` → `SIGKILL` after a grace period → **confirm the process group is gone before the post snapshot** |
| Budget cap | `--max-budget-usd` |
| Wall-clock cap | `asyncio.wait_for` |
| Config isolation | Clone `agent-home` per run to prevent session/cache leakage |
| Run order | Equal numbers of AB and BA, fixed in the published evaluator snapshot (plan.json) |
| Concurrency | **1 (sequential)**. Running in parallel lets resource contention leak into the treatment difference |
| Variant concealment | **Strip `baseline` / `candidate` from cwd names, prompts, and environment variables.**<br>Use only equal-length opaque `run_id`s; the evaluator holds the mapping |

### 7.1 Steps are observed but not enforced

`agent_steps = number of distinct tool_ref`.
However, **no step cap is enforced in P0** (including parallel tool_use within a
single event, because we cannot yet verify that the process can reliably be
stopped). We record only `observed_steps` and make no `limit_enforced` claim.
Only wall-clock time and budget are enforced.

### 7.2 This is not a security boundary

As stated in §2. The worktree, `cwd`, `CLAUDE_CONFIG_DIR`, and process group are
not a defense against host compromise. Run this only on trusted repositories.

---

## 8. Scope (genuinely reducing P0)

Following the R3 findings, we **reduce rather than add features**.

### P0

| Area | Contents |
|---|---|
| provider | Claude Code only |
| future task | 1 |
| scenarios | 2: `null` and `coupling` |
| repetitions | K=4 pairs × 2 scenarios = **16 runs nominal** (up to **48** with retries) |
| components | **3** (`files_read_distinct` / `test_cycles` / `final_churn`) |
| modes | `measure` / `verify` / `gate` (`gate` **recomputes evidence validation, metrics, and the verdict from the cassette**; the recorded report is not an input — §4.6) |
| decision | the state machine in §4.2 |
| observability | OTel → SigNoz, 2 trajectories + 1 dashboard screen (manual import allowed) |
| output | `morrow-report.md` / `morrow-report.json` |
| tests | unit (friction / decision / normalization / projection / churn) / contract (76-line fixture, golden bytes) / architecture / e2e (`verify`) |
| docs | README (including the claims table from §0.1), demo video, AI-usage disclosure |

### P1 and beyond (no clause to fold anything back into P0)

Codex adapter / `fixed` scenario / generalizing ASR / per-test-ID spill /
routing SigNoz into the decision / `--audit-signoz` / MCP / alert automation /
general-purpose shell-grammar parsing / OS-level isolation / signing and
provenance / demo-repo automation

### Cost and time

| | 1 run | P0 total |
|---|---|---|
| Cost | $0.16–0.59 (measured) | **$3–10** for 16 runs nominal, up to **~$8–28** at the maximum 48 runs |
| Duration | 3–8 min | **50–130 min sequential** for 16 runs, up to **~6.5 h** at 48 (never parallel) |

---

## 9. Demo design

| ID | baseline | candidate | Hypothesis fixed in the published evaluator snapshot |
|---|---|---|---|
| `null` | independent clone A of `main` | independent clone B of `main` | `FFR_gate ≤ 1.20` |
| `coupling` | `main` | `pr/1` (domain imports Redis directly) | `FFR_gate > 1.50` |

**"Getting the expected verdict" is not a completion criterion.**

| What happened | What we do |
|---|---|
| `null` fell outside the tolerance band | Mark all scenarios `INVALID_EXPERIMENT`. Do not loosen the threshold. Report "this environment could not separate the signal" |
| `coupling` did not BLOCK | **Report it as-is.** Do not re-record and cherry-pick a convenient result |
| Some pairs were invalidated | Keep them with a reason, and report the counts |

### 9.1 The structural cost difference

Future task: "Add an in-memory cache for local and test environments. Do not change the order-service API."

```
main : create orders/adapters/memory_cache.py + 1 line in composition.py    → 2 files
pr/1 : strip redis out of order_service / pricing / inventory / promotions,
       invent a new abstraction, and fix redis-specific tests               → 6–9 files
```

The invariant `orders.domain does not import redis` forces the "strip it all out"
path. The current tests pass for both (using `fakeredis` to make external-server
dependencies zero).

---

## 10. Architecture and layer enforcement

```
src/morrow/
├── domain/        pure. stdlib + pydantic only
│   ├── events.py  metrics.py  assessment.py
│   ├── friction.py     ★ friction computation (pure functions)
│   └── policy.py       ★ evaluate_policy / enforce (pure functions)
├── application/   validate_evidence / measure / verify
├── adapters/      claude/ fs/ git/ otel/ report/
└── cli/
```

| Layer | Allowed imports (positive allowlist) |
|---|---|
| `domain` | `morrow.domain`, `pydantic`, `enum`, `math`, `statistics`, `decimal`, `dataclasses`, `typing`, `collections.abc`, `hashlib` |
| `application` | the above + `morrow.application`, `abc`, `asyncio` |
| `adapters` | the above + `morrow.adapters` + any external |
| `cli` | everything |

`tests/architecture/test_layers.py` walks the AST and fails on any disallowed
`import`. Calls to `importlib` / `__import__` are caught by a separate rule.

**Honest statement of coverage**: this is a **check on static imports**. It does
not catch `eval` or indirect calls via attributes. The test's docstring says so.

---

## 11. SigNoz / OpenTelemetry

**Emit after the decision is made.** This structurally guarantees that a
telemetry failure cannot change the verdict.

| Surface | Contents |
|---|---|
| Traces | `morrow.experiment` → `morrow.pair` → `morrow.run` → each operation (**the agent's work trajectory itself**) |
| Metrics | per-pair values per component, `FFR_gate` / `FFR_display`, success count, valid-pair count |
| Logs | normalized events (no free text) and the decision rationale |
| Dashboard | commit `dashboards/morrow.json` |
| Alert | `morrow.future_friction_ratio > threshold` (for the demo; the authority for the decision is the policy engine) |

**Do not route SigNoz into the decision.** Asynchronous emission, ingestion lag,
and duplicate submission would let the same evidence yield a different verdict,
which would make C4 (determinism) a lie.

---

## 12. Published evaluator snapshot (not called "pre-registration")

As the R3 findings note, an annotated tag's timestamp can be set, and tags can be
moved or deleted. **It does not constitute a pre-registration that a third party
can verify.**

What v4 actually does:

1. Before recording, push the following to a public remote:

   ```
   src/  policies/  future-packs/  regression-tests/  acceptance-tests/
   prompts/  uv.lock  experiments/<id>.plan.json
       plan.json = { K, run_order, model, provider CLI version, limits,
                     retry rules, null tolerance band and handling when it is exceeded }
   ```

2. In each cassette's manifest, record `source_tree_digest` (excluding the
   cassette) and `plan_sha256`.
3. **Record every attempt.** Keep invalidated pairs with a reason.
4. Always report "N pairs attempted, M valid, K invalid (reason)" in the report.
5. If a frozen artifact is changed after recording, discard that experiment and
   issue a new experiment ID. Do not recompute by applying a new policy to an old
   cassette.

**Strength of the claim**: this is an **operational mechanism that ensures I did
not tune the yardstick after seeing the result**, not a proof to a third party.
The README says so.

---

## 13. Test strategy

Tests we must write:

* Fix the exit code for **every state × mode combination** in §4.2
* **Absence of fail-open**: evidence / infrastructure / trust-boundary /
  incomparable errors **exit 2 in every mode**
* **pairing**: compute `r[i,p]` within a pair before taking the median (pinned
  with an example where the result differs from the ratio of per-variant medians)
* **No cancellation**: `r = [10.0, 0.1]` → `FFR_gate > 1`
* **`ADAPTATION_REGRESSION` is reachable**: fires when candidate fails entirely
  and baseline succeeds entirely
* **churn**: `final_churn > 0` for a run that only creates new files
* **churn**: correct after `git add` / `git commit` / `.gitignore` changes
* **churn**: a `.pytest_cache` created by the acceptance command is not counted
  (snapshot ordering)
* **venv**: an acceptance verdict does not change even if the agent installs
  packages into the venv
* **test isolation**: an experiment is not invalidated if `acceptance-tests` fail
  in the pre state
* **regression**: detected even if the frozen tests are rewritten on the worktree
  side
* **launcher**: a run that invoked `pytest` directly becomes `EVIDENCE_INCOMPLETE`
* **schema scope**: correct evidence for K=4 is not rejected due to a `seq=0`
  duplicate
* **schema**: an orphan `tool_result` / duplicate `tool_ref` / non-contiguous
  `seq` → `EVIDENCE_INVALID`
* **manifest**: presence of a file not listed → `EVIDENCE_INCOMPLETE`
* **policy**: reject `component_hard_max > clamp_ratio` or
  `minimum_valid_pairs > runs_per_variant`
* **boundary**: `FFR_gate` exactly at the threshold → PASS (log space + epsilon)
* **projection**: plant a secret and source fragments in the raw events, and
  confirm they never appear in the output
* **golden bytes**: the same input always produces byte-identical JSONL
* **fixture inventory**: the per-`raw_kind` counts of the 76 collected lines
  match, with 0 unclassified
* **tree walk**: does not follow symlinks / `INVALID_RUN` on a FIFO

---

## 14. Critical path (27 hours)

| Gate | JST | Contents | Owner |
|---|---|---|---|
| **G0** | 2026-07-25 07:00 JST | scaffold / `mise run check` green / commit the design | Claude |
| **G1** | 2026-07-25 09:30 JST | **Bring up SigNoz with `foundryctl`, smoke trace, commit `casting.yaml(.lock)`** | **Human (host)** |
| **G2** | 2026-07-25 14:00 JST | normalize → validate → friction → decide → report. The required tests in §13 are green | Claude + subagents |
| **G3** | 2026-07-25 16:00 JST | demo repo (`main` / `pr/1`), current tests green on both, **push the evaluator artifacts** | subagent |
| **G4** | 2026-07-25 21:00 JST | **Record 16 runs sequentially** (null 8 + coupling 8). Apply the rules fixed in the published evaluator snapshot and record without cherry-picking | Claude |
| **G5** | 2026-07-25 23:30 JST | trajectories visible in OTel → SigNoz, dashboard, `morrow verify` green | Claude |
| **G6** | 2026-07-26 02:00 JST | GitHub push, CI green, checks visible on the demo PR | Claude |
| **G7** | 2026-07-26 05:00 JST | **code freeze**. README / diagrams / screenshots | Claude + human |
| **G8** | 2026-07-26 07:30 JST | demo video, submission text, AI-usage disclosure | human |
| **G9** | **2026-07-26 09:00 JST** | **submit** | human |

**Buffers**: 2.5 hours between G4 and G5, and 3 hours between G6 and G7, reserved
for re-recording and for a null that fails.

### 14.1 Why G1 was moved to the human

It is empirically established that the sandbox cannot reach the docker socket. We
do not assign the first 2.5 hours of the critical path to an owner who cannot
execute it.

### 14.2 Parallel lanes

| Lane | Owner | Scope |
|---|---|---|
| A | Claude Code (this session) | execution adapter, OTel, recording, integration |
| B | subagent | `src/morrow/domain/` + `tests/unit/` |
| C | subagent | `demo/` + demo repo |
| D | human | G1, registration, submission form, video |

---

## 15. Official hackathon requirements (verified against primary sources)

| Item | Verification |
|---|---|
| **Foundry required** | The rules state: "Install SigNoz using Foundry. Foundry installs both SigNoz and its MCP server in one step." |
| CLI name | `foundryctl`. `gauge` / `forge` / `cast` (`-f casting.yaml`) |
| **Required files** | The rules state: "Your repo must include the casting.yaml and casting.yaml.lock." |
| Track | 01 AI & Agent Observability (this is what we submit under) |
| Judging | Qualitative only. "The more SigNoz features you use, the better your chances" |
| **Non-disclosure of AI use is disqualifying** | State it in both the README and the submission form |
| Deadline | **The submission date, time, and time zone are not stated officially** |

### 15.1 Unverified second-hand information (not treated as fact)

* "`foundryctl forge` generates `casting.yaml.lock`"
* "MCP is disabled by default and requires `spec.mcp.spec.enabled: true`"
* "`casting.yaml` requires `kind: Installation`"

Confirm these in G1 from `foundryctl --help` and the actual generated artifacts.

### 15.2 G1 acceptance criteria

```
[ ] Install foundryctl at a pinned version and record its version and checksum
[ ] gauge → forge → verify and commit the generated casting.yaml.lock → cast
[ ] If the lock is not auto-generated, do not hand-craft it; treat G1 as failed and record the cause
[ ] The SigNoz UI opens
[ ] Send a smoke trace to OTLP and query it in SigNoz
```

---

## 16. Risks

| Item | Status | Response |
|---|---|---|
| `foundryctl` and `casting.yaml.lock` | **Unverified** | Human executes in G1. On failure, record the cause in the README |
| The null's `FFR_gate` exceeds 1.20 | **Unknown**. If exceeded, the instrument fails | Do not loosen the threshold; report "could not separate" (§9) |
| Variance is large even at K=4 | Possible | **Report every pair's `r[i,p]`.** Do not show only the median |
| 16 runs do not finish in time | 50–130 min sequential | 5 hours reserved for G4. On overrun, prioritize `coupling` and record `null` first |
| No OS-level isolation | **Not implemented (P1)** | Explicitly stated as C5: "we do not claim this" |
| Constrained execution environment | 6 CPU / 15.6 GiB | **Do not parallelize** (do not mix contention into the treatment difference) |

---
