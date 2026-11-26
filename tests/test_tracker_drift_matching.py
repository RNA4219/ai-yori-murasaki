"""R071、R074、R075 の照合境界を合成した課題と観測で確認する。"""

import unittest

from ai_yori_murasaki.tracker_drift import build_drift


NOW = "2026-11-26T09:00:00+00:00"
OLD = "2026-11-25T09:00:00+00:00"
MAPPING = {"fields": {"scope": "環境", "status": "状態", "summary": "要約", "updated_at": "編集日"},
           "status_values": {"済": "done", "見送り": "wont_fix", "継続": "open"}}


def issue(fid, title, status="継続", scope="dev", **extra):
    props = {"状態": status, "環境": scope, **extra}
    return {"finding_id": fid, "title": title, "extensions": {"notion": {"properties": props}}}


def observation(kind, rows, *, time=NOW, region="test-east", account="dev"):
    scope = {"provider": "test", "account": account, "regions": [region]}
    return {"schema_version": "1.0.0", "kind": kind, "collected_at": time, "scope": scope,
            "method": "合成一覧", "resources" if kind == "inventory" else "detections": rows}


def asset(rid, name):
    return {"id": rid, "type": "node", "name": name, "state": "running", "private": "SECRET"}


def detection(rid, status):
    return {"id": "check-" + rid, "resource_id": rid, "rule": "example", "status": status,
            "updated_at": OLD, "private": "SECRET"}


def report(findings, observations, records=None):
    return build_drift("synthetic", findings, records or {}, MAPPING, observations)


class TrackerDriftMatchingTests(unittest.TestCase):
    def test_japanese_prose_delimits_ascii_ids_but_longer_identifiers_do_not(self):
        observations = [observation("detections", [detection("node-27", "active")])]
        for title in ("node-27の通知設定", "対象node-27を確認", "テストnode-27用"):
            with self.subTest(title=title):
                item = report([issue("task", title, "済")], observations)["items"][0]
                self.assertEqual(item["classification"], "closed_active")
                self.assertEqual(item["matches"][0]["method"], "id")
        for title in ("node-270の通知", "xnode-27", "node-27_backup", "node-27-other", "énode-27"):
            with self.subTest(title=title):
                item = report([issue("task", title, "済")], observations)["items"][0]
                self.assertEqual(item["reason"], "no_resource")

    def test_five_classes_four_reasons_and_source_evidence(self):
        findings = [
            issue("gone", "確認 (node-gone)"),
            issue("fixed", "課題の要約を参照"),
            issue("reopen", "node-live", "済"),
            issue("aligned", "node-live", "未知"),
            issue("out", "node-live", scope="dev-other"),
            issue("none", "node-live-extra"),
            issue("guess", "service-alpha-prod"),
            issue("unchecked", "node-blank"),
        ]
        observations = [observation("inventory", [asset("node-fixed", "service-alpha"),
                                                   asset("node-live", "service-beta"),
                                                   asset("node-blank", "service-gamma")]),
                        observation("detections", [detection("node-gone", "resolved"),
                                                   detection("node-fixed", "resolved"),
                                                   detection("node-live", "active")])]
        records = {"fixed": {"状態": "継続", "環境": "dev", "要約": "確認 (service-alpha)",
                             "編集日": "2026-11-27T01:00:00+09:00", "private": "SECRET"}}
        result = report(findings, observations, records)
        self.assertEqual(result["counts"], {"open_absent": 1, "open_resolved": 1, "closed_active": 1,
                                            "aligned": 1, "uncomparable": 4})
        self.assertEqual(result["reasons"], dict.fromkeys(
            ("out_of_scope", "no_resource", "inferred_only", "no_detections"), 1))
        items = {item["finding_id"]: item for item in result["items"]}
        self.assertEqual([items[f["finding_id"]]["classification"] for f in findings[:4]],
                         ["open_absent", "open_resolved", "closed_active", "aligned"])
        self.assertEqual([items[f["finding_id"]]["reason"] for f in findings[4:]],
                         ["out_of_scope", "no_resource", "inferred_only", "no_detections"])
        self.assertEqual(items["fixed"]["matches"][0]["method"], "name")
        self.assertEqual(items["fixed"]["matches"][0]["detections"][0]["id"], "check-node-fixed")
        self.assertTrue(items["fixed"]["stale"])
        self.assertFalse(items["aligned"]["closed"])
        self.assertEqual(items["gone"]["matches"][0]["in_inventory"], False)
        self.assertEqual(items["fixed"]["collected_at"], [NOW])
        self.assertNotIn("SECRET", str(result))

    def test_only_current_status_and_historical_identity_for_absent(self):
        old_inventory = observation("inventory", [asset("old-id", "old-name")], time=OLD)
        new_inventory = observation("inventory", [])
        old_signal = observation("detections", [detection("old-id", "active")], time=OLD)
        new_signals = observation("detections", [])
        observations = [new_inventory, old_signal, new_signals, old_inventory]
        results = report([issue("a", "old-name"), issue("b", "old-id", "見送り")], observations)
        self.assertEqual([i["classification"] for i in results["items"]], ["open_absent", "aligned"])
        match = results["items"][0]["matches"][0]
        self.assertEqual(match["method"], "name")
        self.assertEqual(match["detections"], [])
        self.assertIn(3, match["observation_indices"])
        self.assertEqual(results["items"][0]["collected_at"], [OLD, NOW])

    def test_different_scopes_cannot_prove_absence_or_fix(self):
        inventory = observation("inventory", [asset("node-a", "alpha")])
        empty_elsewhere = observation("inventory", [], region="test-west")
        detections = observation("detections", [detection("node-b", "resolved")], region="test-north")
        items = report([issue("a", "node-a"), issue("b", "node-b")],
                       [inventory, empty_elsewhere, detections])["items"]
        self.assertEqual(items[0]["reason"], "no_detections")
        self.assertEqual(items[1]["classification"], "open_resolved")
        self.assertIsNone(items[1]["matches"][0]["in_inventory"])

    def test_duplicate_and_partial_names_do_not_certify_status(self):
        observations = [observation("inventory", [asset("node-a", "shared"), asset("node-b", "shared")]),
                        observation("detections", [detection("node-a", "resolved")])]
        a, b, c = report([issue("a", "shared"), issue("b", "node-a / shared"),
                          issue("c", "node-a node-b")], observations)["items"]
        self.assertEqual(a["reason"], "inferred_only")
        self.assertEqual([m["method"] for m in a["matches"]], ["inferred", "inferred"])
        self.assertEqual(b["classification"], "open_resolved")
        self.assertEqual([m["method"] for m in b["matches"]], ["id", "inferred"])
        self.assertEqual(c["reason"], "no_detections")

    def test_unlinked_observation_does_not_set_stale(self):
        findings = [issue("a", "node-a", 編集日="2026-11-26T10:00:00+02:00")]
        observations = [observation("detections", [detection("node-a", "resolved")]),
                        observation("detections", [detection("node-z", "active")], time=OLD, region="elsewhere")]
        item = report(findings, observations)["items"][0]
        self.assertFalse(item["stale"])
        self.assertEqual(item["collected_at"], [NOW])

    def test_empty_observations_and_non_tracker_findings(self):
        findings = [issue("dup", "対象"), issue("dup", "対象"), {"finding_id": "scanner", "title": "検知"}]
        result = report(findings, [])
        self.assertEqual(result["counts"]["uncomparable"], 1)
        self.assertEqual(result["items"][0]["reason"], "out_of_scope")
        self.assertEqual(result["items"][0]["collected_at"], [])

    def test_incompatible_names_at_same_time_are_only_inferred_in_either_order(self):
        first = observation("inventory", [asset("node-a", "service-first")])
        second = observation("inventory", [asset("node-a", "service-second")])
        signals = observation("detections", [detection("node-a", "resolved")])
        for observations in ([first, second, signals], [second, signals, first]):
            by_name, by_id = report([issue("a", "service-first"), issue("b", "node-a")],
                                    observations)["items"]
            self.assertEqual(by_name["reason"], "inferred_only")
            self.assertEqual(by_id["classification"], "open_resolved")
            self.assertIsNone(by_id["matches"][0]["name"])
