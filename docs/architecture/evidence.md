# MORROW 判定・証拠検証・イベントモデル

> 設計の全体像は [design.md](design.md) を参照。

## 4. 判定と終了コード（状態機械を 1 か所に）

R3 の指摘どおり、v3 は `measure` を常に exit 0 としながら本文各所で「証拠エラーは非ゼロ」と書いており、
**fail-open が別経路で復活していた**。v4 では状態機械を 1 つの表に集約する。

### 4.1 3 段階に分解する

```
validate_evidence(raw)                     -> ValidatedExperiment | EvidenceError
evaluate_policy(ValidatedExperiment, Policy) -> Assessment          # domain・純関数
enforce(mode, Assessment | EvidenceError)  -> ExitResult            # domain・純関数
```

`EvidenceError` は `FrictionMetrics` の一部ではない。
「判定は `FrictionMetrics + Policy` の純関数」という v3 の主張は、
証拠エラー・信頼境界エラーを含められないので**分解して正確にした**。

### 4.2 状態 × モード → 終了コード（唯一の定義）

| 状態 | 分類 | `measure` | `verify` | `gate` |
|---|---|---|---|---|
| `EVIDENCE_INVALID` | 証拠 | **2** | **2** | **2** |
| `EVIDENCE_INCOMPLETE` | 証拠 | **2** | **2** | **2** |
| `CASSETTE_CORRUPTED` | 証拠 | **2** | **2** | **2** |
| `UNTRUSTED_TARGET` | 信頼境界 | **2** | **2** | **2** |
| `INFRASTRUCTURE_ERROR` | インフラ | **2** | **2** | **2** |
| `INVALID_EXPERIMENT` | インフラ | **2** | **2** | **2** |
| `INCONCLUSIVE` | 比較不能 | **2** | **2** | **2** |
| `GATE_PRECONDITION_UNMET` | 事前条件 | — | — | **2** |
| `REGRESSION` | 摩擦所見 | 0（advisory） | — | **1** |
| `ADAPTATION_REGRESSION` | 摩擦所見 | 0（advisory） | — | **1** |
| `FRICTION_REGRESSION` | 摩擦所見 | 0（advisory） | — | **1** |
| `SINGLE_AXIS_REGRESSION` | 摩擦所見 | 0（advisory） | — | **1** |
| `DEGRADED_DATA` | 劣化 | 0 | 0 | 0 |
| `OK` | 正常 | 0 | 0 | 0 |
| `EVIDENCE_REPRODUCED` | verify 専用 | — | 0 | — |
| `EVIDENCE_STALE` | verify 専用 | — | **2** | — |

**規則**: 証拠・インフラ・信頼境界・比較不能のエラーは**全モードで exit 2**。
摩擦所見のみが `measure` で advisory（0）、`gate` で block（1）になる。
`--strict` は `DEGRADED_DATA` を 1 に格上げする。

`ADVISORY` は verdict ではなくモードの性質なので、状態名から削除した。

### 4.3 所見の集約

短絡するのは**証拠・信頼境界・インフラのエラーのみ**。
摩擦所見は全部収集し、**最大重大度**で verdict を決める。
`primary_reason` の表示順は verdict の決定とは独立に定義する。

### 4.4 `gate` の事前条件

| 事前条件 | 満たさない場合 |
|---|---|
| §2 の信頼判定に全一致 | `UNTRUSTED_TARGET` |
| ヌルコントロールが実施済みかつ許容帯内 | `INVALID_EXPERIMENT` |
| `len(valid_pairs) >= minimum_valid_pairs` | `INFRASTRUCTURE_ERROR` |
| 証拠検証を全通過 | `EVIDENCE_INVALID` / `EVIDENCE_INCOMPLETE` |
| policy / pack が evaluator パス由来 | `GATE_PRECONDITION_UNMET` |

---

## 5. 証拠の検証（スコープを run 単位に）

R3 の指摘どおり、v3 は一意性と件数を **variant 単位**で検証すると書いていたが、
K 回反復では `seq=0` が K 個・`SESSION_START` が K 個現れるため、**正しい証拠が必ず弾かれる**。

**検証の単位は `(variant, run_index)` である。**

| 検証 | 単位 | 違反時 |
|---|---|---|
| closed JSON Schema（未知フィールド拒否） | イベント | `EVIDENCE_INVALID` |
| `seq` が 0 始まり・欠番なし・重複なし | run | `EVIDENCE_INVALID` |
| `tool_use_id` が一意 | run | `EVIDENCE_INVALID` |
| 孤立 `tool_result`（対応する `tool_use` なし） | run | `EVIDENCE_INVALID` |
| `SESSION_START` がちょうど 1 件、`COMPLETION` がちょうど 1 件 | run | `EVIDENCE_INCOMPLETE` |
| `session_id` が manifest の当該 run の値と一致 | run | `EVIDENCE_INVALID` |
| 未対応 `tool_use`（`success is None`）が上限以下 | run | `EVIDENCE_INCOMPLETE` |
| manifest の `runs[]` と実ファイル集合が完全一致（過不足なし） | experiment | `EVIDENCE_INCOMPLETE` |
| 各 pair に baseline と candidate が 1 件ずつ | experiment | `EVIDENCE_INVALID` |

### 5.1 manifest の `runs[]`（閉じた配列）

```json
"runs": [
  {"run_id": "r0", "run_index": 0, "variant": "baseline",  "pair_id": 0, "order_position": 0,
   "session_id": "…", "status": "ok",
   "files": {"events": "r0.events.jsonl", "churn": "r0.churn.json", "tests": "r0.tests.json"}},
  {"run_id": "r1", "run_index": 0, "variant": "candidate", "pair_id": 0, "order_position": 1, …}
]
```

`files` に列挙されていないファイルがカセットディレクトリにあれば `EVIDENCE_INCOMPLETE`。
各パスは `resolve()` 後にカセットルート直下の通常ファイルであること、
シンボリックリンクでないこと、正規化後に重複しないことを検証する。

---

## 6. 正規化イベントモデル（本当に閉じる）

R3 の指摘どおり、v3 の `executable` / `raw_kind` / `counters` のキー / 各種 ID はすべて自由文字列だった。
`Mapping[str, int]` は値を整数にしただけで、任意 map の抜け道は閉じていない。

### 6.1 公開 DTO は EventKind ごとの discriminated union

```python
class RawKind(StrEnum):                    # provider 由来の生文字列は保存しない
    INIT = "init"; ASSISTANT_TOOL_USE = "assistant_tool_use"
    TOOL_RESULT = "tool_result"; RESULT = "result"; OTHER = "other"

class KnownExecutable(StrEnum):            # 生の実行ファイル名は保存しない
    MORROW_TEST = "morrow_test"; PYTEST = "pytest"; PYTHON = "python"
    GIT = "git"; UV = "uv"; RUFF = "ruff"; MYPY = "mypy"; OTHER = "other"

class EventBase(BaseModel, frozen=True, extra="forbid"):
    seq: NonNegativeInt
    run_id: RunId                          # ^r[0-9]{1,3}$
    tool_ref: ToolRef | None               # ^t[0-9]{1,4}$  ← ★ provider の tool_use_id を再採番
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
    stop_reason: StopReason; terminal_reason: TerminalReason   # ともに enum
    permission_denial_count: NonNegativeInt
class SessionStartEvent(EventBase): kind: Literal["session_start"]; model: KnownModel
class OpaqueEvent(EventBase):     kind: Literal["opaque"]      # ★ 本文も raw_kind 詳細も持たない

AgentEvent = Annotated[Union[...], Field(discriminator="kind")]
```

**要点**:

* `path_ref` / `tool_ref` は **experiment 内で採番した opaque ID**。
  実パスとの対応表は evaluator 側にのみ置き、**公開しない**。
  → パスのハッシュに対する辞書攻撃（`src/auth.py` の存在推測）も同時に塞げる
* `executable` は **enum**。未知は `OTHER`。シークレットをファイル名にした実行ファイルは通らない
* 自由 map は存在しない。すべて型付きフィールド
* `OpaqueEvent` は件数だけを持つ。`raw_kind` も粗い enum に丸める
* 金額は `cost_micro_usd`（整数）。浮動小数点を正規化イベントに入れない

### 6.2 tool_use ↔ tool_result の対応付けと順序

```
正規順序キー = (source_line_index, content_index)
seq          = 上記キーで昇順ソートしたときの 0 始まりの通し番号

1. assistant の tool_use を pending に入れる（tool_ref を採番）
2. 対応する tool_result で success を確定し、確定後に一度だけ emit
3. 終端で残った pending は success = None で emit し、unpaired を加算
4. 全イベントを seq 昇順に並べて書き出す
```

`timestamp` は正規化イベントに**含めない**。provider が出さない場合の合成値が決定性を壊すため。
時刻情報は manifest の run 単位（開始・終了）にのみ持つ。順序は常に `seq` で決まる。

### 6.3 正規化 JSON のバイト決定性

* キーは辞書順（RFC 8785 相当）。**golden byte fixture でバイト列を固定する**
* 数値は整数のみ。浮動小数点を含めない
* 行区切りは LF、末尾に改行 1 つ
* 非 ASCII は現れない（すべて enum と opaque ID のため）

### 6.4 テスト実行の一次ソース（推測をやめる）

シェル文字列のパースは P0 の品質では成立しない。**入口を 1 本に固定する。**

```
evaluator が worktree に配置: ./morrow-test
    実体は future-pack の acceptance argv を実行し、
    <state_root>/launcher-log/<run_id>.jsonl に
    {launcher_seq, exit_code, duration_ms} を追記する

プロンプトで明示: 「テストは ./morrow-test で実行してください」

test_cycles = ランチャログの行数        ← イベント推定ではなく一次記録
```

エージェントが `./morrow-test` を使わずに直接テストを叩いた場合:

* `executable` が `PYTEST` / `PYTHON` の `CommandEvent` を検出したら、
  **`data_quality.direct_test_invocations` を加算する**
* この件数が `policy.metrics.max_direct_test_invocations`（既定 0）を超えたら
  **`EVIDENCE_INCOMPLETE` → exit 2**。黙って過小カウントにしない

シェル文法の汎用パースは P1。

---

