# MORROW Decision, Evidence Validation, and Event Model

> For the overall design, see [design.md](design.md).

## 4. Verdicts and exit codes (state machine in one place)

As R3 pointed out, v3 kept `measure` always at exit 0 while stating in several places that "evidence errors are non-zero,"
so **fail-open crept back in through a side path**. v4 consolidates the state machine into a single table.

### 4.1 Split into three stages

```
validate_evidence(raw)                     -> ValidatedExperiment | EvidenceError
evaluate_policy(ValidatedExperiment, Policy) -> Assessment          # domain, pure function
enforce(mode, Assessment | EvidenceError)  -> ExitResult            # domain, pure function
```

`EvidenceError` is not part of `FrictionMetrics`.
v3's claim that "the verdict is a pure function of `FrictionMetrics + Policy`"
could not accommodate evidence errors or trust-boundary errors, so we **split it apart to make it accurate**.

### 4.2 State × mode → exit code (the single definition)

| State | Class | `measure` | `verify` | `gate` |
|---|---|---|---|---|
| `EVIDENCE_INVALID` | evidence | **2** | **2** | **2** |
| `EVIDENCE_INCOMPLETE` | evidence | **2** | **2** | **2** |
| `CASSETTE_CORRUPTED` | evidence | **2** | **2** | **2** |
| `UNTRUSTED_TARGET` | trust boundary | **2** | **2** | **2** |
| `INFRASTRUCTURE_ERROR` | infrastructure | **2** | **2** | **2** |
| `INVALID_EXPERIMENT` | infrastructure | **2** | **2** | **2** |
| `INCONCLUSIVE` | not comparable | **2** | **2** | **2** |
| `GATE_PRECONDITION_UNMET` | precondition | — | — | **2** |
| `REGRESSION` | friction finding | 0 (advisory) | — | **1** |
| `ADAPTATION_REGRESSION` | friction finding | 0 (advisory) | — | **1** |
| `FRICTION_REGRESSION` | friction finding | 0 (advisory) | — | **1** |
| `SINGLE_AXIS_REGRESSION` | friction finding | 0 (advisory) | — | **1** |
| `DEGRADED_DATA` | degraded | 0 | 0 | 0 |
| `OK` | normal | 0 | 0 | 0 |
| `EVIDENCE_REPRODUCED` | verify only | — | 0 | — |
| `EVIDENCE_STALE` | verify only | — | **2** | — |

**Rule**: evidence, infrastructure, trust-boundary, and not-comparable errors are **exit 2 in every mode**.
Only friction findings are advisory (0) under `measure` and block (1) under `gate`.
`--strict` promotes `DEGRADED_DATA` to 1.

`ADVISORY` is a property of the mode, not of the verdict, so it was removed from the state names.

### 4.3 Aggregating findings

Only **evidence, trust-boundary, and infrastructure errors** short-circuit.
Friction findings are all collected, and the verdict is decided by the **highest severity**.
The display order of `primary_reason` is defined independently of how the verdict is decided.

### 4.4 Preconditions for `gate`

| Precondition | If unmet |
|---|---|
| Fully matches the trust decision in §2 | `UNTRUSTED_TARGET` |
| Null control was run and is within the tolerance band | `INVALID_EXPERIMENT` |
| `len(valid_pairs) >= minimum_valid_pairs` | `INFRASTRUCTURE_ERROR` |
| Passes all evidence validation | `EVIDENCE_INVALID` / `EVIDENCE_INCOMPLETE` |
| policy / pack originate from the evaluator path | `GATE_PRECONDITION_UNMET` |

---

## 5. Evidence validation (scope it to the run)

As R3 pointed out, v3 stated that uniqueness and counts were validated **per variant**, but
across K repetitions there are K instances of `seq=0` and K instances of `SESSION_START`, so **valid evidence is always rejected**.

**The unit of validation is `(variant, run_index)`.**

| Check | Unit | On violation |
|---|---|---|
| Closed JSON Schema (reject unknown fields) | event | `EVIDENCE_INVALID` |
| `seq` starts at 0, no gaps, no duplicates | run | `EVIDENCE_INVALID` |
| `tool_use_id` is unique | run | `EVIDENCE_INVALID` |
| Orphaned `tool_result` (no matching `tool_use`) | run | `EVIDENCE_INVALID` |
| Exactly one `SESSION_START` and exactly one `COMPLETION` | run | `EVIDENCE_INCOMPLETE` |
| `session_id` matches the value for that run in the manifest | run | `EVIDENCE_INVALID` |
| Unpaired `tool_use` (`success is None`) at or below the cap | run | `EVIDENCE_INCOMPLETE` |
| The manifest's `runs[]` exactly matches the actual file set (no more, no less) | experiment | `EVIDENCE_INCOMPLETE` |
| Each pair has exactly one baseline and one candidate | experiment | `EVIDENCE_INVALID` |

### 5.1 The manifest's `runs[]` (closed array)

```json
"runs": [
  {"run_id": "r0", "run_index": 0, "variant": "baseline",  "pair_id": 0, "order_position": 0,
   "session_id": "…", "status": "ok",
   "files": {"events": "r0.events.jsonl", "churn": "r0.churn.json", "tests": "r0.tests.json"}},
  {"run_id": "r1", "run_index": 0, "variant": "candidate", "pair_id": 0, "order_position": 1, …}
]
```

If any file in the cassette directory is not listed under `files`, the result is `EVIDENCE_INCOMPLETE`.
Each path must be a regular file directly under the cassette root after `resolve()`,
must not be a symlink, and must not collide with another after normalization.

---

## 6. Normalized event model (truly closed)

As R3 pointed out, v3's `executable` / `raw_kind` / `counters` keys and various IDs were all free-form strings.
`Mapping[str, int]` only makes the values integers; it does not close the escape hatch of an arbitrary map.

### 6.1 The public DTO is a discriminated union per EventKind

```python
class RawKind(StrEnum):                    # do not store the provider's raw strings
    INIT = "init"; ASSISTANT_TOOL_USE = "assistant_tool_use"
    TOOL_RESULT = "tool_result"; RESULT = "result"; OTHER = "other"

class KnownExecutable(StrEnum):            # do not store raw executable names
    MORROW_TEST = "morrow_test"; PYTEST = "pytest"; PYTHON = "python"
    GIT = "git"; UV = "uv"; RUFF = "ruff"; MYPY = "mypy"; OTHER = "other"

class EventBase(BaseModel, frozen=True, extra="forbid"):
    seq: NonNegativeInt
    run_id: RunId                          # ^r[0-9]{1,3}$
    tool_ref: ToolRef | None               # ^t[0-9]{1,4}$  <- reassigned from the provider's tool_use_id
    raw_kind: RawKind
    success: bool | None
    duration_ms: NonNegativeInt | None

class FileReadEvent(EventBase):   kind: Literal["file_read"]; path_ref: PathRef      # ^p[0-9]{1,4}$
class PatchEvent(EventBase):      kind: Literal["patch"];     path_ref: PathRef
class SearchEvent(EventBase):     kind: Literal["search"]
class CommandEvent(EventBase):    kind: Literal["command"];   executable: KnownExecutable
class TestEvent(EventBase):       kind: Literal["test"];      launcher_seq: NonNegativeInt
class CompletionEvent(EventBase):
    kind: Literal["completion"]
    num_turns: NonNegativeInt; output_tokens: NonNegativeInt
    api_duration_ms: NonNegativeInt; cost_micro_usd: NonNegativeInt
    stop_reason: StopReason; terminal_reason: TerminalReason   # both enums
    permission_denial_count: NonNegativeInt
class SessionStartEvent(EventBase): kind: Literal["session_start"]; model: KnownModel
class OpaqueEvent(EventBase):     kind: Literal["opaque"]      # carries neither a body nor raw_kind detail

AgentEvent = Annotated[Union[...], Field(discriminator="kind")]
```

**Key points**:

* `path_ref` / `tool_ref` are **opaque IDs assigned within the experiment**.
  The mapping to real paths lives only on the evaluator side and is **not published**.
  -> This also blocks dictionary attacks against path hashes (inferring the existence of `src/auth.py`)
* `executable` is an **enum**; unknown values become `OTHER`. An executable whose name encodes a secret does not get through
* No free-form maps exist; every field is typed
* `OpaqueEvent` carries only a count. Even `raw_kind` is rounded down to a coarse enum
* Amounts are `cost_micro_usd` (integer). No floating point enters the normalized events

### 6.2 Pairing and ordering of tool_use <-> tool_result

```
canonical order key = (source_line_index, content_index)
seq                 = 0-based running number when sorted ascending by the key above

1. Put the assistant's tool_use into pending (assign a tool_ref)
2. Confirm success on the matching tool_result, and emit exactly once after confirmation
3. Any pending left at the end is emitted with success = None, and unpaired is incremented
4. Sort all events ascending by seq and write them out
```

`timestamp` is **not included** in the normalized events, because a synthesized value used when the provider omits it would break determinism.
Time information is held only per run in the manifest (start and end). Order is always decided by `seq`.

### 6.3 Byte-level determinism of the normalized JSON

* Keys are in lexicographic order (equivalent to RFC 8785). **A golden byte fixture pins the byte sequence.**
* Numbers are integers only; no floating point
* Line separator is LF, with a single trailing newline
* No non-ASCII appears (everything is enums and opaque IDs)

### 6.4 Primary source for test runs (stop guessing)

Parsing shell strings is not viable at P0 quality. **Fix the entry point to a single path.**

```
The evaluator places ./morrow-test in the worktree:
    it runs the acceptance argv of the future-pack and
    appends {launcher_seq, exit_code, duration_ms} to
    <state_root>/launcher-log/<run_id>.jsonl

The prompt states explicitly: "Run tests with ./morrow-test."

test_cycles = number of lines in the launcher log   <- a primary record, not inferred from events
```

If the agent bypasses `./morrow-test` and invokes tests directly:

* When a `CommandEvent` with `executable` of `PYTEST` / `PYTHON` is detected,
  **increment `data_quality.direct_test_invocations`**
* If this count exceeds `policy.metrics.max_direct_test_invocations` (default 0),
  the result is **`EVIDENCE_INCOMPLETE` -> exit 2**. Do not silently undercount

General-purpose parsing of shell grammar is P1.

---
