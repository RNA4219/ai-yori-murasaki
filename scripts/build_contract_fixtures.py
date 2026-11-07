"""fixtures/v1/contracts.json を実 digest で生成する。

プレースホルダを禁止し、CAS 参照・scope_digest・event_hash を実値で埋める。
生成物は fixtures/v1/contracts.json に書き出す。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_yori_murasaki.jsonio import canonical, digest  # noqa: E402

FIXTURE = ROOT / "fixtures" / "v1" / "contracts.json"


def _ref(obj: dict) -> str:
    return digest(obj)


def _event_hash(event: dict) -> str:
    body = {k: v for k, v in event.items() if k != "event_hash"}
    return hashlib.sha256(canonical(body) + event["previous_event_hash"].encode("ascii")).hexdigest()


def build() -> dict:
    # 1. CAS objects を先に構築し、ref を実 digest で確定する。
    patch_obj = {"kind": "patch-bundle", "content": "patch"}
    static_obj = {"kind": "static-scan", "passed": True}
    unit_obj = {"kind": "unit", "passed": True}
    integration_obj = {"kind": "integration", "passed": True}
    replay_obj = {"kind": "oracle-replay", "verdict": "FAIL"}
    benign_obj = {"kind": "benign-regression", "passed": True}
    evidence_obj = {"kind": "evidence", "content": "evidence"}
    test_report_obj = {
        "campaign_id": "camp-1",
        "run_id": "run-1",
        "suite_id": "suite-1",
        "passed": True,
        "checks": [
            {"name": "unit-tests", "passed": True},
            {"name": "integration-tests", "passed": True},
        ],
    }
    manifest_obj = {"kind": "manifest", "run_id": "run-1"}
    input_obj = {"method": "GET", "path": "/record", "status": 403}

    patch_ref = _ref(patch_obj)
    static_ref = _ref(static_obj)
    unit_ref = _ref(unit_obj)
    integration_ref = _ref(integration_obj)
    replay_ref = _ref(replay_obj)
    benign_ref = _ref(benign_obj)
    evidence_ref = _ref(evidence_obj)
    test_report_ref = _ref(test_report_obj)
    manifest_ref = _ref(manifest_obj)
    input_ref = _ref(input_obj)

    scope = {
        "targets": [
            {"kind": "lab-target", "identifier": "lab://target-1"}
        ],
        "max_targets": 1,
    }
    scope_digest = digest(scope)

    campaign = {
        "schema_version": "1.0.0",
        "campaign_id": "camp-1",
        "intent_id": "INT-AYM-PRODUCT-20261106",
        "scope": scope,
        "authorization": {
            "operator_id": "operator-1",
            "granted_at": "2026-11-06T10:00:00Z",
            "expires_at": "2026-12-06T10:00:00Z",
            "scope_digest": scope_digest,
        },
        "created_at": "2026-11-06T10:00:00Z",
        "operator_id": "operator-1",
        "status": "running",
    }

    run = {
        "schema_version": "1.0.0",
        "campaign_id": "camp-1",
        "run_id": "run-1",
        "phase": "red",
        "actor": {"role": "worker-red", "agent_id": "agent-1", "model": "model-x"},
        "started_at": "2026-11-06T10:05:00Z",
        "ended_at": "2026-11-06T10:30:00Z",
        "status": "completed",
        "completion": "completed",
        "cause": "none",
        "seed": 42,
        "identity": {"operator_id": "operator-1", "worker_id": "worker-1"},
        "budget": {"max_tool_calls": 100, "max_wall_seconds": 3600,
                   "max_tokens": 100000},
        "manifest_ref": manifest_ref,
    }

    finding = {
        "schema_version": "1.0.0",
        "finding_id": "finding-1",
        "campaign_id": "camp-1",
        "run_id": "run-1",
        "source": "trivy",
        "severity": "high",
        "title": "Example finding",
        "location": {"path": "src/app.py", "line": 10, "column": 5},
        "references": ["https://example.invalid/advisory"],
    }

    evidence_event = {
        "schema_version": "1.0.0",
        "campaign_id": "camp-1",
        "run_id": "run-1",
        "sequence": 1,
        "actor": {"role": "orchestrator", "agent_id": "agent-1", "model": None},
        "source": "orchestrator",
        "event_type": "scope_validated",
        "timestamp": "2026-11-06T10:06:00Z",
        "target": {"kind": "lab-target", "identifier": "lab://target-1"},
        "finding": {"finding_id": "finding-1", "severity": "high",
                    "title": "Example finding"},
        "evidence_refs": [evidence_ref],
        "previous_event_hash": "0" * 64,
        "event_hash": "0" * 64,
    }
    evidence_event["event_hash"] = _event_hash(evidence_event)

    oracle_predicate = {
        "schema_version": "1.0.0",
        "predicate_id": "pred-1",
        "kind": "http_status",
        "input_digest": input_ref,
        "expected": {"method": "GET", "path": "/record", "status": 200},
        "actual": {"method": "GET", "path": "/record", "status": 403},
        "outcome": False,
        "verdict": "FAIL",
        "reason_code": "exploit_not_succeeded",
        "evaluated_at": "2026-11-06T10:07:00Z",
    }

    remediation = {
        "schema_version": "1.0.0",
        "remediation_id": "rem-1",
        "finding_id": "finding-1",
        "campaign_id": "camp-1",
        "run_id": "run-1",
        "patch_bundle_ref": patch_ref,
        "static_scan_result": {"passed": True, "ref": static_ref},
        "unit_result": {"passed": True, "ref": unit_ref},
        "integration_result": {"passed": True, "ref": integration_ref},
        "oracle_replay_result": {"verdict": "FAIL",
                                 "reason_code": "exploit_not_succeeded",
                                 "ref": replay_ref},
        "benign_regression_result": {"passed": True, "ref": benign_ref},
        "status": "benign_regression_passed",
    }

    approval = {
        "schema_version": "1.0.0",
        "approval_id": "appr-1",
        "remediation_id": "rem-1",
        "reviewer_id": "reviewer-1",
        "decision": "approved",
        "decided_at": "2026-11-06T11:00:00Z",
        "required_checks": [
            {"name": "unit-tests", "passed": True, "ref": unit_ref},
            {"name": "integration-tests", "passed": True, "ref": integration_ref},
            {"name": "trivy", "passed": True, "ref": static_ref},
            {"name": "codeql", "passed": True, "ref": static_ref},
            {"name": "evidence-verify", "passed": True, "ref": evidence_ref},
            {"name": "oracle-regression", "passed": True, "ref": replay_ref},
            {"name": "red-replay", "passed": True, "ref": replay_ref},
            {"name": "human-review", "passed": True, "ref": benign_ref},
        ],
        "expires_at": "2026-11-07T11:00:00Z",
    }

    final_verdict = {
        "schema_version": "1.0.0",
        "campaign_id": "camp-1",
        "run_id": "run-1",
        "oracle_verdict": "FAIL",
        "oracle_reason_code": "exploit_not_succeeded",
        "run_completion": "completed",
        "adapter_status": [
            {"name": "trivy", "status": "ok"},
            {"name": "codeql", "status": "ok"},
        ],
        "cancellation": None,
        "remediation_success": True,
        "benign_regression_passed": True,
        "remediation_id": "rem-1",
        "test_report_refs": [test_report_ref],
        "decided_at": "2026-11-06T11:30:00Z",
    }

    seal = {
        "campaign_id": "camp-1",
        "run_id": "run-1",
        "last_sequence": 1,
        "last_event_hash": evidence_event["event_hash"],
        "sealed_at": "2026-11-06T10:31:00Z",
        "operator_id": "operator-1",
    }

    objects = {
        patch_ref: patch_obj,
        static_ref: static_obj,
        unit_ref: unit_obj,
        integration_ref: integration_obj,
        replay_ref: replay_obj,
        benign_ref: benign_obj,
        evidence_ref: evidence_obj,
        test_report_ref: test_report_obj,
        manifest_ref: manifest_obj,
        input_ref: input_obj,
    }

    return {
        "campaign": campaign,
        "run": run,
        "evidence_event": evidence_event,
        "oracle_predicate": oracle_predicate,
        "finding": finding,
        "remediation": remediation,
        "approval": approval,
        "final_verdict": final_verdict,
        "seal": seal,
        "runs": [run],
        "findings": [finding],
        "remediations": [remediation],
        "objects": objects,
    }


def main() -> None:
    fixture = build()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
