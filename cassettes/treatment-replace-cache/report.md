# MORROW — ERROR

**Primary reason:** null control FFR_gate 1.7403 exceeds maximum_ffr 1.2000 (`INVALID_EXPERIMENT`)

- Mode `measure` · Evidence `live` · Experiment `treatment-replace-cache` · Scenario `replace-cache`
- Provider `claude-code` · Model `claude_sonnet`
- Repetitions: K = 3 pairs planned, 3 compared
- No statistical significance is claimed — over 3 compared pair(s) a one-sided sign test cannot reach conventional significance — its p floor is 1/8 = 0.1250, above 0.05. This is an observation measured against a concurrently collected null control, under a decision rule fixed before the treatment data was seen.

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
| `files_read_distinct` | 18 | 16 | — | — |
| `final_churn` | 63 | 321 | — | — |
| `test_cycles` | 1 | 3 | — | — |

> r[i] is the median of the per-pair ratios, not the ratio of the two medians in this table.

## Per-pair ratios r[i,p]

| Pair | `files_read_distinct` | `final_churn` | `test_cycles` |
| --- | --- | --- | --- |
| 0 | 0.8947 | 3.7538 | 2.5000 |
| 1 | 0.8095 | 5.1111 | 2.0000 |
| 2 | 1.4167 | 4.0000 | small-sample |

## Trial breakdown

| Metric | Count |
| --- | --- |
| Pairs attempted (N) | 3 |
| Valid (M) | 3 |
| Invalid (K) | 0 |
| Successful (both arms) | 3 |
| Baseline successes | 3 |
| Candidate successes | 3 |

## Null control

| Quantity | Value |
| --- | --- |
| Null FFR_gate | 1.7403 |
| Tolerance (`maximum_ffr`) | 1.2000 |
| Within band | no |

## Evidence

- Evidence mode: `live`
- SigNoz trace id: not available
