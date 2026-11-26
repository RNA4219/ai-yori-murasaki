"""課題に使う検査と、照合から外す資源を明示設定で選ぶ。"""


def _label(value):
    return isinstance(value, str) and bool(value.strip())


def _labels(value):
    return isinstance(value, list) and all(_label(label) for label in value)


def validate_drift_policy(drift):
    """設定を読み込んだ時点で、不完全な検査の対応や余分なキーを検出する。"""
    if not isinstance(drift, dict) or drift.keys() - {"rule_field", "rules_by_value", "excluded_terms"}:
        raise ValueError("invalid drift configuration")
    if not _labels(drift.get("excluded_terms", [])):
        raise ValueError("invalid drift excluded_terms")
    if "rule_field" in drift or "rules_by_value" in drift:
        groups = drift.get("rules_by_value")
        if not _label(drift.get("rule_field")) or not isinstance(groups, dict):
            raise ValueError("drift rule_field and rules_by_value must be specified together")
        for value, rules in groups.items():
            if not _label(value) or not rules or not _labels(rules):
                raise ValueError("invalid drift rule group")


def applicable_rules(mapping, props) -> frozenset[str] | None:
    """指定列の値に対応する検査を返す。対応がなければ全検査を使う。"""
    drift = mapping.get("drift", {})
    value = props.get(drift.get("rule_field"))
    if isinstance(value, str):
        rules = drift.get("rules_by_value", {}).get(value)
        if rules is not None:
            return frozenset(rules)
    return None


def excluded_resource(mapping, account, resource_id, aliases) -> bool:
    if resource_id == account:
        return True
    terms = mapping.get("drift", {}).get("excluded_terms", ())
    return resource_id in terms or any(alias in terms for alias in aliases)
