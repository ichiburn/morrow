# MORROW — PASS

**Primary reason:** FFR_gate 1.7403 > threshold 1.5000 (`FRICTION_REGRESSION`)

- Mode `measure` (advisory) · Evidence `live` · Experiment `null-control-arms-swapped` · Scenario `replace-cache`
- Provider `claude-code` · Model `claude_sonnet`
- Repetitions: K = 2 pairs planned, 2 compared
- No statistical significance is claimed — over 2 compared pair(s) a one-sided sign test cannot reach conventional significance — its p floor is 1/4 = 0.2500, above 0.05. This is an observation measured against a concurrently collected null control, under a decision rule fixed before the treatment data was seen.

## Verdict

| Field | Value |
| --- | --- |
| Verdict | PASS |
| Exit code | 0 |
| State | `FRICTION_REGRESSION` |
| Mode | `measure` |
| Advisory | yes |
| Strict | no |

Findings:

- `FRICTION_REGRESSION`: FFR_gate 1.7403 > threshold 1.5000

## Future Friction Ratio

| Quantity | Value |
| --- | --- |
| FFR_gate (one-sided) | 1.7403 |
| Threshold (`friction_threshold`) | 1.5000 |
| Comparison | FFR_gate > threshold — exceeds threshold |
| FFR_display (two-sided) | 1.7403 |

## Components — median over successful pairs

| Component | Baseline median | Candidate median | r[i] | Over hard-max (3.0000)? |
| --- | --- | --- | --- | --- |
| `files_read_distinct` | 13 | 15.5 | 1.2083 | no |
| `final_churn` | 60 | 132 | 2.1809 | no |
| `test_cycles` | 1.5 | 2 | 2.0000 | no |

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
