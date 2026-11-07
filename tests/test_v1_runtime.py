"""Runtime lifecycle, idempotency, concurrency, guards, crash tests."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


class RuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "ws"
        self.tr = root / "tr"
        self.ws.mkdir()
        self.tr.mkdir()
        self.store = Store(self.ws, self.tr)
        self.rt = Runtime(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _scope(self) -> dict:
        return {"targets": [{"kind": "fixture", "identifier": "t1"}],
                "max_targets": 1}

    def _auth(self, scope: dict) -> dict:
        from ai_yori_murasaki.jsonio import digest
        return {"operator_id": "op1",
                "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope_digest": digest(scope)}

    def _campaign(self) -> dict:
        scope = self._scope()
        return self.rt.create_campaign("INT-TEST-1", scope, self._auth(scope),
                                       "k1")

    def test_create_campaign_lifecycle(self) -> None:
        c = self._campaign()
        self.assertEqual(c["status"], "created")
        runs = self.rt.list_runs(c["campaign_id"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["phase"], "verify")
        self.assertEqual(runs[0]["status"], "completed")
        self.assertTrue(self.store._is_sealed(runs[0]["run_id"]))

    def test_idempotency_same_key(self) -> None:
        c1 = self._campaign()
        c2 = self._campaign()
        self.assertEqual(c1["campaign_id"], c2["campaign_id"])

    def test_idempotency_changed_payload_rejected(self) -> None:
        self._campaign()
        scope = self._scope()
        with self.assertRaises(DataError):
            self.rt.create_campaign("INT-TEST-2", scope, self._auth(scope), "k1")

    def test_idempotency_restart_reuses(self) -> None:
        c1 = self._campaign()
        rt2 = Runtime(Store(self.ws, self.tr))
        c2 = rt2.create_campaign("INT-TEST-1", self._scope(),
                                 self._auth(self._scope()), "k1")
        self.assertEqual(c1["campaign_id"], c2["campaign_id"])

    def test_idempotency_concurrent_one_result(self) -> None:
        results: list = []
        errors: list = []

        def worker() -> None:
            try:
                scope = self._scope()
                results.append(self.rt.create_campaign(
                    "INT-TEST-1", scope, self._auth(scope), "k1"))
            except DataError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)
        ids = {r["campaign_id"] for r in results}
        self.assertEqual(len(ids), 1)

    def test_missing_campaign_rejected(self) -> None:
        with self.assertRaises(DataError):
            self.rt.start_run("cdoesnotexist", "red", 1, "r1")

    def test_phase_bypass_rejected(self) -> None:
        c = self._campaign()
        cid = c["campaign_id"]
        with self.assertRaises(DataError):
            self.rt.start_run(cid, "blue_blind", 1, "b1")
        self.rt.transition(cid, "running")
        with self.assertRaises(DataError):
            self.rt.start_run(cid, "purple", 1, "p1")

    def test_concurrent_running_rejected(self) -> None:
        c = self._campaign()
        self.rt.transition(c["campaign_id"], "running")
        self.rt.start_run(c["campaign_id"], "red", 1, "r1")
        with self.assertRaises(DataError):
            self.rt.start_run(c["campaign_id"], "red", 2, "r2")

    def test_concurrent_threads_one_wins(self) -> None:
        c = self._campaign()
        self.rt.transition(c["campaign_id"], "running")
        results: list = []
        errors: list = []

        def worker(i: int) -> None:
            try:
                results.append(self.rt.start_run(c["campaign_id"], "red", i,
                                                 f"t{i}"))
            except DataError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 3)

    def test_blind_unlock_requires_seal(self) -> None:
        c = self._campaign()
        cid = c["campaign_id"]
        self.rt.transition(cid, "running")
        red = self.rt.start_run(cid, "red", 1, "r1")
        self.rt.finish_run(red["run_id"], "completed")
        self.rt.seal_run(cid, red["run_id"])
        blue = self.rt.start_run(cid, "blue_blind", 1, "b1")
        self.rt.finish_run(blue["run_id"], "completed")
        self.rt.transition(cid, "blind_completed")
        with self.assertRaises(DataError):
            self.rt.transition(cid, "purple_open")

    def test_phase_order_enforced(self) -> None:
        c = self._campaign()
        with self.assertRaises(DataError):
            self.rt.transition(c["campaign_id"], "purple_open")

    def test_forged_actor_rejected(self) -> None:
        c = self._campaign()
        self.rt.transition(c["campaign_id"], "running")
        run = self.rt.start_run(c["campaign_id"], "red", 1, "r1")
        with self.assertRaises(DataError):
            self.store.append_event(run["run_id"], {
                "schema_version": "1.0.0", "campaign_id": c["campaign_id"],
                "run_id": run["run_id"], "actor": {"role": "operator",
                "agent_id": "x", "model": None}, "source": "orchestrator",
                "event_type": "scope_validated",
                "timestamp": "2026-01-01T00:00:00Z",
                "target": {"kind": "fixture", "identifier": "t"},
                "finding": None, "evidence_refs": [],
                "sequence": 1, "previous_event_hash": "0" * 64,
                "event_hash": "0" * 64})

    def test_crash_resume_new_id(self) -> None:
        c = self._campaign()
        self.rt.transition(c["campaign_id"], "running")
        run = self.rt.start_run(c["campaign_id"], "red", 1, "r1")
        new_run = self.rt.resume_after_crash(run["run_id"])
        self.assertNotEqual(new_run["run_id"], run["run_id"])
        old = self.rt.get_run(run["run_id"])
        self.assertEqual(old["status"], "incomplete")
        self.assertEqual(old["cause"], "crash")

    def test_crash_resume_idempotent(self) -> None:
        c = self._campaign()
        self.rt.transition(c["campaign_id"], "running")
        run = self.rt.start_run(c["campaign_id"], "red", 1, "r1")
        first = self.rt.resume_after_crash(run["run_id"])
        # Resume is idempotent: a second call returns the same new run and
        # does not create an extra run.
        second = self.rt.resume_after_crash(run["run_id"])
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertNotEqual(first["run_id"], run["run_id"])
        # Count only red-phase runs; the initial verify bootstrap and
        # transition control runs are not part of this assertion.
        red_runs = [r for r in self.rt.list_runs(c["campaign_id"])
                    if r["phase"] == "red"]
        self.assertEqual(len(red_runs), 2)

    def test_resume_terminal_completed_sealed_error(self) -> None:
        c = self._campaign()
        self.rt.transition(c["campaign_id"], "running")
        run = self.rt.start_run(c["campaign_id"], "red", 1, "r1")
        self.rt.finish_run(run["run_id"], "completed")
        self.rt.seal_run(c["campaign_id"], run["run_id"])
        with self.assertRaises(DataError):
            self.rt.resume_after_crash(run["run_id"])

    def test_seal_unfinished_rejected(self) -> None:
        c = self._campaign()
        self.rt.transition(c["campaign_id"], "running")
        run = self.rt.start_run(c["campaign_id"], "red", 1, "r1")
        with self.assertRaises(DataError):
            self.rt.seal_run(c["campaign_id"], run["run_id"])

    def test_finish_run_terminal(self) -> None:
        c = self._campaign()
        self.rt.transition(c["campaign_id"], "running")
        run = self.rt.start_run(c["campaign_id"], "red", 1, "r1")
        done = self.rt.finish_run(run["run_id"], "completed")
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["cause"], "none")
        self.assertIsNotNone(done["ended_at"])

    def test_sealed_append_forbidden(self) -> None:
        c = self._campaign()
        runs = self.rt.list_runs(c["campaign_id"])
        with self.assertRaises(DataError):
            self.rt.record_event(runs[0]["run_id"], "scope_validated",
                                 "orchestrator")

    def test_snapshot_rebuild(self) -> None:
        c = self._campaign()
        cid = c["campaign_id"]
        self.rt.transition(cid, "running")
        self.store.rebuild_catalog()
        got = self.rt.get_campaign(cid)
        self.assertEqual(got["status"], "running")

    def test_selfclaim_guard_denied(self) -> None:
        c = self._campaign()
        cid = c["campaign_id"]
        self.rt.transition(cid, "running")
        with self.assertRaises(DataError):
            self.rt.transition(cid, "blind_completed",
                               guard_ctx={"blind_completed": True})

    def test_transition_idempotent(self) -> None:
        c = self._campaign()
        cid = c["campaign_id"]
        self.rt.transition(cid, "running")
        self.rt.transition(cid, "running")
        self.assertEqual(self.rt.get_campaign(cid)["status"], "running")

    def test_blue_gating_positive_flow(self) -> None:
        from ai_yori_murasaki.oracle import Oracle
        c = self._campaign()
        cid = c["campaign_id"]
        self.rt.transition(cid, "running")
        red = self.rt.start_run(cid, "red", 1, "r1")
        self.rt.finish_run(red["run_id"], "completed")
        self.rt.seal_run(cid, red["run_id"])
        blue = self.rt.start_run(cid, "blue_blind", 1, "b1")
        self.rt.finish_run(blue["run_id"], "completed")
        self.rt.transition(cid, "blind_completed")
        # Purple cannot open before the Blue run is sealed.
        with self.assertRaises(DataError):
            self.rt.transition(cid, "purple_open")
        self.rt.seal_run(cid, blue["run_id"])
        # Purple cannot open before evaluation is recorded.
        with self.assertRaises(DataError):
            self.rt.transition(cid, "purple_open")
        verify = self.rt.start_run(cid, "verify", 1, "v1")
        ref = self.store.put_object({"suite_id": "blue-detection",
                                     "passed": True})
        pred = Oracle().make_predicate("blue-detection", "test_pass", ref,
                                       {"suite_id": "blue-detection",
                                        "passed": True})
        self.rt.record_oracle(verify["run_id"], pred,
                              evaluated_run_id=blue["run_id"])
        self.rt.finish_run(verify["run_id"], "completed")
        self.rt.seal_run(cid, verify["run_id"])
        self.rt.transition(cid, "purple_open")
        self.assertEqual(self.rt.get_campaign(cid)["status"], "purple_open")

    def test_authorization_storage_redaction(self) -> None:
        c = self._campaign()
        cid = c["campaign_id"]
        # Persist a record containing a secret header, then read it back
        # through the real store and confirm the secret is not exposed.
        ref = self.store.put_object(
            {"headers": {"Authorization": "Bearer test-secret"}})
        obj = self.store.get_object(ref)
        self.assertIsInstance(obj, dict)
        serialized = str(obj)
        self.assertNotIn("test-secret", serialized)
        # The campaign's authorization must match the scope digest used at
        # creation (real _auth(scope) contract).
        campaign = self.rt.get_campaign(cid)
        auth = campaign.get("authorization")
        self.assertIsInstance(auth, dict)
        from ai_yori_murasaki.jsonio import digest
        self.assertEqual(auth.get("scope_digest"),
                         digest(campaign.get("scope")))


if __name__ == "__main__":
    unittest.main()
