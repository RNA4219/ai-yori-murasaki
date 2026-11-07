# Third-Party Notices

この配布物（ai-yori-murasaki）の自作コードは [LICENSE](LICENSE) のMITライセンスで提供する。本書は再配布に伴う第三者著作物の表示をまとめる。依存パッケージのライセンスをMITへ書き換えない。

## 実行時依存（pyproject.toml で固定）

版とライセンスは、ローカル実行環境にインストールされた配布メタデータ（importlib.metadata）の実物から2026-11-07に確認した。ネット情報からの推測ではない。

| パッケージ | 版 | ライセンス（メタデータ実物） | 用途 |
| --- | --- | --- | --- |
| jsonschema | 4.26.0 | License-Expression: MIT | 証跡・assessment reportのJSON Schema検証 |
| referencing | 0.37.0 | License-Expression: MIT | jsonschemaの参照解決（動的依存） |
| cvss | 3.6 | License: LGPLv3+ | CVSS v4.0ベクトルの算出（R-056） |
| packaging | 26.0 | License-Expression: Apache-2.0 OR BSD-2-Clause | 依存CVEのPEP440版範囲判定（advisories） |

- `referencing` は `jsonschema` の動的依存であり自作扱いしない。
- `cvss` は LGPLv3+ であるため、自作コードのMIT表示にライセンス文言を転記しない。LGPLv3+ の配布義務は当該パッケージ側のライセンス原文（PyPI配布物に同梱）が正本。利用者は `pip show cvss` 等で同梱ライセンスを確認すること。
- `packaging` はデュアルライセンス（Apache-2.0 OR BSD-2-Clause）。いずれか一方を選択して利用できる。ライセンス原文はPyPI配布物に同梱。
- 各ライセンスの全文は各パッケージの配布物（wheel内のライセンスファイル、または各公式リポジトリ）から取得する。本リポジトリは全文を複製しない。

## CVSS v4.0 仕様の帰属

CVSS v4.0 の算出仕様は FIRST (Forum of Incident Response and Security Teams) が定める。CVSSはFIRSTの登録商標の場合がある。仕様の詳細は FIRST の公式仕様書を正本とし、本製品は `cvss` パッケージを介して算出する（`calculator: "cvss/3.6"` として記録）。

## 配布方式

- wheel / sdist いずれにも本書（THIRD_PARTY_NOTICES.md）と LICENSE を同梱する。
- `ai_yori_murasaki` パッケージデータ（schemas/v1、web、lab_assets、assessment_assets、adapters/contracts、default-policy.json）をwheel/sdistに含める。
- 本書は公開準備（R-063）の一部であり、公開操作そのものではない。
