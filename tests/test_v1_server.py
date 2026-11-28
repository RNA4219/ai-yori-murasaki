"""HTTP server contract tests using an ephemeral Store + Runtime."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import create_server, handle_request, project_event
from ai_yori_murasaki.storage import Store


class _ServerFixture:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.workspace = base / "ws"
        self.trusted = base / "tr"
        self.workspace.mkdir()
        self.trusted.mkdir()
        self.store = Store(self.workspace, self.trusted)
        self.runtime = Runtime(self.store)
        self.server = create_server(self.runtime, "127.0.0.1:0")
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str, headers: dict | None = None) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(self.url(path), method="GET")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()


class ServerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _ServerFixture()

    def tearDown(self) -> None:
        self.fx.close()

    def test_health_ok(self) -> None:
        status, headers, body = self.fx.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("frame-ancestors 'none'", headers.get("Content-Security-Policy", ""))
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_index_html_packaged(self) -> None:
        status, headers, body = self.fx.get("/")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(body), 256 * 1024)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        # The packaged page is the product monitor; assert its actual title
        # and that the CSP carries a script hash rather than inventing a
        # title or hash.  Use booleans to avoid dumping the whole HTML.
        text = body.decode("utf-8", errors="replace")
        self.assertTrue("紫 - 製品実行モニター" in text)
        csp = headers.get("Content-Security-Policy", "")
        self.assertTrue("script-src" in csp)
        self.assertTrue("sha256-" in csp)

    def test_post_405(self) -> None:
        req = urllib.request.Request(self.fx.url("/api/health"), method="POST", data=b"{}")
        try:
            urllib.request.urlopen(req, timeout=5)
            self.fail("expected 405")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 405)

    def test_bad_host_400(self) -> None:
        status, _, _ = self.fx.get("/api/health", headers={"Host": "evil.example:80"})
        self.assertEqual(status, 400)

    def test_bad_origin_403(self) -> None:
        status, _, _ = self.fx.get("/api/health", headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_unknown_campaign_404(self) -> None:
        status, _, _ = self.fx.get("/api/campaigns/cdeadbeefdeadbeef")
        self.assertEqual(status, 404)

    def test_unknown_run_404(self) -> None:
        status, _, _ = self.fx.get("/api/runs/rdeadbeefdeadbeef")
        self.assertEqual(status, 404)

    def test_traversal_rejected(self) -> None:
        status, _, _ = self.fx.get("/api/campaigns/..%2f..%2fetc")
        self.assertIn(status, (400, 404))

    def test_duplicate_query_rejected(self) -> None:
        status, _, _ = self.fx.get("/api/runs/rdeadbeefdeadbeef/events?limit=1&limit=2")
        self.assertEqual(status, 400)

    def test_limit_out_of_range(self) -> None:
        status, _, _ = self.fx.get("/api/runs/rdeadbeefdeadbeef/events?limit=99999")
        self.assertEqual(status, 400)

    def test_models_absent_returns_empty(self) -> None:
        status, _, body = self.fx.get("/api/models")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"models": []})

    def test_workspace_json_boundary_and_unavailable_inputs(self) -> None:
        path = self.fx.workspace / "model-status.json"
        row = b'{"models":[{"role":"preview"}]}'
        limit = 1024
        with patch("ai_yori_murasaki.server.MAX_RESPONSE_BYTES", limit):
            path.write_bytes(row.ljust(limit))
            status, _, body = self.fx.get("/api/models")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["models"][0]["role"], "preview")
            for data in (row.ljust(limit + 1), b"null", b"unfinished"):
                path.write_bytes(data)
                status, _, body = self.fx.get("/api/models")
                self.assertEqual((status, json.loads(body)), (200, {"models": []}))
            path.unlink()
            path.mkdir()
            status, _, body = self.fx.get("/api/models")
            self.assertEqual((status, json.loads(body)), (200, {"models": []}))

    def test_adapters_absent_returns_empty(self) -> None:
        status, _, body = self.fx.get("/api/adapters")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"adapters": []})

    def test_status_projections_use_current_redactions_before_truncating(self) -> None:
        marker = "fixture-private-label"
        prefix = "r" * 248
        (self.fx.workspace / "model-status.json").write_text(json.dumps({"models": [{
            "role": "planner", "registration": {"evidence_ref": prefix + marker},
            "development_call": {"evidence_refs": ["sample/" + marker, "public-ref"]},
        }]}), encoding="utf-8")
        (self.fx.workspace / "adapter-status.json").write_text(json.dumps({"adapters": [{
            "name": "sample/" + marker, "status": "not_tested",
        }]}), encoding="utf-8")
        self.fx.store.secrets = (marker,)
        for view in ("blue", "operator"):
            with self.subTest(view=view):
                status, _, body = self.fx.get("/api/models?view=" + view)
                self.assertEqual(status, 200)
                model = json.loads(body)["models"][0]
                self.assertEqual(model["role"], "planner")
                self.assertEqual(model["registration"]["evidence_ref"], (prefix + "[REDACTED]")[:256])
                self.assertEqual(model["development_call"]["evidence_refs"], ["sample/[REDACTED]", "public-ref"])
                status, _, body = self.fx.get("/api/adapters?view=" + view)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["adapters"][0]["name"], "sample/[REDACTED]")

    def test_adapters_never_live(self) -> None:
        (self.fx.workspace / "adapter-status.json").write_text(
            json.dumps({"adapters": [{"name": "x", "status": "live"}]}),
            encoding="utf-8",
        )
        status, _, body = self.fx.get("/api/adapters")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["adapters"][0]["status"], "not_tested")

    def test_models_absent_status_unknown(self) -> None:
        (self.fx.workspace / "model-status.json").write_text(
            json.dumps({"models": [{"role": "red"}]}), encoding="utf-8"
        )
        status, _, body = self.fx.get("/api/models")
        self.assertEqual(status, 200)
        m = json.loads(body)["models"][0]
        self.assertEqual(m["registration"]["status"], "unknown")
        self.assertEqual(m["pin"]["status"], "unknown")

    def test_blue_view_redacts(self) -> None:
        event = {
            "event_id": "e1", "sequence": 1, "timestamp": "t",
            "phase": "red", "role": "worker-red", "event_type": "finding",
            "source": "s", "finding": {"title": "secret", "severity": "high"},
            "evidence_refs": ["a", "b"],
            "extensions": {"oracle": "canary"},
        }
        blue = project_event(event, "blue")
        self.assertEqual(blue["title"], "[redacted]")
        self.assertEqual(blue["evidence_count"], 0)
        self.assertNotIn("extensions", blue)
        self.assertNotIn("evidence_refs", blue)
        op = project_event(event, "operator")
        self.assertEqual(op["title"], "secret")
        self.assertEqual(op["evidence_count"], 2)
        self.assertNotIn("extensions", op)

    def test_handle_request_helper(self) -> None:
        status, headers, body = handle_request(
            self.fx.runtime, "GET", "/api/health",
            headers={"Host": f"127.0.0.1:{self.fx.port}"},
            port=self.fx.port,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})

    def test_handle_request_bad_origin(self) -> None:
        status, _, _ = handle_request(
            self.fx.runtime, "GET", "/api/health",
            headers={"Host": f"127.0.0.1:{self.fx.port}", "Origin": "http://evil"},
            port=self.fx.port,
        )
        self.assertEqual(status, 403)

    def test_bind_not_loopback(self) -> None:
        from ai_yori_murasaki.jsonio import DataError
        with self.assertRaises(DataError) as ctx:
            create_server(self.fx.runtime, "0.0.0.0:0")
        self.assertEqual(ctx.exception.reason_code, "bind_not_loopback")

    def _scope(self) -> dict:
        return {"targets": [{"kind": "fixture", "identifier": "t1"}],
                "max_targets": 1}

    def _auth(self, scope: dict) -> dict:
        from ai_yori_murasaki.jsonio import digest
        return {"operator_id": "op1",
                "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope_digest": digest(scope)}

    def _create_campaign(self, intent_id: str, idem: str) -> dict:
        scope = self._scope()
        return self.fx.runtime.create_campaign(
            intent_id, scope, self._auth(scope), idem)

    def test_agent_event_projection_and_agent_rsi(self) -> None:
        campaign = self._create_campaign("INT-4", "idem-4")
        cid = campaign["campaign_id"]
        self.fx.runtime.transition(cid, "running")
        run = self.fx.runtime.start_run(cid, "red", 1, "idem-run-4")
        rid = run["run_id"]
        self.fx.runtime.record_event(
            rid, "state_captured", "llm",
            extensions={"record_type": "agent_turn", "turn": 1,
                        "model": "test-model", "tool": "probe_endpoint"},
        )
        status, _, body = self.fx.get(f"/api/runs/{rid}/events?limit=10")
        self.assertEqual(status, 200)
        events = json.loads(body)["events"]
        agent_events = [e for e in events if isinstance(e.get("agent"), dict)]
        self.assertEqual(len(agent_events), 1)
        agent = agent_events[0]["agent"]
        self.assertEqual(agent["record_type"], "agent_turn")
        self.assertEqual(agent["model"], "test-model")
        self.assertEqual(agent["tool"], "probe_endpoint")
        self.assertEqual(agent["turn"], 1)
        # agent RSI state file projects through the campaign RSI endpoint
        import json as _json
        state_dir = self.fx.workspace / "agent_rsi"
        state_dir.mkdir(parents=True, exist_ok=True)
        candidate = {
            "candidate_id": "agc-test01",
            "freeze_id": "ag-test01",
            "campaign_id": cid,
            "bundle_digest": "b" * 64,
            "baseline_digest": "a" * 64,
            "rules_applied": ["blue_missed_detection"],
            "compared": True,
            "applied": False,
            "summary": {"pairs": 2, "gain": 0.5, "ready": False,
                        "exploration_grade": True, "model_pinned": False},
        }
        (state_dir / "state.json").write_text(
            _json.dumps({"candidates": {"agc-test01": candidate},
                         "freezes": {}, "reviews": {}, "memory": []}),
            encoding="utf-8")
        status, _, body = self.fx.get(f"/api/campaigns/{cid}/rsi")
        self.assertEqual(status, 200)
        doc = json.loads(body)
        self.assertIn("rsi", doc)
        agent_rsi = doc["agent_rsi"]
        self.assertEqual(len(agent_rsi), 1)
        self.assertEqual(agent_rsi[0]["candidate_id"], "agc-test01")
        self.assertEqual(agent_rsi[0]["summary"]["gain"], 0.5)
        self.assertTrue(agent_rsi[0]["summary"]["exploration_grade"])
        self.assertFalse(agent_rsi[0]["summary"]["ready"])

    def test_campaign_flow(self) -> None:
        campaign = self._create_campaign("INT-1", "idem-1")
        cid = campaign["campaign_id"]
        status, _, body = self.fx.get("/api/campaigns")
        self.assertEqual(status, 200)
        campaigns = json.loads(body)["campaigns"]
        self.assertTrue(any(c["campaign_id"] == cid for c in campaigns))
        status, _, body = self.fx.get(f"/api/campaigns/{cid}")
        self.assertEqual(status, 200)
        detail = json.loads(body)
        self.assertEqual(detail["campaign"]["campaign_id"], cid)
        self.assertIn("findings", detail)
        self.assertIn("remediations", detail)
        self.assertIsNone(detail["metrics"])

    def test_existing_monitor_records_use_current_redactions_in_both_views(self) -> None:
        from ai_yori_murasaki.monitor_index import build_index

        marker = "FIXTURE-PRIVATE"
        cid = self._create_campaign("INT-" + marker, "display-case")["campaign_id"]
        self.fx.runtime.transition(cid, "running")
        rid = self.fx.runtime.start_run(cid, "red", 1, "display-run")["run_id"]
        prefix = "x" * 125
        self.fx.runtime.record_event(
            rid, "state_captured", "llm",
            extensions={"record_type": "agent_stopped", "model": "sample/" + marker,
                        "reason": prefix + marker})
        self.fx.store.append_verification({"verify_id": rid, "run_id": rid, "result": "HOLD",
                                           "reason_code": "sample/" + marker})
        state = self.fx.workspace / "agent_rsi"
        state.mkdir()
        (state / "state.json").write_text(json.dumps({"candidates": {"example": {
            "candidate_id": "example-" + marker, "campaign_id": cid, "freeze_id": "snapshot-" + marker,
            "bundle_digest": "digest-" + marker, "baseline_digest": 0, "rules_applied": ["sample/" + marker],
        }}}), encoding="utf-8")
        self.fx.store.secrets = (marker,)
        for view in ("blue", "operator"):
            with self.subTest(view=view):
                query = "?view=" + view
                status, _, body = self.fx.get(f"/api/runs/{rid}/events" + query)
                self.assertEqual(status, 200)
                agent = next(e["agent"] for e in json.loads(body)["events"] if "agent" in e)
                self.assertEqual(agent["model"], "sample/[REDACTED]")
                if view == "blue":
                    self.assertNotIn("reason", agent)
                else:
                    self.assertEqual(agent["reason"], (prefix + "[REDACTED]")[:128])
                status, _, body = self.fx.get(f"/api/campaigns/{cid}/rsi" + query)
                self.assertEqual(status, 200)
                row = json.loads(body)["agent_rsi"][0]
                self.assertEqual(row["rules_applied"], ["sample/[REDACTED]"])
                self.assertEqual(row["candidate_id"], "example-[REDACTED]")
                self.assertEqual(row["freeze_id"], "snapshot-[REDACTED]")
                self.assertEqual(row["bundle_digest"], "digest-[REDACTED]")
                self.assertIsNone(row["baseline_digest"])
                for suffix in ("", "&limit=2"):
                    status, _, body = self.fx.get(f"/api/verification/{rid}" + query + suffix)
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)["verification"][0]["reason_code"], "sample/[REDACTED]")
                status, _, body = self.fx.get("/api/campaigns" + query)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["campaigns"][0]["intent_id"], "INT-[REDACTED]")
        self.fx.store.secrets = ()
        build_index(self.fx.store)
        self.fx.store.secrets = (marker,)
        for view in ("blue", "operator"):
            status, _, body = self.fx.get("/api/campaigns?page=1&view=" + view)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["campaigns"][0]["intent_id"], "INT-[REDACTED]")

    def test_events_pagination(self) -> None:
        campaign = self._create_campaign("INT-2", "idem-2")
        cid = campaign["campaign_id"]
        self.fx.runtime.transition(cid, "running")
        run = self.fx.runtime.start_run(cid, "red", 1, "idem-run-2")
        rid = run["run_id"]
        for i in range(3):
            self.fx.runtime.record_event(rid, "state_captured", "orchestrator")
        status, _, body = self.fx.get(f"/api/runs/{rid}/events?limit=2")
        self.assertEqual(status, 200)
        events = json.loads(body)["events"]
        self.assertEqual(len(events), 2)
        last_seq = events[-1]["sequence"]
        status, _, body = self.fx.get(f"/api/runs/{rid}/events?after={last_seq}&limit=10")
        self.assertEqual(status, 200)
        events2 = json.loads(body)["events"]
        self.assertTrue(all(e["sequence"] > last_seq for e in events2))

    def test_event_history_uses_one_verified_run_snapshot(self) -> None:
        cid = self._create_campaign("INT-HISTORY", "event-history")["campaign_id"]
        rid = self.fx.runtime.start_run(cid, "verify", 1, "history-check")["run_id"]
        path = f"/api/runs/{rid}/events?limit=1"
        status, _, body = self.fx.get(path)
        self.assertEqual((status, json.loads(body)), (200, {"events": []}))
        for _ in range(2):
            self.fx.runtime.record_event(rid, "state_captured", "orchestrator")
        status, _, body = self.fx.get(path)
        self.assertEqual(status, 200)
        self.assertEqual([event["sequence"] for event in json.loads(body)["events"]], [1])
        self.fx.runtime.finish_run(rid, "completed")
        self.fx.runtime.seal_run(cid, rid)
        ledger = self.fx.store._ledger_path(rid)
        rows = ledger.read_bytes().splitlines(keepends=True)
        ledger.write_bytes(b"".join(rows[:-1]))
        status, _, body = self.fx.get(path)
        self.assertEqual((status, json.loads(body)), (409, {"error": "evidence_unverified"}))
        # An incomplete restore left the last row belonging to another run.
        other = self.fx.runtime.start_run(cid, "verify", 2, "other-history")["run_id"]
        self.fx.runtime.record_event(other, "state_captured", "orchestrator")
        ledger.write_bytes(rows[0] + self.fx.store._ledger_path(other).read_bytes() + rows[-1])
        status, _, body = self.fx.get(path)
        self.assertEqual((status, json.loads(body)), (409, {"error": "evidence_unverified"}))

    def test_blue_view_no_canary(self) -> None:
        campaign = self._create_campaign("INT-3", "idem-3")
        cid = campaign["campaign_id"]
        self.fx.runtime.transition(cid, "running")
        run = self.fx.runtime.start_run(cid, "red", 1, "idem-run-3")
        rid = run["run_id"]
        self.fx.runtime.record_event(
            rid, "attack_executed", "broker",
            finding={"finding_id": "canary-finding", "title": "CANARY_SECRET", "severity": "high"},
            evidence_refs=(self.fx.runtime.store.put_object({"canary": "CANARY_SECRET"}),),
            extensions={"oracle": "CANARY_ORACLE"},
        )
        status, _, body = self.fx.get(f"/api/runs/{rid}/events?view=blue")
        self.assertEqual(status, 200)
        self.assertNotIn(b"CANARY_SECRET", body)
        self.assertNotIn(b"CANARY_ORACLE", body)
        self.assertNotIn(b"ref1", body)
        events = json.loads(body)["events"]
        for e in events:
            self.assertNotIn("extensions", e)
            self.assertNotIn("evidence_refs", e)

    def test_verification_journal(self) -> None:
        campaign = self._create_campaign("INT-4", "idem-4")
        cid = campaign["campaign_id"]
        self.fx.runtime.transition(cid, "running")
        run = self.fx.runtime.start_run(cid, "red", 1, "idem-run-4")
        rid = run["run_id"]
        vdir = self.fx.workspace / "verification"
        vdir.mkdir(exist_ok=True)
        (vdir / f"{rid}.jsonl").write_text(
            json.dumps({"verify_id": "v1", "run_id": rid, "result": "PASS", "reason_code": "ok"}) + "\n"
            + "not json\n"
            + json.dumps({"verify_id": "v2", "run_id": rid, "result": "FAIL", "reason_code": "verdict_fail"}) + "\n",
            encoding="utf-8",
        )
        status, _, body = self.fx.get(f"/api/verification/{rid}")
        self.assertEqual(status, 200)
        records = json.loads(body)["verification"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["result"], "PASS")
        self.assertEqual(json.loads(body)["omitted"], 1)

    def test_page_large_verification_history_while_appending(self) -> None:
        campaign = self._create_campaign("INT-HISTORY", "history")
        cid = campaign["campaign_id"]
        self.fx.runtime.transition(cid, "running")
        rid = self.fx.runtime.start_run(cid, "red", 1, "history-run")["run_id"]
        route = f"/api/verification/{rid}"
        directory = self.fx.workspace / "verification"
        directory.mkdir(exist_ok=True)
        journal = directory / f"{rid}.jsonl"
        self.assertEqual(json.loads(self.fx.get(route + "?limit=25")[2])["journal_status"], "missing")
        journal.touch()
        self.assertEqual(json.loads(self.fx.get(route + "?limit=25")[2])["journal_status"], "empty")
        legacy = {"run_id": rid, "verify_id": rid, "result": "PASS", "reason_code": "ok"}
        current = dict(legacy, method="checkpoint", target_last_sequence=10,
                       target_last_event_hash="a" * 64, checkpoint_digest="b" * 64)
        with journal.open("wb") as stream:
            for number in range(29):
                record = dict(legacy, verify_id=f"check-{number}", padding="x" * 48_000)
                if number == 0:
                    record["result"] = "FAIL"
                stream.write(json.dumps(record).encode() + b"\n")
            stream.write(b'{"verify_id":"bad","result":"\\ud800"}\n')
            stream.write(json.dumps(current).encode() + b"\n")
        self.assertGreater(journal.stat().st_size, 1024 * 1024)
        code, _, body = self.fx.get(route)
        self.assertEqual((code, json.loads(body)["error"]), (409, "verification_paging_required"))
        status, _, body = self.fx.get(route + "?limit=25")
        self.assertEqual(status, 200)
        page = json.loads(body)
        head = page["verification"][0]
        self.assertEqual(head["checkpoint_digest"], "b" * 64)
        self.assertEqual(head["target_last_event_hash"], "a" * 64)
        self.assertEqual(head["target_last_sequence"], 10)
        self.assertEqual(page["omitted"], 1)
        with journal.open("ab") as stream:
            stream.write(json.dumps(current).encode() + b"\n")
        rows = list(page["verification"])
        while page["next_before"] is not None:
            status, _, body = self.fx.get(route + "?limit=25&before=" + str(page["next_before"]))
            self.assertEqual(status, 200)
            page = json.loads(body)
            rows.extend(page["verification"])
        self.assertEqual(len(rows), 30)
        self.assertEqual(rows[-1]["result"], "FAIL")
        self.assertNotIn("method", rows[-1])
        self.assertEqual([row["verify_id"] for row in rows[1:]], [f"check-{n}" for n in reversed(range(29))])
        status, _, body = self.fx.get(route + "?limit=1&view=blue")
        self.assertEqual(status, 200)
        self.assertEqual(set(json.loads(body)["verification"][0]),
                         {"verify_id", "run_id", "checked_at", "result", "reason_code"})
        for args in ("?limit=0", "?limit=101", "?limit=5&before=1", "?before=5"):
            self.assertEqual(self.fx.get(route + args)[0], 400)
        encoded = json.dumps(legacy).encode()
        journal.write_bytes(encoded + b" " * (1024 * 1024 - len(encoded) - 1) + b"\n")
        status, _, body = self.fx.get(route + "?limit=25")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["verification"][0]["verify_id"], rid)
        journal.write_bytes(b" " * (1024 * 1024 + 1) + b"\n")
        status, _, body = self.fx.get(route + "?limit=25")
        self.assertEqual((status, json.loads(body)["error"]), (409, "verification_record_too_large"))

    def test_response_size_bounded(self) -> None:
        status, headers, body = self.fx.get("/api/campaigns")
        self.assertEqual(status, 200)
        self.assertLessEqual(len(body), 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
