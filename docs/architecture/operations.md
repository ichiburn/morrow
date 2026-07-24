# MORROW 実行・スコープ・運用

> 設計の全体像は [design.md](design.md) を参照。

## 7. 実行と隔離

```
<work_root>/<run_id>/            worktree（agent の cwd）
<state_root>/<experiment_id>/
    plan.json  policy/  pack/  regression-tests/  acceptance-tests/
    agent-home/<run_id>/         ← ★ run ごとに独立（読み取り専用テンプレートから複製）
    venvs/<run_id>/              ← worktree の外
    snapshots/<run_id>.pre/
    launcher-log/<run_id>.jsonl
```

| 項目 | 実装 |
|---|---|
| プロセス群の停止 | `setsid` → `killpg(SIGTERM)` → 猶予後 `SIGKILL` → **プロセスグループ消滅を確認してから post スナップショット** |
| 予算上限 | `--max-budget-usd` |
| 壁時計上限 | `asyncio.wait_for` |
| config の独立性 | run ごとに `agent-home` を複製。session / cache の漏れを防ぐ |
| 実行順 | AB / BA を同数。plan.json に事前登録 |
| 並列度 | **1（逐次）**。並列にすると resource contention が treatment 差に混入する |
| variant の秘匿 | **cwd 名・プロンプト・環境変数から `baseline` / `candidate` を除く**。<br>等長の opaque `run_id` のみを使い、対応表は evaluator が持つ |

### 7.1 ステップ数は観測するが強制しない

`agent_steps = distinct な tool_ref 数`。
ただし**上限強制は P0 では行わない**（1 イベント内の並列 tool_use を含め、
確実に止められることを検証できていないため）。
記録するのは `observed_steps` のみで、`limit_enforced` という主張はしない。
強制するのは壁時計時間と予算だけである。

### 7.2 これはセキュリティ境界ではない

§2 のとおり。worktree・`cwd`・`CLAUDE_CONFIG_DIR`・プロセスグループは
ホスト侵害への防御ではない。信頼済みリポジトリでのみ実行する。

---

## 8. スコープ（P0 を本当に減らす）

R3 の指摘を受け、**機能を足さずに減らす**。

### P0

| 領域 | 内容 |
|---|---|
| provider | Claude Code のみ |
| 未来タスク | 1 件 |
| シナリオ | `null` と `coupling` の 2 つ |
| 反復 | K=4 pair × 2 シナリオ = **16 run** |
| 成分 | **3 つ**（`files_read_distinct` / `test_cycles` / `final_churn`） |
| モード | `measure` / `verify` / `gate`（`gate` は記録済みレポートに規則を当てる薄い層） |
| 判定 | §4.2 の状態機械 |
| 観測 | OTel → SigNoz にトラジェクトリ 2 本 + ダッシュボード 1 画面（手動インポート可） |
| 出力 | `morrow-report.md` / `morrow-report.json` |
| テスト | unit（摩擦・判定・正規化・射影・churn）/ contract（76 行 fixture・golden bytes）/ architecture / e2e（`verify`） |
| 文書 | README（§0.1 の主張表を含む）・デモ動画・AI 利用申告 |

### P1 以降（P0 に戻す条項は置かない）

Codex アダプタ / `fixed` シナリオ / ASR の一般化 / テスト ID 単位の spill /
SigNoz を判定入力にする経路 / `--audit-signoz` / MCP / アラート自動化 /
シェル文法の汎用パース / OS レベル隔離 / 署名・provenance / デモリポジトリの自動化

### コストと時間

| | 1 run | P0 合計 |
|---|---|---|
| コスト | $0.16〜0.59（実測） | 16 run で **$3〜10** |
| 所要 | 3〜8 分 | **逐次で 50〜130 分**（並列にしない） |

---

## 9. デモ設計

| ID | baseline | candidate | 事前登録した仮説 |
|---|---|---|---|
| `null` | `main` の独立クローン A | `main` の独立クローン B | `FFR_gate ≤ 1.20` |
| `coupling` | `main` | `pr/1`（domain が Redis を直 import） | `FFR_gate > 1.50` |

**「期待どおりの verdict が出ること」を完了条件にしない。**

| 起きたこと | どうするか |
|---|---|
| `null` が許容帯を外れた | 全シナリオを `INVALID_EXPERIMENT`。閾値を緩めない。「この環境では分離できなかった」と報告する |
| `coupling` が BLOCK にならなかった | **そのまま報告する。**再録画して都合のよい結果を採らない |
| 一部 pair が無効化された | 理由付きで残し、レポートに件数を出す |

### 9.1 構造的なコスト差

未来タスク: 「ローカル・テスト環境向けのインメモリキャッシュを追加せよ。order-service の API は変えないこと」

```
main : orders/adapters/memory_cache.py を新規作成 + composition.py の 1 行   → 2 ファイル
pr/1 : order_service / pricing / inventory / promotions から redis を剥がし、
       抽象を新規に発明し、redis 固有のテストを直す                          → 6〜9 ファイル
```

不変条件 `orders.domain は redis を import しない` が「全部剥がす」を強制する。
現行テストは両者で通る（`fakeredis` を使い外部サーバ依存をゼロにする）。

---

## 10. アーキテクチャと層の強制

```
src/morrow/
├── domain/        純粋。stdlib + pydantic のみ
│   ├── events.py  metrics.py  assessment.py
│   ├── friction.py     ★ 摩擦計算（純関数）
│   └── policy.py       ★ evaluate_policy / enforce（純関数）
├── application/   validate_evidence / measure / verify
├── adapters/      claude/ fs/ git/ otel/ report/
└── cli/
```

| 層 | import 許可（positive allowlist） |
|---|---|
| `domain` | `morrow.domain`, `pydantic`, `enum`, `math`, `statistics`, `decimal`, `dataclasses`, `typing`, `collections.abc`, `hashlib` |
| `application` | 上記 + `morrow.application`, `abc`, `asyncio` |
| `adapters` | 上記 + `morrow.adapters` + 任意の外部 |
| `cli` | すべて |

`tests/architecture/test_layers.py` が AST を走査し、許可されない `import` を検出したら fail。
`importlib` / `__import__` の呼び出しも別規則で検出する。

**保証範囲の正直な記述**: これは**静的 import に対する検査**である。
`eval` や属性経由の間接呼び出しは捕捉しない。テストの docstring にそう書く。

---

## 11. SigNoz / OpenTelemetry

**判定が終わった後に送出する。**テレメトリ障害が verdict を変えないことを構造的に保証する。

| 面 | 内容 |
|---|---|
| Traces | `morrow.experiment` → `morrow.pair` → `morrow.run` → 各操作（**エージェントの作業軌跡そのもの**） |
| Metrics | 成分ごとの pair 別値、`FFR_gate` / `FFR_display`、成功数、有効 pair 数 |
| Logs | 正規化イベント（自由文なし）と判定理由 |
| Dashboard | `dashboards/morrow.json` をコミット |
| Alert | `morrow.future_friction_ratio > 閾値`（デモ用。判定の権威はポリシーエンジン） |

**SigNoz を判定入力にしない。**非同期送出・取り込み遅延・重複投入により
同じ証拠から別の verdict が出るため、C4（決定論）が嘘になる。

---

## 12. 公開済み評価資材スナップショット（「事前登録」とは呼ばない）

R3 の指摘どおり、annotated tag の日時は指定でき、tag は移動・削除できる。
**第三者が検証できる事前登録にはならない。**

v4 で実際に行うこと:

1. 録画前に、以下を public remote に push する。

   ```
   src/  policies/  future-packs/  regression-tests/  acceptance-tests/
   prompts/  uv.lock  experiments/<id>.plan.json
       plan.json = { K, run_order, model, provider CLI version, limits,
                     再試行規則, ヌルの許容帯と外れた場合の処理 }
   ```

2. 各カセットの manifest に `source_tree_digest`（カセットを除く）と `plan_sha256` を書く。
3. **全試行を記録する。**無効化した pair も理由付きで残す。
4. レポートに「試行 N pair、有効 M、無効 K（理由）」を必ず出す。
5. 凍結対象を録画後に変更したら、その実験を破棄して新しい実験 ID を発行する。
   古いカセットに新しい policy を当てて再計算しない。

**主張の強度**: これは「私が結果を見てから物差しを調整しなかった」ことを
**運用として担保する仕組み**であって、第三者への証明ではない。README にそう書く。

---

## 13. テスト戦略

必ず書くテスト:

* §4.2 の**状態 × モードの全組み合わせ**について終了コードを固定する
* **fail-open の不在**: 証拠・インフラ・信頼境界・比較不能のエラーが**全モードで exit 2**
* **pairing**: `r[i,p]` を pair 内で計算してから中央値を取る（variant 別中央値の比と結果が異なる例で固定）
* **相殺しないこと**: `r = [10.0, 0.1]` → `FFR_gate > 1`
* **`ADAPTATION_REGRESSION` が到達可能**: candidate 全失敗 + baseline 全成功で発火する
* **churn**: 新規ファイルのみ作成した run で `final_churn > 0`
* **churn**: `git add` / `git commit` / `.gitignore` 変更後も正しい
* **churn**: 受入コマンドが作った `.pytest_cache` が計上されない（スナップショット順序）
* **venv**: エージェントが venv にパッケージを入れても受入判定が変わらない
* **テスト分離**: `acceptance-tests` が pre で落ちても実験は無効化されない
* **回帰**: 凍結テストを worktree 側で書き換えても検出される
* **ランチャ**: 直接 `pytest` を叩いた run が `EVIDENCE_INCOMPLETE` になる
* **schema スコープ**: K=4 の正しい証拠が `seq=0` の重複で弾かれない
* **schema**: 孤立 `tool_result` / 重複 `tool_ref` / 非連続 `seq` → `EVIDENCE_INVALID`
* **manifest**: 列挙されていないファイルの存在 → `EVIDENCE_INCOMPLETE`
* **policy**: `component_hard_max > clamp_ratio` や `minimum_valid_pairs > runs_per_variant` を拒否
* **境界**: `FFR_gate` が閾値ちょうど → PASS（log 空間 + epsilon）
* **射影**: 生イベントにシークレットとソース断片を仕込み、出力に一切現れない
* **golden bytes**: 同じ入力から常に同一バイト列の JSONL が出る
* **fixture inventory**: 採取した 76 行の `raw_kind` 別件数が一致し、未分類 0 件
* **tree walk**: symlink を追わない / FIFO で `INVALID_RUN`

---

## 14. クリティカルパス（27 時間）

| ゲート | JST | 内容 | 担当 |
|---|---|---|---|
| **G0** | 07-25 07:00 | scaffold / `mise run check` green / 設計をコミット | Claude |
| **G1** | 07-25 09:30 | **`foundryctl` で SigNoz 起動、smoke trace、`casting.yaml(.lock)` コミット** | **人間（host）** |
| **G2** | 07-25 14:00 | 正規化 → 検証 → 摩擦 → 判定 → レポート。§13 の必須テストが green | Claude + サブエージェント |
| **G3** | 07-25 16:00 | デモリポジトリ（`main` / `pr/1`）、両方で現行テスト green、**評価資材を push** | サブエージェント |
| **G4** | 07-25 21:00 | **16 run を逐次録画**（null 8 + coupling 8）。事前登録規則を適用し無選別で記録 | Claude |
| **G5** | 07-25 23:30 | OTel → SigNoz に軌跡表示、ダッシュボード、`morrow verify` が green | Claude |
| **G6** | 07-26 02:00 | GitHub push、CI green、デモ PR にチェック表示 | Claude |
| **G7** | 07-26 05:00 | **コードフリーズ**。README / 構成図 / スクリーンショット | Claude + 人間 |
| **G8** | 07-26 07:30 | デモ動画・提出文・AI 利用申告 | 人間 |
| **G9** | **07-26 09:00** | **提出** | 人間 |

**バッファ**: G4 → G5 の間に 2.5 時間、G6 → G7 に 3 時間を確保した。
録画のやり直しとヌル不合格に備える。

### 14.1 G1 を人間に移した理由

サンドボックスから docker socket に到達できないことが実測で判明している。
クリティカルパス最初の 2.5 時間を、実行できない担当に割り当てない。

### 14.2 並列レーン

| レーン | 担当 | 範囲 |
|---|---|---|
| A | Claude Code（本セッション） | 実行アダプタ・OTel・録画・統合 |
| B | サブエージェント | `src/morrow/domain/` + `tests/unit/` |
| C | サブエージェント | `demo/` + デモリポジトリ |
| D | 人間 | G1・登録・提出フォーム・動画 |

---

## 15. ハッカソン公式要件（一次情報で確認済み）

| 項目 | 確認結果 |
|---|---|
| **Foundry 必須** | rules に "Install SigNoz using Foundry. Foundry installs both SigNoz and its MCP server in one step." |
| CLI 名 | `foundryctl`。`gauge` / `forge` / `cast`（`-f casting.yaml`） |
| **必須ファイル** | rules に "Your repo must include the casting.yaml and casting.yaml.lock." |
| トラック | 01 AI & Agent Observability（これで出す） |
| 審査 | 定性のみ。"The more SigNoz features you use, the better your chances" |
| **AI 利用の未開示は失格** | README と提出フォームの両方に書く |
| 締切 | **プロジェクト提出の日時・タイムゾーンは公式に記載なし** |

### 15.1 未検証の伝聞（事実として扱わない）

* 「`foundryctl forge` が `casting.yaml.lock` を生成する」
* 「MCP は既定で無効で `spec.mcp.spec.enabled: true` が必要」
* 「`casting.yaml` に `kind: Installation` が必須」

G1 で `foundryctl --help` と実際の生成物から確認する。

### 15.2 G1 の受入条件

```
[ ] foundryctl を固定バージョンで導入し、version と checksum を記録する
[ ] gauge → forge → 生成された casting.yaml.lock を検証してコミット → cast
[ ] lock が自動生成されない場合、手作りせず G1 を失敗として扱い原因を記録する
[ ] SigNoz UI が開く
[ ] OTLP へ smoke trace を送り、SigNoz 側でクエリできる
```

---

## 16. リスク

| 項目 | 状態 | 対応 |
|---|---|---|
| `foundryctl` と `casting.yaml.lock` | **未検証** | G1 で人間が実行。失敗なら原因を README に記録 |
| ヌルの `FFR_gate` が 1.20 を超える | **未知**。超えたら測定器として不合格 | 閾値を緩めず「分離できなかった」と報告する（§9） |
| K=4 でも分散が大きい | あり得る | **全 pair の `r[i,p]` をレポートに出す**。中央値だけを見せない |
| 16 run が時間内に終わらない | 逐次 50〜130 分 | G4 に 5 時間確保。超過したら `coupling` を優先し `null` を先に取る |
| OS レベル隔離が無い | **未実装（P1）** | C5 として「主張しない」に明記 |
| 実行環境の逼迫 | 6 CPU / 15.6 GiB | **並列にしない**（contention を treatment 差に混ぜない） |

---

