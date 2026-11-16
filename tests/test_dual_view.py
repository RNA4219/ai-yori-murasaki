"""T36/T39/T41: decision-view server, mapping and assessment regression."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ai_yori_murasaki.decision_view import (
    build_view, derive_assessment_status, load_mapping, _unique_findings,
)
from ai_yori_murasaki.jsonio import DataError, digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import handle_request
from ai_yori_murasaki.storage import Store


MAPPING = {"fields":{"status":"状態"},"status_values":{"判断待ち":"waiting","対応済み":"done"},"rules":{}}
NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)


def f(fid, status="判断待ち"):
    return {"finding_id":fid,"title":"sample","severity":"high","location":{"path":"/issues/"+fid},
            "extensions":{"notion":{"properties":{"状態":status}}}}


class DecisionViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        ws, tr = base/"ws", base/"trusted"
        ws.mkdir()
        tr.mkdir()
        self.store = Store(ws, tr)
        self.runtime = Runtime(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self,method,path):
        s,h,b = handle_request(self.runtime,method,path)
        return s,h,json.loads(b) if h.get("Content-Type","").startswith("application/json") else b

    def campaign(self):
        scope = {"targets":[{"kind":"fixture","identifier":"t1"}],"max_targets":1}
        auth = {"operator_id":"op1","granted_at":"2026-01-01T00:00:00Z",
                "expires_at":"2099-01-01T00:00:00Z","scope_digest":digest(scope)}
        return self.runtime.create_campaign("INT-TEST-20261116",scope,auth,"idem-dec")

    def test_serve_both_html_with_csp(self):
        for path in ("/","/decision"):
            status, headers, body = self.request("GET",path)
            self.assertEqual(status,200)
            self.assertLessEqual(len(body),256*1024)
            self.assertIn(b"<html",body)
            self.assertIn("sha256-",headers.get("Content-Security-Policy",""))
            self.assertIn("text/html",headers["Content-Type"])

    def test_blue_missing_and_real_campaign_same_withheld(self):
        cid = self.campaign()["campaign_id"]
        for path in (f"/api/campaigns/{cid}/decision-view?view=blue",
                     "/api/campaigns/nonexistent/decision-view?view=blue"):
            s,_,payload = self.request("GET",path)
            self.assertEqual(s,200)
            self.assertEqual(payload,{"decision_view":{"status":"withheld","reason":"評価情報はBlueに非公開"}})

    def test_method_view_query_and_missing(self):
        for method,path,want in (
            ("POST","/decision",405),
            ("POST","/api/campaigns/nope/decision-view",405),
            ("GET","/api/campaigns/nope/decision-view?view=bad",400),
            ("GET","/api/campaigns/nope/decision-view?view=blue&view=blue",400),
            ("GET","/api/campaigns/nope/decision-view",404)):
            self.assertEqual(self.request(method,path)[0],want)

    def test_no_assessment_and_no_store_mutations(self):
        cid = self.campaign()["campaign_id"]
        before = {str(p.relative_to(self.store.workspace)):p.read_bytes()
                  for p in self.store.workspace.rglob("*") if p.is_file()}
        status,_,payload = self.request("GET",f"/api/campaigns/{cid}/decision-view")
        after = {str(p.relative_to(self.store.workspace)):p.read_bytes()
                 for p in self.store.workspace.rglob("*") if p.is_file()}
        self.assertEqual(status,200)
        self.assertEqual(payload["decision_view"]["items"],[])
        self.assertFalse(payload["decision_view"]["assessment"]["present"])
        self.assertEqual(before,after)

    def test_bad_mapping_is_hold(self):
        cid = self.campaign()["campaign_id"]
        p = self.store.workspace/"decision-view"/"mapping.json"
        p.parent.mkdir()
        p.write_text('{"fields":{},"status_values":{},"fields":{}}',encoding="utf-8")
        self.assertRaises(DataError,load_mapping,self.store)
        status,_,payload=self.request("GET",f"/api/campaigns/{cid}/decision-view")
        self.assertEqual((status,payload), (200,{"decision_view":None,"status":"hold","reason":"記録を確認できません"}))

    def test_untrusted_mapping_symlink_is_hold(self):
        p=self.store.workspace/"decision-view"/"mapping.json";p.parent.mkdir()
        src=self.store.workspace/"other.json";src.write_text(json.dumps(MAPPING))
        try:p.symlink_to(src)
        except (OSError,NotImplementedError):self.skipTest("symlink unsupported")
        self.assertRaises(DataError,load_mapping,self.store)

    def test_finding_id_dedup_before_previous_comparison(self):
        rows=[f("same","判断待ち"),f("same","対応済み"),f("other","対応済み")]
        self.assertEqual(len(_unique_findings(rows)),2)
        v=build_view({"campaign_id":"c","status":"review_pending"},rows,mapping=MAPPING,now=NOW,
                     previous={"campaign":{"campaign_id":"old"},"findings":[f("same")], "records":{}},
                     assessment={})
        self.assertEqual(len(v["items"]),2)
        self.assertEqual(v["items"][0]["state"],"waiting")
        self.assertIsNone(v["items"][0]["assessment_status"])
        self.assertEqual(v["campaign"]["status"],"review_pending")

    def test_eight_assessment_states_match_monitor(self):
        cases=[
            ({"claims":[{"status":"confirmed"}]},"confirmed"),
            ({"claims":[{"status":"hypothesis"}]},"candidate"),
            ({"claims":[{"status":"refuted"}]},"refuted"),
            ({"validation":{"result":"success"}},"retested"),
            ({"remediation":{"proposal_ref":"a"}},"proposed"),
            ({"validation":{"result":"undetermined"}},"undetermined"),
            ({"historical":True},"historical"),
            ({"claims":[]},"unknown"),
        ]
        for finding,want in cases:
            self.assertEqual(derive_assessment_status(finding),want)
        self.assertEqual(derive_assessment_status({"validation":{"result":"success"}},True),"historical")

    def test_report_projection_and_coverage(self):
        report={"status":"ready","historical":True,
                "findings":[{"finding_id":"id1","validation":{"result":"success"}}],
                "coverage":{"categories":[{"category":"authz","status":"executed"},
                                          {"category":"sqli","status":"failed"}]}}
        v=build_view({"campaign_id":"c"},[f("id1")],mapping=MAPPING,now=NOW,assessment=report)
        self.assertEqual(v["items"][0]["assessment_status"],"historical")
        self.assertTrue(v["assessment"]["historical"])
        self.assertGreaterEqual(v["assessment"]["coverage"]["not_executed"],1)

if __name__=="__main__":
    unittest.main()
