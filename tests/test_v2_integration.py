"""Integration tests for CLI import/show/CVSS and HTTP assessment using real Store/Runtime."""
import json
from pathlib import Path

import pytest

from tests.test_v1_cli import cli_env  # noqa: F401  (fixture import only)

from ai_yori_murasaki.assessment import project_report
from ai_yori_murasaki.server import handle_request
from ai_yori_murasaki.jsonio import digest


def _write(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj))
    return path


def _trivy_candidate() -> dict:
    return {
        "SchemaVersion": 2,
        "ArtifactName": "fixture-image",
        "ArtifactType": "container_image",
        "Results": [
            {
                "Target": "fixture-image (alpine 3.19)",
                "Class": "os-pkgs",
                "Type": "alpine",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2024-0001",
                        "PkgName": "openssl",
                        "InstalledVersion": "3.1.0-r0",
                        "FixedVersion": "3.1.1-r0",
                        "Severity": "HIGH",
                        "Title": "fixture vulnerability",
                    }
                ],
            }
        ],
    }


def _import_context() -> dict:
    return {
        "target": "cli",
        "target_version": "v1",
        "actor": "auditor",
        "previous_report_ref": None,
    }


def _start_run(rt, cid):
    return rt.start_run(cid, "verify", 1, "integration-start")


def _run_id(run):
    if isinstance(run, dict):
        for key in ("run_id", "id"):
            if isinstance(run.get(key), str):
                return run[key]
    return run


def test_import_show_roundtrip_through_cli(cli_env, tmp_path):
    rt, cid, call = cli_env
    rid = _run_id(_start_run(rt, cid))

    candidate = _write(tmp_path / "trivy.json", _trivy_candidate())
    context = _write(tmp_path / "context.json", _import_context())

    rc, out = call(
        "assess", "import",
        "--campaign", cid,
        "--run", rid,
        "--format", "trivy",
        "--input", str(candidate),
        "--context", str(context),
    )
    assert rc == 0
    assert out["status"] == "ok"
    data = out["data"]
    assert out["campaign_id"] == cid
    assert out["run_id"] == rid

    rc, out = call("assess", "show", "--campaign", cid)
    assert rc == 0
    report = out["data"]["report"]
    assert report is not None
    assert report["status"] == "ready"
    assert report["campaign_id"] == cid
    assert report["run_id"] == rid
    assert len(report["findings"]) == 1
    assert report["findings"][0]["finding_id"]
    assert report["coverage"]["categories"]
    statuses = {c["status"] for c in report["coverage"]["categories"]}
    assert "not_executed" in statuses


def test_http_assessment_returns_ready_report(cli_env, tmp_path):
    rt, cid, call = cli_env
    rid = _run_id(_start_run(rt, cid))

    candidate = _write(tmp_path / "trivy.json", _trivy_candidate())
    context = _write(tmp_path / "context.json", _import_context())
    rc, _ = call(
        "assess", "import",
        "--campaign", cid,
        "--run", rid,
        "--format", "trivy",
        "--input", str(candidate),
        "--context", str(context),
    )
    assert rc == 0

    status, headers, body = handle_request(
        rt,
        "GET",
        f"/api/campaigns/{cid}/assessment",
        headers={"Host": "127.0.0.1:8765"},
    )
    assert status == 200
    assert isinstance(headers, dict)
    assert isinstance(body, (bytes, bytearray))
    payload = json.loads(bytes(body).decode("utf-8"))
    report = payload["report"]
    assert report["status"] == "ready"
    assert report["campaign_id"] == cid
    assert len(report["findings"]) == 1


def test_blue_view_never_discloses_report(cli_env, tmp_path):
    rt, cid, call = cli_env
    rid = _run_id(_start_run(rt, cid))

    candidate = _write(tmp_path / "trivy.json", _trivy_candidate())
    context = _write(tmp_path / "context.json", _import_context())
    rc, _ = call(
        "assess", "import",
        "--campaign", cid,
        "--run", rid,
        "--format", "trivy",
        "--input", str(candidate),
        "--context", str(context),
    )
    assert rc == 0

    status, headers, body = handle_request(
        rt,
        "GET",
        f"/api/campaigns/{cid}/assessment?view=blue",
        headers={"Host": "127.0.0.1:8765"},
    )
    payload = json.loads(bytes(body).decode("utf-8"))
    text = json.dumps(payload)
    assert "findings" not in text or payload["report"].get("status") == "withheld"
    report = payload["report"]
    assert report.get("status") == "withheld"
    assert "findings" not in report

    # phase/reveal attempts must not disclose either
    for query in ("?view=blue&phase=reveal", "?view=blue&reveal=1"):
        status, headers, body = handle_request(
            rt,
            "GET",
            f"/api/campaigns/{cid}/assessment{query}",
            headers={"Host": "127.0.0.1:8765"},
        )
        payload = json.loads(bytes(body).decode("utf-8"))
        report = payload["report"]
        assert report.get("status") == "withheld"
        assert "findings" not in report


def test_blue_view_plain_identical_for_existent_and_nonexistent(cli_env):
    rt, cid, call = cli_env
    _start_run(rt, cid)

    status_a, headers_a, body_a = handle_request(
        rt,
        "GET",
        f"/api/campaigns/{cid}/assessment?view=blue",
        headers={"Host": "127.0.0.1:8765"},
    )
    status_b, headers_b, body_b = handle_request(
        rt,
        "GET",
        "/api/campaigns/absent1/assessment?view=blue",
        headers={"Host": "127.0.0.1:8765"},
    )
    assert status_a == status_b
    assert bytes(body_a) == bytes(body_b)


def test_cvss_invalid_vector_exit_2(cli_env, tmp_path):
    rt, cid, call = cli_env
    basis = _write(tmp_path / "basis.json", {})
    rc, out = call(
        "assess", "cvss",
        "--vector", "not-a-vector",
        "--basis", str(basis),
    )
    assert rc == 2
    assert out["data"]["status"] == "invalid"


def test_cvss_valid_missing_basis_unevaluated_exit_3(cli_env, tmp_path):
    rt, cid, call = cli_env
    basis = _write(tmp_path / "basis.json", {})
    rc, out = call(
        "assess", "cvss",
        "--vector", "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:N/SI:N/SA:N",
        "--basis", str(basis),
    )
    assert rc == 3
    assert out["data"]["status"] == "unevaluated"


def test_cvss_valid_basis_scored_exit_0(cli_env, tmp_path):
    rt, cid, call = cli_env
    ref = rt.store.put_object({"summary": "fixture basis"})
    basis = _write(tmp_path / "basis.json", {
        "AV": [ref], "AC": [ref], "AT": [ref], "PR": [ref], "UI": [ref],
        "VC": [ref], "VI": [ref], "VA": [ref], "SC": [ref], "SI": [ref], "SA": [ref],
    })
    rc, out = call(
        "assess", "cvss",
        "--vector", "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:N",
        "--basis", str(basis),
    )
    assert rc == 0
    assert out["status"] == "ok"
    data = out["data"]
    assert data["status"] == "scored"
    assert data["record"]["score"] == 9.9
    ref = data["cvss_ref"]
    assert isinstance(ref, str)
    stored = rt.store.get_object(ref)
    assert stored["score"] == 9.9


def test_malformed_report_referenced_by_event_holds(cli_env):
    rt, cid, call = cli_env
    rid = _run_id(_start_run(rt, cid))

    # Store a malformed report object and reference it from an assessment event.
    bad_ref = rt.store.put_object({"summary": "fixture basis"})
    rt.record_event(
        rid,
        "state_captured",
        "orchestrator",
        evidence_refs=(bad_ref,),
        extensions={
            "assessment_version": "1.0.0",
            "assessment_ref": bad_ref,
            "assessment_target_version": "v1",
        },
    )

    rc, out = call("assess", "show", "--campaign", cid)
    assert rc == 3
    assert out["status"] == "hold"


def test_project_report_pure_partial_dict():
    minimal = {
        "report_id": "r1",
        "campaign_id": "c1",
        "run_id": "run1",
        "findings": [
            {
                "finding_id": "f1",
                "title": "t",
                "validation": {
                    "result": "pass",
                    "oracle_verdict": "blocked",
                    "attack_blocked": True,
                    "benign_continued": True,
                    "related_tests_passed": True,
                    "oracle_evidence_ref": "abc",
                    "version_recorded": True,
                    "unverified_scope_recorded": True,
                },
                "remediation": {
                    "proposal_ref": "p1",
                    "applied": True,
                    "applied_env": "prod",
                    "applied_version": "1.0",
                    "diff_ref": "d1",
                    "side_effects": ["none"],
                },
            }
        ],
        "cvss": [
            {
                "finding_id": "f1",
                "status": "scored",
                "score": 9.9,
                "nomenclature": "CVSS:4.0",
                "vector": "CVSS:4.0/AV:N",
                "calculator": "calc",
                "metric_basis": {"11": ["basis-ref"]},
                "reason": "",
            }
        ],
    }
    out = project_report(minimal, "operator", "operator")
    assert out["status"] == "ready"
    assert out["report_id"] == "r1"
    f = out["findings"][0]
    assert f["validation"]["result"] == "pass"
    assert f["validation"]["oracle_verdict"] == "blocked"
    assert f["remediation"]["side_effects"] == ["none"]
    assert out["cvss"][0]["metric_basis"] == {"11": ["basis-ref"]}
    assert out["cvss"][0]["score"] == 9.9

    # partial dict must not raise
    partial = project_report({"report_id": "r2"}, "operator", "operator")
    assert partial["status"] == "ready"
    assert partial["findings"] == []
    assert partial["cvss"] == []

    # blue view withholds regardless of input
    blue = project_report(minimal, "blue", "operator")
    assert blue["status"] == "withheld"
    assert "findings" not in blue
