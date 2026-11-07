# ENRICHMENT-FORMATS (R-046 / SPEC A-5)

短い記録。原典は再配布せず、URL・形式・製品側選択のみ。

## 1. 原典URL

| kind | URL | 備考 |
|---|---|---|
| attack | https://github.com/mitre-attack/attack-stix-data (enterprise-attack/enterprise-attack.json) | STIX 2.1 bundle |
| kev | https://www.cisa.gov/known-exploited-vulnerabilities-catalog (JSON) | schema: catalogVersion/dateReleased/count/vulnerabilities |
| epss (API) | https://api.first.org/data/v1/epss | JSON `{status,data:[{cve,epss,percentile,date}]}` |
| epss (CSV) | https://epss.empiricalsecurity.com/epss_scores-current.csv.gz | gzip CSV。先頭 `#` metadata 行 + header `cve,epss,percentile` |

## 2. 入力形式（製品側で受理する形）

- **attack**: STIX 2.1 bundle JSON。`objects[]` の `type=="attack-pattern"` から `external_references[].source_name=="mitre-attack"` かつ `external_id` が `T####` / `T####.###` のもののみ抽出。完全 STIX validator ではない。
- **kev**: JSON object。`catalogVersion`(str), `dateReleased`(date-time), `count`(int), `vulnerabilities`(array) 必須。各要素は `cveID`(CVE-YYYY-NNNN..), `vendorProject`, `product`, `vulnerabilityName`, `dateAdded`, `shortDescription`, `requiredAction`, `dueDate` 必須。`count != len(vulnerabilities)` は reject。
- **epss CSV**: 先頭 `#` 行は metadata（`model_version:...` 等）。header は `cve,epss,percentile`。score は 0..1 の有限 float。NaN/bool は reject。
- **epss JSON**: `{"data":[{cve,epss,percentile,...}]}`。epss/percentile は文字列でも可（float 変換）。

## 3. 製品側の選択（実装判断）

- **圧縮**: 自動解凍しない。gzip は呼び出し側で展開してから渡す。
- **入力上限**: 12 MiB (`MAX_INPUT_BYTES`)。超過は reject、切り詰めない。
- **TTL default**: attack=30日、kev=1日、epss=1日。`ttl_seconds` で正の有限 int のみ上書き可。
- **期限切れ/不在**: `query_snapshot` は `status="expired"|"missing"|"corrupt"` と `score=null` を返す。古い値で優先付けしない。
- **source_version**: 必須。空文字 reject。
- **as_of と fetched_at**: 別フィールド。両方 future は reject。
- **CAS**: 原文をUTF-8のJSON wrapperに格納し、そのcontent digestと原文bytesのSHA-256を別に保持する。`expected_sha256`指定時は原文の実体hashと照合し、不一致を拒否する。query時もwrapperから戻した原文bytesを照合する。
- **重複ID**: 最初の出現を採用し、`duplicates` に記録（情報を残す）。
- **未知フィールド**: 保持（reject しない）。
- **ネットワーク**: 一切起動しない。offline import のみ。

## 4. 未対応 / 非主張

- 完全な STIX 2.1 validator ではない。
- EPSS API の `date`/`days`/`epss-gt` 等のクエリパラメータは製品側では扱わない（取得は呼び出し側）。
- KEV の `cwes`/`forensicTriage` 等の追加フィールドは保持するが検証しない。
