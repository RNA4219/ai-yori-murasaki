"""検査の対応と除外語を、利用者が指定した列・値で照合する。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from ai_yori_murasaki.decision_view import load_mapping
from ai_yori_murasaki.drift_policy import applicable_rules, excluded_resource
from ai_yori_murasaki.jsonio import DataError


class TrackerPolicyTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.store = SimpleNamespace(workspace=Path(workspace.name))
        self.config = self.store.workspace / "decision-view" / "mapping.json"
        self.config.parent.mkdir()

    def mapping(self, drift):
        data = {"fields": {}, "status_values": {}, "drift": drift}
        self.config.write_text(json.dumps(data), encoding="utf-8")
        return load_mapping(self.store)

    def test_only_the_selected_column_and_exact_value_choose_rules(self):
        mapping = self.mapping({"rule_field": "分類", "rules_by_value": {"通知": ["Channel", "Delivery"]}})
        self.assertEqual(applicable_rules(mapping, {"分類": "通知"}), {"Channel", "Delivery"})
        for row in ({}, {"題名": "通知"}, {"分類": "通知済"}, {"分類": ["通知"]}):
            with self.subTest(row=row):
                self.assertIsNone(applicable_rules(mapping, row))

    def test_account_and_configured_ids_or_names_are_excluded(self):
        mapping = self.mapping({"excluded_terms": ["Shared"]})
        self.assertTrue(excluded_resource(mapping, "acct", "acct", []))
        self.assertTrue(excluded_resource(mapping, "acct", "Shared", []))
        self.assertTrue(excluded_resource(mapping, "acct", "unit-9", ["old-label", "Shared"]))
        for rid, names in (("shared", []), ("other", ["SharedUnit"]), ("dev", []), ("prod", [])):
            with self.subTest(rid=rid):
                self.assertFalse(excluded_resource(mapping, "acct", rid, names))

    def test_legacy_mapping_has_no_optional_filters(self):
        mapping = load_mapping(self.store)
        self.assertIsNone(applicable_rules(mapping, {"分類": "通知"}))
        self.assertFalse(excluded_resource(mapping, "acct", "dev", []))
        self.assertTrue(excluded_resource(mapping, "acct", "acct", ["service-a"]))

    def test_invalid_explicit_settings_hold_the_report(self):
        invalid = [
            None, [], {"rule_fields": ["分類"]}, {"rule_field": "分類"}, {"rules_by_value": {}},
            {"rule_field": " ", "rules_by_value": {}},
            {"rule_field": "分類", "rules_by_value": []},
            {"rule_field": "分類", "rules_by_value": {"通知": []}},
            {"rule_field": "分類", "rules_by_value": {" ": ["Channel"]}},
            {"rule_field": "分類", "rules_by_value": {"通知": "Channel"}},
            {"rule_field": "分類", "rules_by_value": {"通知": [" "]}},
            {"excluded_terms": "Shared"}, {"excluded_terms": [42]},
        ]
        for drift in invalid:
            with self.subTest(drift=drift), self.assertRaises(DataError) as raised:
                self.mapping(drift)
            self.assertEqual(raised.exception.reason_code, "hold")
