# MORROW 計測モデルと信頼境界

> 設計の全体像は [design.md](design.md) を参照。

## 2. 信頼境界（主張を正確に）

```
evaluator 資材（worktree の外に置く）
    <state_root>/<experiment_id>/
        plan.json  policy/  pack/  regression-tests/  acceptance-tests/
        snapshots/<run_id>.pre/          ← pre ツリーの実体コピー（§3.4）
        launcher-log/<run_id>.jsonl      ← テスト実行の一次記録（§6.4）

measured 資材（agent の cwd）
    <work_root>/<run_id>/                ← worktree。ここだけが agent の作業対象
```

**これは権限境界ではない。**
agent は同一 UID で動くため、`<state_root>` にも到達できる。
worktree の外に置くのは**事故と取り違えの防止**であり、敵対的エージェントには効かない。

したがって **`measure` / `gate` は信頼済みリポジトリでのみ実行する**（C5）。
OS レベルの隔離（別 UID / コンテナ / ネットワーク遮断）は P1 以降。

`UNTRUSTED_TARGET` の判定は、リポジトリ名の許可リストではなく以下の**全一致**で行う:

```
target.repository      == 許可リストのいずれか
target.head_repository == target.repository        # fork からの PR を弾く
trigger                ∈ {workflow_dispatch, push}  # pull_request / pull_request_target は不可
head_sha               ∈ 明示的に承認した SHA
```

いずれか一つでも外れたら、**エージェントを起動する前に exit 2**。

---

## 3. 計測モデル

### 3.1 対反復（pair）

R3 の指摘どおり、v3 は「variant ごとに中央値 → 比」で **pairing を捨てていた**。
これでは時間ドリフトや warm cache の影響を treatment 差と誤認する。

```
K = 4（偶数。AB / BA を同数にするため）

pair p ∈ {0,1,2,3}
    各 pair は baseline run と candidate run を隣接して実行する
    実行順  p=0: A→B   p=1: B→A   p=2: A→B   p=3: B→A

成分 i、pair p:
    r[i,p] = clamp( (c[i,p] + α) / (b[i,p] + α),  1/R,  R )

成分 i:
    r[i]   = median_p( r[i,p] )        ← ★ pair 比の中央値。variant 別中央値の比ではない

FFR_gate    = exp( Σ wᵢ · ln( max(1, r[i]) ) / Σ wᵢ )
FFR_display = exp( Σ wᵢ · ln( r[i] )         / Σ wᵢ )
```

**レポートには `r[i,p]` を全部出す。**中央値だけを見せない。
4 本の比がどれくらいばらついているかは、読者が自分で判断すべき情報である。

### 3.2 pair の有効性と再試行

| 事象 | 扱い |
|---|---|
| pair の片方が infra 障害（MORROW のクラッシュ / API 障害 / worktree 作成失敗） | **pair 全体を無効化** |
| 無効化した pair の再実行 | `policy.experiment.max_pair_retries`（既定 2）まで。**事前に登録する** |
| 有効 pair < `policy.experiment.minimum_valid_pairs`（既定 3） | `INFRASTRUCTURE_ERROR` → **exit 2** |

エージェントの時間切れ・予算超過は infra 障害ではなく **`success = 0`** として扱う（有効な観測）。

### 3.3 成分（本当に相互排他にする）

R3 の指摘どおり、v3 の `other_tool_calls` は `PATCH` を含み、その結果の行差分が `final_churn` にも入るため
**同じ編集を 2 軸で重複加重していた**。v4 では 3 成分に絞る。

| 成分 | 重み | 一次ソース | 何を測るか |
|---|---|---|---|
| `files_read_distinct` | 1.0 | `FILE_READ` の distinct な path ID | 理解しなければならない範囲 |
| `test_cycles` | 1.0 | **テストランチャの記録**（§6.4） | 試行錯誤の回数 |
| `final_churn` | 1.0 | **pre ツリー実体との差分**（§3.4） | 実装の物理量 |

`SEARCH` / `PATCH` / `COMMAND` / `TOOL_OTHER` の件数は**記録し表示するが、gate には使わない**。
`output_tokens` / `api_duration_ms` / `cost_usd` も同様に表示のみ。

**成分集合は policy に固定する。**必須成分が片側でも欠けたら `EVIDENCE_INCOMPLETE` → exit 2。

### 3.4 churn（pre ツリーの実体を保持する）

R3 の指摘は情報理論的に正しい。**`(サイズ, sha256)` からは行差分を復元できない。**

```
① worktree 作成
② pre スナップショット:  worktree の実体を <state_root>/snapshots/<run_id>.pre/ へコピー
③ エージェント実行
④ agent のプロセスグループ消滅を確認
⑤ post スナップショット: worktree を tree walk
⑥ final_churn = pre ツリーと post ツリーの実ファイル差分
⑦ 受入・回帰テストを実行（★ churn 確定より後。生成物は計上されない）
```

デモリポジトリは小さい（数百 KB）ので、実体コピーのコストは無視できる。

```
final_churn = Σ 追加ファイルの行数
            + Σ 削除ファイルの行数
            + Σ 変更ファイルの (追加行 + 削除行)      ← difflib による実差分

除外: policy.metrics.churn_exclude の固定 allowlist
      (.venv/, __pycache__/, .pytest_cache/, .ruff_cache/, .git/, *.pyc)
```

### 3.4.1 tree walk の安全性と決定性

| 対象 | 扱い |
|---|---|
| symlink | **追わない**（`lstat`）。リンク先の文字列長のみを記録し、内容は読まない |
| FIFO / socket / device / ハードリンク多重 | 検出したら **その run を `INVALID_RUN`** |
| バイナリ（UTF-8 デコード不能） | 行数に混ぜない。**`binary_bytes_changed` として別集計**し、gate には使わない |
| ファイル数 / 総バイト / 単一ファイルサイズ | policy の上限を超えたら `INVALID_RUN` |
| 走査中の変更 | 走査前後の mtime 集合が変わっていたら `INVALID_RUN` |
| 走査順 | 相対パスのバイト列昇順に固定（決定性） |

バイナリを `bytes/80` で行数に換算する v3 の案は撤回した。単位に意味がなく、
画像や lockfile の変更だけで指標が跳ねるため。

### 3.5 実行環境（エージェントに書き換えさせない）

実測で**エージェントが `pip install` した**以上、venv が worktree 内にあると
「依存を入れて受入テストを通す」が可能になり、しかも `final_churn` は 0 のままになる。

```
venv は worktree の外に作る:
    <state_root>/venvs/<run_id>/          ← UV_PROJECT_ENVIRONMENT で指定

受入・回帰テストの実行時:
    リポジトリの lockfile から <state_root>/venvs/<run_id>.verify/ を作り直す
    → エージェントが入れたパッケージは受入判定に影響しない

依存の変更は lockfile の差分として final_churn に現れる（lockfile は worktree 内にある）
```

### 3.6 成功判定とテストの分離

R3 の指摘どおり、v3 は「未来タスクの受入テスト」と「既存挙動を守る回帰テスト」を混同していた。
未来タスクの受入テストは **run 前に落ちているのが正常**なので、
「pre で落ちていたら実験無効」という規則と衝突する。

```
<state_root>/regression-tests/     既存挙動を守る。pre で全通過が必須。post で同一 bytes を再実行
<state_root>/acceptance-tests/     未来タスクの受入。post でのみ実行（pre では落ちて当然）
```

| | pre | post |
|---|---|---|
| `regression-tests` | **全通過が必須**。落ちていたら `EVIDENCE_INCOMPLETE` → exit 2 | 全通過しなければ `REGRESSION` |
| `acceptance-tests` | 実行しない | 全通過しなければ `success = 0` |

```
success[v, p] = 1  ⟺  acceptance-tests 全通過
                    ∧ regression-tests 全通過
                    ∧ invariants 成立
                    ∧ 壁時計・予算の上限内
```

実行時の固定条件: `cwd` = worktree ルート、`PYTHONPATH` は設定しない、
`-p no:cacheprovider` を付ける、プラグイン自動ロードを無効化（`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`）。

### 3.7 FFR と適応の判定に使う集合

R3 の指摘どおり、v3 は `comparable_runs >= minimum_paired_runs = 3` かつ K=3 だったため、
**candidate が 1 回でも失敗すると `ADAPTATION_REGRESSION` に到達できなかった**。

```
valid_pairs      = infra 的に有効な pair            （§3.2）
successful_pairs = 両 variant が success した pair

適応の判定  : valid_pairs 全体で行う
FFR の計算  : successful_pairs でのみ行う
```

| 条件 | 所見 |
|---|---|
| `len(valid_pairs) < minimum_valid_pairs` | `INFRASTRUCTURE_ERROR` |
| baseline の success 数 == 0 | `INCONCLUSIVE`（対照が成立していない） |
| baseline の success 数 ≥ 閾値 かつ candidate の success 数 == 0 | `ADAPTATION_REGRESSION` |
| `len(successful_pairs) < minimum_ffr_pairs`（既定 3） | FFR を出さない。`INCONCLUSIVE` |
| `FFR_gate > policy.decision.friction_threshold` | `FRICTION_REGRESSION` |
| いずれかの `r[i] > policy.decision.component_hard_max` | `SINGLE_AXIS_REGRESSION` |

### 3.8 ヌルコントロールと閾値（循環を断つ）

**閾値はヌルから導出しない。**導出すると、ヌル自身が同じ規則で自動的に通り、循環する。

```
閾値 friction_threshold は policy に固定値として書き、
treatment のデータを見る前に公開スナップショットへ含める（§12）

ヌルコントロール（main の独立クローン A vs B、同じ K=4）は
    ・同じ手続きで取得する
    ・レポートで treatment と並べて提示する
    ・「規則の妥当性の参考」であって、閾値の計算入力ではない
```

**ヌルの結果に対する事前登録済みの処理**:

| ヌルの `FFR_gate` | 処理 |
|---|---|
| `≤ policy.null_control.maximum_ffr`（固定値。既定 1.20） | 正常。treatment の結果を提示する |
| それを超えた | **その日の全シナリオを `INVALID_EXPERIMENT` → exit 2**。<br>閾値を緩めて通すことはしない。「この環境では分離できなかった」と報告する |

**統計的主張はしない**（C6）。K=4 の符号検定では片側 p の下限が 1/16 = 0.0625 であり、
有意性は原理的に言えない。提示するのは
**「事前に登録した規則の下で、同時に取得したヌルを上回る観測が得られた」**という事実のみ。

### 3.9 数値の整合と境界

```yaml
# policies/default.yaml（closed schema。未知キーは拒否）
experiment:
  runs_per_variant: 4            # = pair 数 K
  minimum_valid_pairs: 3
  minimum_ffr_pairs: 3
  max_pair_retries: 2
metrics:
  alpha: 1.0                     # > 0
  clamp_ratio: 10.0              # R >= 1
  small_sample_floor: 3
  weights:                       # すべて > 0、総和 > 0
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

**cross-field 検証**（違反は起動時に拒否）:

```
alpha > 0
clamp_ratio >= 1
1 < friction_threshold <= component_hard_max <= clamp_ratio
minimum_valid_pairs <= runs_per_variant
minimum_ffr_pairs   <= runs_per_variant
すべての weight > 0 かつ Σ weight > 0
```

**小標本**: 成分 i について `b[i,p]` と `c[i,p]` がともに `small_sample_floor` 未満の pair は、
その成分の比を計算せず `data_quality` に記録する。全 pair でそうなった成分は gate から外す。

**浮動小数点の境界**: 比較は log 空間で行い、`Decimal` に変換して `epsilon` を明示的に使う。

```
FRICTION_REGRESSION  ⟺  ln(FFR_gate) > ln(friction_threshold) + epsilon
```

これにより「ちょうど閾値なら PASS」が環境をまたいで再現する。

---

