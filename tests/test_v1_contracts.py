"""v1 契約 schema と semantic validator の受入テスト。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from ai_yori_murasaki import contracts
from ai_yori_murasaki.jsonio import ContractError, DataError, canonical, digest, loads

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "v1" / "contracts.json"
NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=timezone.utc)


def _load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _ctx(fixture: dict, objects: dict[str, dict] | None = None) -> dict:
    store = dict(objects or {})

    def get_object(ref: str) -> dict:
        if ref not in store:
            raise KeyError(ref)
        return store[ref]

    return {
        "campaign": fixture["campaign"],
        "runs": fixture["runs"],
        "findings": fixture["findings"],
        "remediations": fixture["remediations"],
        "events": [fixture["evidence_event"]],
        "oracle_evaluation": fixture["oracle_predicate"],
        "get_object": get_object,
        "now": NOW,
    }


def _objects(fixture: dict) -> dict[str, dict]:
    return {ref: obj for ref, obj in fixture["objects"].items()}


class JsonIOTest(unittest.TestCase):
    def test_known_vectors(self) -> None:
        cases = [
            ({"a": 1, "b": 1.0}, b'{"a":1,"b":1.0}'),
            ({"x": -0.0}, b'{"x":-0.0}'),
            ({"s": "\u3042"}, '{"s":"\u3042"}'.encode("utf-8")),
            ({"n": 1e20}, b'{"n":1e+20}'),
            ({"k": None}, b'{"k":null}'),
        ]
        for value, expected in cases:
            self.assertEqual(canonical(value), expected)

    def test_duplicate_key_rejected(self) -> None:
        with self.assertRaises(DataError):
            loads('{"a":1,"a":2}')

    def test_non_finite_rejected(self) -> None:
        for raw in ('{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}'):
            with self.assertRaises(DataError):
                loads(raw)

    def test_depth_limit(self) -> None:
        value: object = None
        for _ in range(33):
            value = [value]
        with self.assertRaises(DataError):
            canonical(value)

    def test_bool_is_not_int(self) -> None:
        with self.assertRaises(DataError):
            canonical({True: 1})

    def test_contract_error_is_data_error(self) -> None:
        err = ContractError("x", "policy_denied")
        self.assertIsInstance(err, DataError)
        self.assertEqual(err.reason_code, "policy_denied")


class SchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _load_fixture()

    def test_all_kinds_validate(self) -> None:
        for kind in contracts.KINDS:
            with self.subTest(kind=kind):
                contracts.validate(kind, self.fixture[kind])

    def test_seal_validates(self) -> None:
        contracts.validate("seal", self.fixture["seal"])

    def test_unknown_kind(self) -> None:
        with self.assertRaises(ContractError):
            contracts.validate("nope", {})

    def test_additional_properties_rejected(self) -> None:
        bad = dict(self.fixture["campaign"])
        bad["unexpected"] = 1
        with self.assertRaises(ContractError):
            contracts.validate("campaign", bad)

    def test_bool_not_integer(self) -> None:
        bad = dict(self.fixture["run"])
        bad["seed"] = True
        with self.assertRaises(ContractError):
            contracts.validate("run", bad)

    def test_timestamp_calendar(self) -> None:
        bad = dict(self.fixture["campaign"])
        bad["created_at"] = "2026-02-30T00:00:00Z"
        with self.assertRaises(ContractError):
            contracts.validate("campaign", bad)

    def test_timestamp_requires_utc(self) -> None:
        bad = dict(self.fixture["campaign"])
        bad["created_at"] = "2026-11-06T12:00:00+09:00"
        with self.assertRaises(ContractError):
            contracts.validate("campaign", bad)


class SemanticTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _load_fixture()
        self.ctx = _ctx(self.fixture, _objects(self.fixture))

    def test_all_semantic_pass(self) -> None:
        for kind in contracts.KINDS:
            with self.subTest(kind=kind):
                contracts.semantic_validate(kind, self.fixture[kind], self.ctx)

    def test_campaign_scope_digest_mismatch(self) -> None:
        bad = json.loads(json.dumps(self.fixture["campaign"]))
        bad["authorization"]["scope_digest"] = "0" * 64
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("campaign", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_campaign_expired(self) -> None:
        bad = json.loads(json.dumps(self.fixture["campaign"]))
        bad["authorization"]["expires_at"] = "2026-11-06T11:00:00Z"
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("campaign", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_remediation_cross_campaign(self) -> None:
        bad = json.loads(json.dumps(self.fixture["remediation"]))
        bad["finding_id"] = "missing-finding"
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("remediation", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_remediation_stale_patch(self) -> None:
        bad = json.loads(json.dumps(self.fixture["remediation"]))
        bad["patch_bundle_ref"] = "1" * 64
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("remediation", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_approval_expired(self) -> None:
        bad = json.loads(json.dumps(self.fixture["approval"]))
        bad["expires_at"] = "2026-11-06T11:00:00Z"
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("approval", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_approval_missing_checks(self) -> None:
        bad = json.loads(json.dumps(self.fixture["approval"]))
        bad["required_checks"] = bad["required_checks"][:1]
        with self.assertRaises(ContractError):
            contracts.semantic_validate("approval", bad, self.ctx)

    def test_evidence_stale_ref(self) -> None:
        bad = json.loads(json.dumps(self.fixture["evidence_event"]))
        bad["evidence_refs"] = ["2" * 64]
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("evidence_event", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_final_verdict_derivation_mismatch(self) -> None:
        bad = json.loads(json.dumps(self.fixture["final_verdict"]))
        bad["remediation_success"] = not bad["remediation_success"]
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("final_verdict", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_final_verdict_stale_test_report(self) -> None:
        bad = json.loads(json.dumps(self.fixture["final_verdict"]))
        bad["test_report_refs"] = ["3" * 64]
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("final_verdict", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_oracle_hold_requires_null_outcome(self) -> None:
        bad = json.loads(json.dumps(self.fixture["oracle_predicate"]))
        bad["verdict"] = "HOLD"
        bad["reason_code"] = "not_executed"
        bad["outcome"] = True
        with self.assertRaises(ContractError):
            contracts.semantic_validate("oracle_predicate", bad, self.ctx)

    def test_run_terminal_fields(self) -> None:
        bad = json.loads(json.dumps(self.fixture["run"]))
        bad["status"] = "running"
        with self.assertRaises(ContractError):
            contracts.semantic_validate("run", bad, self.ctx)

    def test_evidence_event_hash_mismatch(self) -> None:
        bad = json.loads(json.dumps(self.fixture["evidence_event"]))
        bad["event_hash"] = "0" * 64
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("evidence_event", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_evidence_event_chain_broken(self) -> None:
        bad = json.loads(json.dumps(self.fixture["evidence_event"]))
        bad["previous_event_hash"] = "1" * 64
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("evidence_event", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_evidence_event_not_in_ctx_events(self) -> None:
        ctx = dict(self.ctx)
        ctx["events"] = []
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("evidence_event",
                                        self.fixture["evidence_event"], ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_evidence_event_single_event_without_ctx_events(self) -> None:
        ctx = dict(self.ctx)
        del ctx["events"]
        contracts.semantic_validate("evidence_event",
                                    self.fixture["evidence_event"], ctx)

    def test_approval_approved_with_failed_check(self) -> None:
        bad = json.loads(json.dumps(self.fixture["approval"]))
        bad["required_checks"][0]["passed"] = False
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("approval", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_approval_check_ref_stale(self) -> None:
        bad = json.loads(json.dumps(self.fixture["approval"]))
        bad["required_checks"][0]["ref"] = "4" * 64
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("approval", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_approval_check_passed_mismatch(self) -> None:
        bad = json.loads(json.dumps(self.fixture["approval"]))
        bad["required_checks"][0]["passed"] = False
        bad["decision"] = "rejected"
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("approval", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_run_manifest_stale(self) -> None:
        bad = json.loads(json.dumps(self.fixture["run"]))
        bad["manifest_ref"] = "5" * 64
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("run", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_remediation_result_ref_stale(self) -> None:
        bad = json.loads(json.dumps(self.fixture["remediation"]))
        bad["unit_result"]["ref"] = "6" * 64
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("remediation", bad, self.ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_final_verdict_false_oracle(self) -> None:
        ctx = dict(self.ctx)
        oracle = json.loads(json.dumps(self.fixture["oracle_predicate"]))
        oracle["verdict"] = "PASS"
        oracle["outcome"] = True
        oracle["reason_code"] = "exploit_succeeded"
        ctx["oracle_evaluation"] = oracle
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("final_verdict", self.fixture["final_verdict"], ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_final_verdict_flags_only_rejected(self) -> None:
        ctx = dict(self.ctx)
        ctx["oracle_evaluation"] = {"schema_validated": True,
                                    "input_digest_verified": True}
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("final_verdict", self.fixture["final_verdict"], ctx)
        self.assertEqual(caught.exception.reason_code, "input_invalid")

    def test_final_verdict_missing_oracle(self) -> None:
        ctx = dict(self.ctx)
        del ctx["oracle_evaluation"]
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("final_verdict", self.fixture["final_verdict"], ctx)
        self.assertEqual(caught.exception.reason_code, "input_invalid")

    def test_final_verdict_false_test_report(self) -> None:
        fixture = self.fixture
        objects = _objects(fixture)
        ref = fixture["final_verdict"]["test_report_refs"][0]
        objects[ref] = {"campaign_id": "camp-1", "run_id": "run-1",
                        "suite_id": "suite-1", "passed": False,
                        "checks": [{"name": "unit-tests", "passed": False}]}
        ctx = _ctx(fixture, objects)
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("final_verdict", fixture["final_verdict"], ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_final_verdict_forged_passed_false_report(self) -> None:
        fixture = self.fixture
        objects = _objects(fixture)
        ref = fixture["final_verdict"]["test_report_refs"][0]
        forged = {"campaign_id": "camp-1", "run_id": "run-1",
                  "suite_id": "suite-1", "passed": True,
                  "checks": [{"name": "unit-tests", "passed": False}]}
        objects[digest(forged)] = forged
        verdict = json.loads(json.dumps(fixture["final_verdict"]))
        verdict["test_report_refs"] = [digest(forged)]
        ctx = _ctx(fixture, objects)
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("final_verdict", verdict, ctx)
        self.assertEqual(caught.exception.reason_code, "policy_denied")

    def test_missing_ctx_findings_fails_closed(self) -> None:
        ctx = dict(self.ctx)
        del ctx["findings"]
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("remediation", self.fixture["remediation"], ctx)
        self.assertEqual(caught.exception.reason_code, "input_invalid")

    def test_missing_ctx_remediations_fails_closed(self) -> None:
        ctx = dict(self.ctx)
        del ctx["remediations"]
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("approval", self.fixture["approval"], ctx)
        self.assertEqual(caught.exception.reason_code, "input_invalid")

    def test_missing_ctx_get_object_fails_closed(self) -> None:
        ctx = dict(self.ctx)
        del ctx["get_object"]
        with self.assertRaises(ContractError) as caught:
            contracts.semantic_validate("run", self.fixture["run"], ctx)
        self.assertEqual(caught.exception.reason_code, "input_invalid")


class DerivationTest(unittest.TestCase):
    def test_derive_remediation_success(self) -> None:
        base = {
            "oracle_verdict": "FAIL",
            "oracle_reason_code": "exploit_not_succeeded",
            "run_completion": "completed",
            "benign_regression_passed": True,
            "remediation_id": "rem-1",
            "test_report_refs": ["a" * 64],
        }
        self.assertTrue(contracts.derive_remediation_success(base))
        for key, value in (("oracle_verdict", "PASS"),
                           ("oracle_reason_code", "exploit_succeeded"),
                           ("run_completion", "incomplete"),
                           ("benign_regression_passed", None),
                           ("remediation_id", None),
                           ("test_report_refs", [])):
            mutated = dict(base)
            mutated[key] = value
            self.assertFalse(contracts.derive_remediation_success(mutated))


if __name__ == "__main__":
    unittest.main()
