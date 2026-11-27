# Third-Party Notices

この配布物（ai-yori-murasaki）の自作コードは [LICENSE](LICENSE) のMITライセンスで提供する。本書は再配布に伴う第三者著作物の表示をまとめる。依存パッケージのライセンスをMITへ書き換えない。

## 実行時依存（pyproject.toml で固定）

版とライセンスは、ローカル実行環境にインストールされた配布メタデータ（importlib.metadata）の実物から2026-11-07に確認した。ネット情報からの推測ではない。

| パッケージ | 版 | ライセンス（メタデータ実物） | 用途 |
| --- | --- | --- | --- |
| jsonschema | 4.26.0 | License-Expression: MIT | 証跡・assessment reportのJSON Schema検証 |
| jsonschema-rs | 0.61.2 | MIT | モニター読み取り時の証跡イベント検証（Rust実装） |
| ijson | 3.6.0 | BSD-3-Clause AND ISC | 案件一覧の構築時に既存の実行索引を逐次読み込み |
| referencing | 0.37.0 | License-Expression: MIT | jsonschemaの参照解決（動的依存） |
| cvss | 3.6 | License: LGPLv3+ | CVSS v4.0ベクトルの算出（R-056） |
| packaging | 26.0 | License-Expression: Apache-2.0 OR BSD-2-Clause | 依存CVEのPEP440版範囲判定（advisories） |

- `referencing` は `jsonschema` の動的依存であり自作扱いしない。
- `cvss` は LGPLv3+ であるため、自作コードのMIT表示にライセンス文言を転記しない。LGPLv3+ の配布義務は当該パッケージ側のライセンス原文（PyPI配布物に同梱）が正本。利用者は `pip show cvss` 等で同梱ライセンスを確認すること。
- `packaging` はデュアルライセンス（Apache-2.0 OR BSD-2-Clause）。いずれか一方を選択して利用できる。ライセンス原文はPyPI配布物に同梱。
- 各ライセンスの全文は各パッケージの配布物（wheel内のライセンスファイル、または各公式リポジトリ）から取得する。本リポジトリは全文を複製しない。

任意のMCP取り込み用依存は `mcp==2.3.0`（MIT）。2026-11-09にインストールした配布メタデータの `License: MIT` を確認した。`pip install '.[mcp]'` で導入し、SDKや依存パッケージのライセンス原文は各配布物を参照する。画面検証に使ったaxe-coreは検証環境だけに導入し、製品へ同梱しない。

## CVSS v4.0 仕様の帰属

CVSS v4.0 の算出仕様は FIRST (Forum of Incident Response and Security Teams) が定める。CVSSはFIRSTの登録商標の場合がある。仕様の詳細は FIRST の公式仕様書を正本とし、本製品は `cvss` パッケージを介して算出する（`calculator: "cvss/3.6"` として記録）。

## 配布方式

Google SSO の任意依存 `google-auth==2.63.1` は Apache License 2.0 で配布される。ID トークンの RSA 署名検証に使用する。ライセンス原文は同パッケージの配布物に含まれる。

案件受付にはPyO3 0.27.2とrusqlite 0.38.0を使用し、SQLiteをネイティブ拡張に含める。依存の版は[Cargo.lock](rust/intake/Cargo.lock)に固定している。[ネイティブ拡張のライセンス文](rust/intake/THIRD_PARTY_LICENSES.txt)をwheelとsdistに同梱する。

構成図のローカルYAML読み込みに js-yaml 4.3.2（MIT）を使用する。配布されたESモジュールとライセンス原文を `ai_yori_murasaki/web/architecture/` に同梱する。[js-yaml](https://github.com/nodeca/js-yaml)

- wheel / sdist いずれにも本書（THIRD_PARTY_NOTICES.md）と LICENSE を同梱する。
- `ai_yori_murasaki` パッケージデータ（schemas/v1、web、lab_assets、assessment_assets、adapters/contracts、default-policy.json）をwheel/sdistに含める。
- 本書は公開準備（R-063）の一部であり、公開操作そのものではない。
