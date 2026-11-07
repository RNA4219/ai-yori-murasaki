---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-06
---

# 判断台帳（改訂稿）

根拠列は **U（ユーザー逐語要求）/ S（監督設計判断）/ R（原レポート由来）** を区別する。

| ID | 判断 | 根拠 | 状態 | 影響 |
|---|---|---|---|---|
| D-001 | 操作主は1名を既定とする | S（原レポートL48） | 採用 | 権限モデル・UI・承認フロー |
| D-002 | ローカル認可ラボを既定環境とする | S（原レポートL48） | 採用 | v1検収範囲 |
| D-003 | OS異なる実行workerはadapter化する | S（原レポートL30） | 採用 | R-038/R-039 |
| D-004 | 危険target実行は外側OS隔離必須、未構成HOLD | S（原レポートL427） | 採用 | R-023 |
| D-005 | 依存ツール不在・認証不在は接続不可明示、PASS捏造禁止 | S（原レポートL522–L523） | 採用 | R-024 |
| D-006 | 実環境で検証できない項目は未検証として残す | S | 採用 | 検収条件 |
| D-007 | 固有組織の規模・SLA・台数・本番認証は設定入力 | S（原レポートL25–L42） | 採用 | R-006 |
| D-008 | 推奨KPIを実証済みにしない | S（原レポートL60）、U | 採用 | R-003 |
| D-009 | 目的・採用閾値は評価前に固定 | S（RSI節1）、U | 採用 | R-004 |
| D-010 | 50〜500 hostは性能保証にしない（将来運用条件） | S（原レポートL29） | 採用 | R-005 |
| D-011 | 任意の第三者攻撃・本番自動変更・対外公開はGoal外 | S（実行範囲解釈） | 採用 | R-045 |
| D-012 | 自身のコードはMIT | U | 採用 | R-045 |
| D-013 | モデル体制: ユーザー回答による指定（監督Codex、レビューGLM5.3、実装DeepSeek1017→Ollama登録モデル）。開発時呼出成功（Ollama /api/tagsで登録確認済み、DeepSeek+GLM実呼び出しログあり）。製品接続の検収未実施。モデルpin不成立（alias unpinned） | U（指定）、S（4点分離） | 採用（4点分離で記録） | R-049/R-043 |
| D-014 | workflow-cookbook Tier3遵守、RUNBOOKでImozuru/Yomiyasu使用 | U | 採用 | 運用手順 |
| D-015 | RSIの固有元プロジェクト名を出さず一般化 | U | 採用 | 文書表現 |
| D-016 | Evidence hashchainと外部trust anchorを分け、真実性は別と明示 | S、原典調査c2sp/crosby | 採用 | R-032 |
| D-017 | tail truncation検知不能を明示 | S、原典調査crosby L101 | 採用 | R-032 |
| D-018 | 秘匿化後canonicalをhash対象、raw secret非保存 | S、原典調査owasp L686/L696 | 採用 | R-033 |
| D-019 | full snapshot rewriteを許さない（不可能保証はしない） | S | 採用 | R-033 |
| D-020 | Blue viewとOS境界を区別する | S（原レポートL425–L441） | 採用 | R-009/R-023 |
| D-021 | hidden Oracleを維持する | S（原レポートL91） | 採用 | R-009/R-014 |
| D-022 | 無効run除外とcandidate起因失敗の分母保持 | S（RSI節6） | 採用 | R-042 |
| D-023 | 未見holdout再使用禁止 | S（RSI節7） | 採用 | R-042 |
| D-024 | モデルaliasのunpinnedを明示 | U、S | 採用 | R-043 |
| D-025 | DB・UI・APIの実装詳細は次工程の仕様へ回す | S | 採用 | 本稿範囲外 |
| D-026 | 原典引用は長く写さず参照を示す | S | 採用 | 文書表現 |
| D-027 | 既存Python試作の実装を要求根拠にしない | S | 採用 | 全要件 |
| D-028 | 契約試験と外部実接続検証を区別 | S | 採用 | R-019 |
| D-029 | kaggle-campaign-opsは未指定・検証不能として扱う | R（原レポートL15/L42/L689） | 採用 | R-028 |
| D-030 | OracleのLLM非依存は無条件MUST、例外を新設しない | S（監督判断） | 採用（確定） | R-014 |
| D-031 | T14のFAIL=修復成功は攻撃不成立FAILに限定 | S（Sankai A4）、原典調査cybergym L14 | 採用 | R-016 |
| D-032 | T13=ラボ固定benign suiteでFP0（事前固定、母集団precision保証外）、T09=秘密除去済みでID/件数/位置/参照/拡張保持 | S（監督確定） | 採用（確定） | R-018 |
| D-033 | Dev=既定deny、ラボ一時workspaceへの検証済みpatch適用のみ許可、production常時deny | S（監督確定） | 採用（確定） | R-035 |
| D-034 | 外部trust anchorはv1で別管理読み取り専用checkpoint検証を実装、外部署名サービス運用は将来 | S（監督確定） | 採用（v1実装） | R-032 |
| D-035 | Experience Memoryは任意、snapshot固定 | S（RSI節8） | 採用 | R-042 |
| D-036 | HTMLは実記録から状態表示、原典実装を複製しない | U、原典調査artex | 採用 | R-044 |
| D-037 | 推論基盤の性能実証はv1検収外 | S（原レポートL553） | 採用 | R-041 |
| D-038 | 原レポートの引用トークンは解決不能として扱う | S（Sankai共通条件） | 採用 | 文書表現 |
| D-039 | 外部API・製品仕様・ライセンス・性能の主張は原典と対象バージョンで再確認 | S（Sankai共通条件） | 採用 | 全要件 |
| D-040 | 約3回の圧縮保存と見直しを工程に含める | U | 採用 | 工程管理 |
| D-041 | 実環境スナップショット（Docker Desktop Linux daemon起動済み、trivy/codeql/rdsh PATH不在）をv1入力として固定 | S（監督確認） | 採用 | R-024 |
| D-042 | 主要adapterは7系統（Trivy, CodeQL SARIF, Wazuh, Sigma, RustXNG, rdsh/Red, LLM）を具体列挙、未取得は接続状態明記 | S（監督判断） | 採用 | R-040 |
| D-043 | KPI測定機能の実装とラボ測定成立はv1必須（数値合否はv1検収外） | S（監督判断） | 採用 | R-002 |
| D-044 | v1完成条件は5段階実動作系列（Red観測成功→Blind検知→修正→攻撃不成立＋正常回帰成功→レビュー待ち） | S（監督判断） | 採用 | R-012 |
| D-045 | 完成条件は単一成功でなくT01-20, RSIゲート, fail closed, adapter契約試験, ローカル実動閉ループ, 通常回帰を含む | S（監督判断） | 採用 | R-048 |
| D-046 | P2の自動Purple反復・複数モデルrouting・detection tuning・timeout/crash回復はv1ラボ機能として対応 | S（監督判断） | 採用 | R-047 |
| D-047 | ATT&CK mappingとKEV/EPSS enrichmentは実装・日付・出典・期限切れ・未取得挙動を持つ要件 | S（監督判断） | 採用 | R-046 |
| D-048 | HTMLには実行を判断する項目だけを表示、ライセンス・実装細部を各UI画面へ強制表示しない | S（監督判断） | 採用 | R-044 |
| D-049 | 各文書で同一文言強制は不要、正本をリンクし意味一致を確認 | S（監督判断） | 採用 | R-001 |
| D-050 | 未測定時は達成表示禁止、測定済みは対象集合/条件/限界を併記して表示可能 | S（監督判断） | 採用 | R-003 |
| D-051 | 未設定SLA等は該当評価だけ未確認、無関係ラボを全HOLDにしない | S（監督判断） | 採用 | R-006 |
| D-052 | R036 required checkはminimum必須であり列挙を上限にしない | S（監督判断） | 採用 | R-036 |
| D-053 | RustXNG standard/clean優先、provider条件の個別確認を落とさない。rdsh HARNESS-ADAPTER原典はcompile-time trait接合でありruntime adapter選択ではない。Pythonから公開CLIを呼ぶadapterとRust内部traitを混同しない。未確認CLIフラグは捏造しない | S（監督判断） | 採用 | R-038 |
| D-054 | LLM adapterはprompt hash, requested/effective model, version/pin, temperature/seed, timeout, tokencap, tool calls, response metadataを記録。tool direct権限をモデルに与えない | S（監督判断） | 採用 | R-040 |
| D-055 | R022のredact前後台帳追跡は除去した種類/位置/ルール版のみ記録、秘匿化前本文や低エントロピー秘密の単純hashは保存しない | S（監督判断） | 採用 | R-022 |
| D-056 | R025の生JSON保持は秘密除去済みの元構造JSONのみ | S（監督判断） | 採用 | R-025 |
| D-057 | R032 checkpointはoperator保管、agent/workerのwriteから分離、最終検収/再生前に照合、未提供HOLD。署名サービス運用は将来、same-admin敵対ホストを守る保証はしない | S（監督判断） | 採用 | R-032 |
| D-058 | R042の9点ごとの検収条件を明記。ネットワーク障害の無効とcandidate由来未完了を分け分母を事前固定。immutableモデルが無い比較は探索用、改善実証・promotion-readyへ進めない。決定的policy候補のラボ比較はLLMなしで可能 | S（監督判断） | 採用 | R-042 |
| D-059 | 多人数運用、外部署名サービス、本番deployment、DGX性能数値は将来運用条件 | S（監督判断） | 採用 | 全体 |
| D-060 | 未確定事項は製品実装前に監督設計判断で閉じる（保守的v1既定）。組織固有値だけ設定待ちとして残す | S（監督判断） | 採用 | 全体 |


追加判断 D-050: ユーザーの2026-11-06追加指示に従い、GLMへ実際のフロントエンドスキルを渡して設計・レビュー、DeepSeekへ実装を割り当てる。根拠はR-050。

現行実装の検証結果と配置は[製品検収の対応表](../PRODUCT-ACCEPTANCE.md)を参照する。3段階の見直しはCOMPACTION-01〜03に保存した。
