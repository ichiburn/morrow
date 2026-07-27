# MORROW — ERROR

**Primary reason:** null control FFR_gate 1.7403 exceeds maximum_ffr 1.2000 (`INVALID_EXPERIMENT`)

- Mode `measure` · Evidence `live` · Experiment `null-control-arms-swapped` · Scenario `replace-cache`
- Provider `claude-code` · Model `claude_sonnet`
- Repetitions: K = 2 pairs planned, 2 compared
- No statistical significance is claimed — over 2 compared pair(s) a one-sided sign test cannot reach conventional significance — its p floor is 1/4 = 0.2500, above 0.05. This is an observation measured against a concurrently collected null control, under a decision rule fixed before the treatment data was seen.

## Verdict

| Field | Value |
| --- | --- |
| Verdict | ERROR |
| Exit code | 2 |
| State | `INVALID_EXPERIMENT` |
| Mode | `measure` |
| Advisory | no |
| Strict | no |

Findings:

- `INVALID_EXPERIMENT`: null control FFR_gate 1.7403 exceeds maximum_ffr 1.2000

## Future Friction Ratio

FFR was not computed for this verdict — the verdict was decided before it applied (INVALID_EXPERIMENT).

- Threshold (`friction_threshold`): 1.5000

## Components — median over successful pairs

| Component | Baseline median | Candidate median | r[i] | Over hard-max (3.0000)? |
| --- | --- | --- | --- | --- |
| `files_read_distinct` | 13 | 15.5 | — | — |
| `final_churn` | 60 | 132 | — | — |
| `test_cycles` | 1.5 | 2 | — | — |

> r[i] is the median of the per-pair ratios, not the ratio of the two medians in this table.

## Per-pair ratios r[i,p]

| Pair | `files_read_distinct` | `final_churn` | `test_cycles` |
| --- | --- | --- | --- |
| 0 | 1.0000 | 2.1452 | 2.0000 |
| 1 | 1.4167 | 2.2167 | small-sample |

## Trial breakdown

| Metric | Count |
| --- | --- |
| Pairs attempted (N) | 2 |
| Valid (M) | 2 |
| Invalid (K) | 0 |
| Successful (both arms) | 2 |
| Baseline successes | 2 |
| Candidate successes | 2 |

## Null control

No null control was provided for this run.

## Evidence

- Evidence mode: `live`
- SigNoz trace id: not available
