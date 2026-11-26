"""観測の読み取り API の公開範囲と応答上限。"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.server import MAX_RESPONSE_BYTES, handle_request


class TrackerDriftApiTests(unittest.TestCase):
    path = "/api/campaigns/synthetic/drift"

    def request(self, method, path):
        status, headers, body = handle_request(SimpleNamespace(store=None), method, path)
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertLessEqual(len(body), MAX_RESPONSE_BYTES)
        return status, json.loads(body)

    def test_blue_hides_existence_and_never_loads_report(self):
        with patch("ai_yori_murasaki.drift_report.read_drift") as reader:
            for cid in ("synthetic", "another-id"):
                endpoint = f"/api/campaigns/{cid}/drift?view=blue"
                self.assertEqual(self.request("GET", endpoint), (200, {"status": "withheld"}))
            reader.assert_not_called()

    def test_mutation_and_invalid_view_rejected_before_loading(self):
        with patch("ai_yori_murasaki.drift_report.read_drift") as reader:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertEqual(self.request(method, self.path), (405, {"error": "method_not_allowed"}))
            self.assertEqual(self.request("GET", self.path + "?view=invalid"), (400, {"error": "bad_view"}))
            reader.assert_not_called()

    def test_returns_report_without_a_second_envelope(self):
        report = {"campaign_id": "synthetic", "observations": [], "counts": {}, "items": []}
        with patch("ai_yori_murasaki.drift_report.read_drift", return_value=report):
            self.assertEqual(self.request("GET", self.path), (200, report))

    def test_unavailable_and_missing_do_not_expose_private_error_details(self):
        for reason, expected in (("hold", (200, {"status": "hold"})), ("not_found", (404, {"error": "not_found"}))):
            with self.subTest(reason=reason), patch(
                "ai_yori_murasaki.drift_report.read_drift", side_effect=DataError("private details", reason)
            ):
                self.assertEqual(self.request("GET", self.path), expected)

    def test_oversized_report_is_never_truncated_into_apparent_success(self):
        value = {"items": [{"title": "あ" * (MAX_RESPONSE_BYTES // 3 + 1)}]}
        with patch("ai_yori_murasaki.drift_report.read_drift", return_value=value):
            self.assertEqual(self.request("GET", self.path), (500, {"error": "response_too_large"}))
