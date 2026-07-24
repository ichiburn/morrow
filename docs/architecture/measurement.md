# MORROW Measurement Model and Trust Boundary

> For the overall design, see [design.md](design.md).

## 2. Trust boundary (stating the claim precisely)

```
evaluator assets (kept outside the worktree)
    <state_root>/<experiment_id>/
        plan.json  policy/  pack/  regression-tests/  acceptance-tests/
        snapshots/<run_id>.pre/          ← full content copy of the pre tree (§3.4)
        launcher-log/<run_id>.jsonl      ← primary record of test execution (§6.4)

measured assets (the agent's cwd)
    <work_root>/<run_id>/                ← worktree. The only thing the agent works on
```

**This is not a permission boundary.**
The agent runs under the same UID, so it can reach `<state_root>` as well.
Keeping evaluator assets outside the worktree is **accident prevention and mix-up avoidance**, not a defense against an adversarial agent.

Therefore, **`measure` / `gate` run only on trusted repositories** (C5).
OS-level isolation (a separate UID / container / network cutoff) is deferred to P1 and later.

`UNTRUSTED_TARGET` is decided not by an allowlist of repository names but by requiring **all** of the following to match:

```
target.repository      == one of the allowlist entries
target.head_repository == target.repository        # reject PRs from forks
trigger                ∈ {workflow_dispatch, push}  # pull_request / pull_request_target not allowed
head_sha               ∈ explicitly approved SHAs
```

If even one of these fails, **exit 2 before launching the agent**.

---

## 3. Measurement model

### 3.1 Paired runs (pair)

As R3 pointed out, v3 took "median per variant, then ratio" and **discarded pairing** in the process.
That mistakes time drift and warm-cache effects for a treatment difference.

```
K = 4 (even, so that AB and BA occur equally often)

pair p ∈ {0,1,2,3}
    each pair runs a baseline run and a candidate run back to back
    order  p=0: A→B   p=1: B→A   p=2: A→B   p=3: B→A

component i, pair p:
    r[i,p] = clamp( (c[i,p] + α) / (b[i,p] + α),  1/R,  R )

component i:
    r[i]   = median_p( r[i,p] )        ← ★ median of the pair ratios, not a ratio of per-variant medians

FFR_gate    = exp( Σ wᵢ · ln( max(1, r[i]) ) / Σ wᵢ )
FFR_display = exp( Σ wᵢ · ln( r[i] )         / Σ wᵢ )
```

**The report shows every `r[i,p]`.** It does not show only the median.
How much the four ratios scatter is information the reader should judge for themselves.

### 3.2 Pair validity and retries

| Event | Handling |
|---|---|
| One side of a pair hits an infra failure (MORROW crash / API outage / worktree creation failure) | **Invalidate the whole pair** |
| Re-running an invalidated pair | Up to `policy.experiment.max_pair_retries` (default 2). **Registered in advance** |
| Valid pairs < `policy.experiment.minimum_valid_pairs` (default 3) | `INFRASTRUCTURE_ERROR` → **exit 2** |

An agent hitting its time limit or exceeding its budget is not an infra failure; it counts as **`success = 0`** (a valid observation).

### 3.3 Components (making them genuinely mutually exclusive)

As R3 pointed out, v3's `other_tool_calls` included `PATCH`, and the resulting line diff also flowed into `final_churn`, so **the same edit was weighted twice on two axes**. v4 narrows this to three components.

| Component | Weight | Primary source | What it measures |
|---|---|---|---|
| `files_read_distinct` | 1.0 | distinct path IDs in `FILE_READ` | the surface area that must be understood |
| `test_cycles` | 1.0 | **test launcher record** (§6.4) | how much trial and error |
| `final_churn` | 1.0 | **diff against the pre tree content** (§3.4) | the physical volume of the implementation |

The counts of `SEARCH` / `PATCH` / `COMMAND` / `TOOL_OTHER` are **recorded and displayed, but not used by the gate**.
`output_tokens` / `api_duration_ms` / `cost_usd` are likewise display-only.

**The component set is fixed in policy.** If a required component is missing on either side, `EVIDENCE_INCOMPLETE` → exit 2.

### 3.4 Churn (retaining the actual content of the pre tree)

R3's point is correct in information-theoretic terms: **a `(size, sha256)` pair cannot reconstruct a line diff.**

```
① create worktree
② pre snapshot:  copy the worktree content into <state_root>/snapshots/<run_id>.pre/
③ run the agent
④ confirm the agent's process group has died
⑤ post snapshot: tree walk the worktree
⑥ final_churn = actual file diff between the pre tree and the post tree
⑦ run acceptance and regression tests (★ after churn is finalized; their artifacts are not counted)
```

The demo repository is small (a few hundred KB), so the cost of a full content copy is negligible.

```
final_churn = Σ lines in added files
            + Σ lines in deleted files
            + Σ (added lines + deleted lines) in changed files   ← actual diff via difflib

Excluded: the fixed allowlist in policy.metrics.churn_exclude
          (.venv/, __pycache__/, .pytest_cache/, .ruff_cache/, .git/, *.pyc)
```

### 3.4.1 Tree walk safety and determinism

| Target | Handling |
|---|---|
| symlink | **not followed** (`lstat`). Only the length of the link target string is recorded; its contents are not read |
| FIFO / socket / device / hard-link fan-out | if detected, mark that run `INVALID_RUN` |
| binary (undecodable as UTF-8) | not mixed into line counts. **Tallied separately as `binary_bytes_changed`** and not used by the gate |
| file count / total bytes / single-file size | if any exceeds the policy limit, `INVALID_RUN` |
| change during the walk | if the mtime set differs before and after the walk, `INVALID_RUN` |
| walk order | fixed to ascending byte order of the relative path (determinism) |

The v3 idea of converting binary to a line count via `bytes/80` is withdrawn: the unit is meaningless, and an image or lockfile change alone would spike the metric.

### 3.5 Execution environment (not letting the agent rewrite it)

In practice the **agent ran `pip install`**, so if the venv lives inside the worktree, "install a dependency and make the acceptance tests pass" becomes possible while `final_churn` stays 0.

```
Create the venv outside the worktree:
    <state_root>/venvs/<run_id>/          ← specified via UV_PROJECT_ENVIRONMENT

When running acceptance and regression tests:
    rebuild <state_root>/venvs/<run_id>.verify/ from the repository lockfile
    → packages the agent installed do not affect the acceptance decision

A dependency change surfaces in final_churn as a lockfile diff (the lockfile is inside the worktree)
```

### 3.6 Separating the success decision from the tests

As R3 pointed out, v3 conflated "acceptance tests for the future task" with "regression tests that protect existing behavior."
Acceptance tests for the future task are **supposed to fail before the run**, which collides with the rule "if pre fails, the experiment is invalid."

```
<state_root>/regression-tests/     protect existing behavior. Must all pass at pre. Re-run at the same bytes at post
<state_root>/acceptance-tests/     acceptance for the future task. Run only at post (failing at pre is expected)
```

| | pre | post |
|---|---|---|
| `regression-tests` | **must all pass**. If any fail, `EVIDENCE_INCOMPLETE` → exit 2 | if not all pass, `REGRESSION` |
| `acceptance-tests` | not run | if not all pass, `success = 0` |

```
success[v, p] = 1  ⟺  all acceptance-tests pass
                    ∧ all regression-tests pass
                    ∧ invariants hold
                    ∧ within the wall-clock and budget limits
```

Fixed conditions at execution: `cwd` = worktree root, `PYTHONPATH` unset,
`-p no:cacheprovider` added, plugin autoloading disabled (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`).

### 3.7 The sets used for FFR and the adaptation decision

As R3 pointed out, v3 had `comparable_runs >= minimum_paired_runs = 3` with K=3, so **a single candidate failure made `ADAPTATION_REGRESSION` unreachable**.

```
valid_pairs      = pairs that are valid at the infra level     (§3.2)
successful_pairs = pairs where both variants succeeded

Adaptation decision : made over all valid_pairs
FFR computation     : made over successful_pairs only
```

| Condition | Finding |
|---|---|
| `len(valid_pairs) < minimum_valid_pairs` | `INFRASTRUCTURE_ERROR` |
| baseline success count == 0 | `INCONCLUSIVE` (the control did not hold) |
| baseline success count ≥ threshold and candidate success count == 0 | `ADAPTATION_REGRESSION` |
| `len(successful_pairs) < minimum_ffr_pairs` (default 3) | do not report FFR. `INCONCLUSIVE` |
| `FFR_gate > policy.decision.friction_threshold` | `FRICTION_REGRESSION` |
| any `r[i] > policy.decision.component_hard_max` | `SINGLE_AXIS_REGRESSION` |

### 3.8 Null control and thresholds (breaking the circularity)

**Thresholds are not derived from the null.** If they were, the null would automatically pass under the same rule, closing a circle.

```
The threshold friction_threshold is written in policy as a fixed value and
included in the public snapshot before any treatment data is seen (§12)

The null control (independent clones A vs B of main, same K=4)
    · is collected by the same procedure
    · is presented alongside the treatment in the report
    · is a "reference for the validity of the rule," not an input to computing the threshold
```

**Pre-registered handling of the null result**:

| Null `FFR_gate` | Handling |
|---|---|
| `≤ policy.null_control.maximum_ffr` (fixed value, default 1.20) | normal. Present the treatment result |
| above it | **mark the whole day's scenarios `INVALID_EXPERIMENT` → exit 2**.<br>Do not loosen the threshold to pass. Report "separation was not achievable in this environment" |

**No statistical claim is made** (C6). For a K=4 sign test the lower bound on the one-sided p is 1/16 = 0.0625, so significance cannot be stated in principle. All that is presented is the fact that **"under a pre-registered rule, an observation exceeding the null collected at the same time was obtained."**

### 3.9 Numeric consistency and boundaries

```yaml
# policies/default.yaml (closed schema; unknown keys rejected)
experiment:
  runs_per_variant: 4            # = pair count K
  minimum_valid_pairs: 3
  minimum_ffr_pairs: 3
  max_pair_retries: 2
metrics:
  alpha: 1.0                     # > 0
  clamp_ratio: 10.0              # R >= 1
  small_sample_floor: 3
  weights:                       # all > 0, sum > 0
    files_read_distinct: 1.0
    test_cycles: 1.0
    final_churn: 1.0
  churn_exclude: [".venv/", "__pycache__/", ".pytest_cache/", ".ruff_cache/", ".git/", "*.pyc"]
decision:
  friction_threshold: 1.50       # 1 < threshold <= component_hard_max <= clamp_ratio
  component_hard_max: 3.00
null_control:
  maximum_ffr: 1.20
numeric:
  epsilon: 1e-9
acceptance:
  command_timeout_seconds: 300
  output_limit_bytes: 1048576
```

**Cross-field validation** (violations rejected at startup):

```
alpha > 0
clamp_ratio >= 1
1 < friction_threshold <= component_hard_max <= clamp_ratio
minimum_valid_pairs <= runs_per_variant
minimum_ffr_pairs   <= runs_per_variant
every weight > 0 and Σ weight > 0
```

**Small sample**: for component i, a pair whose `b[i,p]` and `c[i,p]` are both below `small_sample_floor` does not have its ratio computed for that component and is recorded in `data_quality`. A component for which every pair is like this is dropped from the gate.

**Floating-point boundaries**: comparisons are done in log space, converting to `Decimal` and using `epsilon` explicitly.

```
FRICTION_REGRESSION  ⟺  ln(FFR_gate) > ln(friction_threshold) + epsilon
```

This makes "exactly at the threshold means PASS" reproducible across environments.

---
