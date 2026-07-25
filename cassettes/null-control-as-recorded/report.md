# MORROW — PASS

**Primary reason:** no friction finding (`OK`)

- Mode `measure` · Evidence `live` · Experiment `null-control-as-recorded` · Scenario `replace-cache`
- Provider `claude-code` · Model `claude_sonnet`
- Repetitions: K = 2 pairs
- No statistical significance is claimed. At K=2 a sign test cannot establish significance (one-sided p floor 1/4 = 0.2500). This is an observation measured against a concurrently collected null control, under a decision rule fixed before the treatment data was seen.

## Verdict

| Field | Value |
| --- | --- |
| Verdict | PASS |
| Exit code | 0 |
| State | `OK` |
| Mode | `measure` |
| Advisory | no |
| Strict | no |

## Future Friction Ratio

| Quantity | Value |
| --- | --- |
| FFR_gate (one-sided) | 1.0000 |
| Threshold (`friction_threshold`) | 1.5000 |
| Comparison | FFR_gate <= threshold — within threshold |
| FFR_display (two-sided) | 0.5805 |

## Components — median over successful pairs

| Component | Baseline median | Candidate median | r[i] | Over hard-max (3.0000)? |
| --- | --- | --- | --- | --- |
| `files_read_distinct` | 15.5 | 13 | 0.8529 | no |
| `final_churn` | 132 | 60 | 0.4586 | no |
| `test_cycles` | 2 | 1.5 | 0.5000 | no |

> r[i] is the median of the per-pair ratios, not the ratio of the two medians in this table.

## Per-pair ratios r[i,p]

| Pair | `files_read_distinct` | `final_churn` | `test_cycles` |
| --- | --- | --- | --- |
| 0 | 1.0000 | 0.4662 | 0.5000 |
| 1 | 0.7059 | 0.4511 | small-sample |

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
