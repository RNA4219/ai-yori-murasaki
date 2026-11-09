---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# Blue評価とRSI適用の契約

## FindingとOracleの単位

Blueの `submit_findings` は、1件の報告につき1件の観測証拠を保存する。事象の識別はcampaign、run、path、role、category、method、evidence_refの組とする。タイトルや説明の変更だけによる再送は新しい事象にしない。同一組の再送は、同じ呼び出し内でも別の呼び出しでも重複として扱う。異なる証拠参照は、path・role・categoryが同じでも保持する。時刻や提出順は重複判定に使わない。

`finding.schema.json` の構造は維持する。証拠を1つのfindingへ統合する方式は、既存の保存済みfindingを書き換えるか複数版を保存する処理が必要になり、`references` の32件上限にも達する。今回は証拠参照を重複キーとfinding IDに含める方式を採用し、各証拠と報告の対応を保持する。既存の保存記録は書き換えない。旧版のrunを再採点してRSIへ戻す場合は、計測版を揃えた別評価とする。

Oracleの検知対象は、`GET /record`（query部分を除く）がstatus 200を返し、roleがadmin以外の観測である。CASのhashと観測本文を照合してから、証拠参照ごとに検知／見逃しを数える。同じ参照を入力に再掲しても検知数を増やさない。保存済みfindingが対象pathと証拠参照を指す場合に被覆とする。検知対象に結び付かないfindingは、finding IDごとに誤検知として数える。全対象の被覆と誤検知ゼロをblind判定の成功条件とする。対象がない試験を攻撃検知の成功にしない。

報告の `detected_events` / `missed_events` は観測事象単位、`true_positive_findings` / `false_positive_findings` は安定したfinding ID単位である。これはKPIのrecallとprecisionの分母に対応する。Oracleとlab KPIは同じ対象判定・参照照合関数を使用する。汎用Finding schemaにはadapter由来の集約報告もあるため、すべてのFindingを1観測へ制限するschema変更はしない。

brokerは、当該エージェントへ発行された参照だけを受理する。Redの参照、Oracleの正解、private ledgerをBlueへ追加公開しない。Purpleへは従来どおり許可されたfindingとtelemetryを渡す。

## 検証の範囲

再現・回帰は実broker、Runtime、Store、非LLM Oracleを用い、外部クラウドLLMなしで行う。偽LLM・偽Dockerの統合試験は実モデルの能力評価ではない。過去の検知失敗に今回の不具合が何件含まれたかは未再評価であり、過去の失敗を一括して真の検知漏れ、または今回の不具合と断定しない。

## 通常実行の方策選択

`aym lab demo` と `aym lab iterate` の選択順は次のとおりとする。

1. `--policy-bundle-file <絶対パス>` による操作者の明示指定。通常ファイル・JSON・bundle schema・既存のrole別ツール権限・予算上限を検証する。symlink、Windows reparse point、親経路の迂回、相対パスを拒否する。これは操作者の上書きであり、RSI採用とは記録上も区別する。
2. RSIのtrusted activationと一致するregistryのbundle。初期baselineは操作者が明示して登録する。候補の通常適用には、固定された比較、promotion-ready、承認review、baseline／candidateの期待hash一致が必要。
3. registryとtrusted activationがどちらも存在しない場合に限り、パッケージの既定bundleを使う。

選択した明示ファイルが不正、適用済みCASが破損、registryだけが存在、trusted activationとの不一致などの場合は停止する。読めない適用状態を「未採用」とみなしてデフォルトへ切り替えない。未承認候補をstateやCASに作成しただけでは通常実行へ反映しない。明示指定は自動選択より優先するため、壊れた自動適用状態でも、操作者が検証済みファイルを指定することはできる。

RSI評価器はfreezeで固定したbaseline／candidateを明示的に渡す。通常実行用registryで評価対象を差し替えない。既存のholdout分離・model pinning・改善閾値・安全性・正常回帰条件は緩めない。適用前には凍結条件とCASの比較pairからreadyを再計算し、workspaceのsummaryだけで採用しない。

## 適用と復旧

workspaceの `agent_rsi/registry.json` は、trusted rootの `agent_rsi/activation.json` の写しとする。trusted activationを確定点にし、承認reviewもtrusted rootに保存する。workspace側のreviewやcurrent refだけを書き換えても自動適用しない。これらの領域は従来どおりworkerへ渡さない。

初期化・適用・選択・ロールバックはStoreのlockで直列化する。適用時点のcurrent refが評価したbaselineと異なる場合は、古い評価結果による上書きを拒否する。registryの再初期化による履歴消去も拒否する。書き込み失敗は例外として返し、CLIは成功を返さない。workspaceの写しとtrustedの確定状態が食い違う場合は通常実行を停止する。

`agent-rollback` はtrustedの履歴から、最後の有効なapplyの `from_ref` を復元する。適用中のCASが壊れていても、復元先のCASが正常であれば復旧できる。workspaceの写しが破損している場合もtrustedから作り直す。期待current refの一致と理由を必須にし、任意のrefへの復旧は許可しない。履歴はapply／rollbackを追記し、通常実行を同じseedで再実行すると復元したbundleのhashが記録される。候補の `applied` は履歴上の状態であり、現在の適用方策は `agent-status` と実行証跡で確認する。

trusted pinのない旧registryは自動移行しない。実行を止め、旧ファイルを保全して操作者が内容を検証したうえで、分離した新規workspace／trusted rootに初期化する。trusted rootそのものを失った場合は、このロールバックでは復元できない。保全したtrusted記録の復旧か、再評価・再承認が必要になる。

## 方策の実行証跡

実行開始時にbundle本体をCASへ保存し、Red runの `agent_policy_selection` イベントへそのrefを記録する。`bundle_id`、`version`、`bundle_ref`、canonical JSONの `sha256`、`source`、`reason` を保存する。registry経由ではactivationのhashと、該当するreview／candidate IDも残す。run結果の `policy_selection` はこの記録と対応する。メタデータのID・hashと渡されたbundleの内容が一致しない場合は実行を拒否する。

`source` は `explicit_file`、`explicit_bundle`、`approved_registry`、`packaged_default`。`explicit_bundle` はfreeze済み評価などPython呼び出し元による指定を表す。失敗したラボrunにも、選択済みであれば同じ方策情報を返す。検証済みの選択情報はエージェントの自己申告から作らない。
