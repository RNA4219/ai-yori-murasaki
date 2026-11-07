"""Tests for ai_yori_murasaki.worker (DockerLab + ObservationBroker)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ai_yori_murasaki import worker as worker_mod
from ai_yori_murasaki.adapters import base as base_mod


class _Recorder:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.responses: List[Dict[str, Any]] = []
        self.inspect_response = None

    def __call__(self, argv, timeout=30.0, max_bytes=1048576, retry_cap=0, *,
                 staging_root=None, allowed_images=None, allowed_names=None):
        self.calls.append({
            "argv": list(argv),
            "timeout": timeout,
            "max_bytes": max_bytes,
            "retry_cap": retry_cap,
            "staging_root": staging_root,
            "allowed_images": list(allowed_images) if allowed_images is not None else None,
            "allowed_names": list(allowed_names) if allowed_names is not None else None,
        })
        if list(argv[:2]) == ["docker", "network"] and len(argv) > 2 and argv[2] == "inspect":
            return self.inspect_response if self.inspect_response is not None else _ok(
                json.dumps([{"Name": argv[3], "Internal": True,
                             "Labels": {"ai-yori-murasaki": "lab"}}]).encode())
        if self.responses:
            return self.responses.pop(0)
        return {"returncode": 0, "stdout": b"", "stderr": b""}


def _ok(stdout: bytes = b"") -> Dict[str, Any]:
    return {"returncode": 0, "stdout": stdout, "stderr": b""}


def _fail(stderr: bytes = b"boom") -> Dict[str, Any]:
    return {"returncode": 1, "stdout": b"", "stderr": stderr}


@pytest.fixture()
def staging(tmp_path: Path) -> Path:
    root = tmp_path / "lab"
    root.mkdir()
    return root


def _make_lab(staging: Path) -> worker_mod.DockerLab:
    return worker_mod.DockerLab(staging)


def _enter_lab(lab: worker_mod.DockerLab, rec: _Recorder, monkeypatch) -> None:
    monkeypatch.setattr(base_mod, "run_docker", rec)
    lab.__enter__()


def test_staging_root_must_be_absolute() -> None:
    with pytest.raises(ValueError):
        worker_mod.DockerLab(Path("relative/path"))


def test_staging_root_must_be_outside_project() -> None:
    project = Path(worker_mod._project_root())
    with pytest.raises(ValueError):
        worker_mod.DockerLab(project / "staging")


def test_default_image_only(staging: Path) -> None:
    with pytest.raises(ValueError):
        worker_mod.DockerLab(staging, image="evil/image:latest")
    lab = worker_mod.DockerLab(staging)
    assert lab.image == worker_mod.DEFAULT_IMAGE


def test_approved_image_list(staging: Path) -> None:
    lab = worker_mod.DockerLab(staging, image="other/img:1",
                               approved_images=["other/img:1"])
    assert lab.image == "other/img:1"
    with pytest.raises(ValueError):
        worker_mod.DockerLab(staging, image="other/img:2",
                             approved_images=["other/img:1"])


def test_network_create_uses_fixed_argv(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        assert rec.calls, "network create must be recorded"
        argv = rec.calls[0]["argv"]
        assert argv[:3] == ["docker", "network", "create"]
        assert "--internal" in argv
        assert "--label" in argv
        assert argv[argv.index("--label") + 1] == "ai-yori-murasaki=lab"
        assert argv[-1].startswith("aym-")
        assert rec.calls[0]["allowed_names"] is not None
    finally:
        lab.close()


def test_start_target_uses_fixed_argv(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        src = staging / "target"
        src.mkdir()
        (src / "target.py").write_text("def authorize(role):\n    return True\n")
        host = lab.start_target(src)
        assert host.startswith("aym-")
        # wait for the background future to run
        lab._target_future.result(timeout=5)
        run_calls = [c for c in rec.calls if c["argv"][:2] == ["docker", "run"]]
        assert run_calls, "target run must be recorded"
        argv = run_calls[0]["argv"]
        assert lab._target_name in argv, "target argv must use exact target name"
        assert "--network" in argv
        net = argv[argv.index("--network") + 1]
        assert net.startswith("aym-")
        assert "--user" in argv
        assert argv[argv.index("--user") + 1] == "65534:65534"
        assert "--read-only" in argv
        assert "--cap-drop" in argv
        assert argv[argv.index("--cap-drop") + 1] == "ALL"
        mount = argv[argv.index("--mount") + 1]
        assert "readonly=true" in mount
        assert "target=/app" in mount
        assert run_calls[0]["timeout"] == worker_mod._TARGET_TIMEOUT
    finally:
        lab.close()


def test_start_target_rejects_outside_staging(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        outside = Path(tempfile.mkdtemp())
        with pytest.raises(ValueError):
            lab.start_target(outside)
    finally:
        lab.close()


def test_blue_cannot_mount_target_or_red_content(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        src = staging / "target"
        src.mkdir()
        (src / "target.py").write_text("def authorize(role):\n    return True\n")
        lab.start_target(src)
        lab._target_future.result(timeout=5)
        blue = worker_mod.stage_worker(staging, "blue", telemetry=[
            {"timestamp": "2024-01-01T00:00:00Z", "method": "GET",
             "path": "/record?role=guest&token=abc", "status": 200,
             "role": "guest", "body": "secret"},
        ])
        assert (blue / "worker.py").is_file()
        assert (blue / "telemetry.json").is_file()
        assert not (blue / "target.py").exists()
        rec.responses.append(_ok(b'{"mode":"blue","status":"ok"}'))
        out = lab.run_worker(blue, "blue", "blue")
        assert out["mode"] == "blue"
        run_calls = [c for c in rec.calls if c["argv"][:2] == ["docker", "run"]]
        worker_argv = run_calls[-1]["argv"]
        assert worker_argv[worker_argv.index("--network") + 1] == "none"
        mount = worker_argv[worker_argv.index("--mount") + 1]
        assert "source={}".format(os.path.realpath(str(blue))) in mount
        assert "target=/app" in mount
        assert "readonly=true" in mount
        # telemetry sanitized: query stripped, body dropped
        text = (blue / "telemetry.json").read_text()
        row = json.loads(text.strip())
        assert row["path"] == "/record"
        assert "body" not in row
        assert set(row.keys()) <= {"timestamp", "method", "path", "status", "role"}
    finally:
        lab.close()


def test_run_worker_rejects_arbitrary_mode(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        src = staging / "target"
        src.mkdir()
        (src / "target.py").write_text("def authorize(role):\n    return True\n")
        lab.start_target(src)
        lab._target_future.result(timeout=5)
        with pytest.raises(ValueError):
            lab.run_worker(src, "blue", "red")
        with pytest.raises(ValueError):
            lab.run_worker(src, "red", "blue")
        with pytest.raises(ValueError):
            lab.run_worker(src, "oracle", "red")
        with pytest.raises(ValueError):
            lab.run_worker(src, "nope", "probe")
    finally:
        lab.close()


def test_run_worker_rejects_host_args(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        src = staging / "target"
        src.mkdir()
        (src / "target.py").write_text("def authorize(role):\n    return True\n")
        lab.start_target(src)
        lab._target_future.result(timeout=5)
        with pytest.raises(ValueError):
            lab.run_worker(src, "red", "red", args=("--host", "evil.example"))
    finally:
        lab.close()


def test_worker_uses_self_target_host(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        src = staging / "target"
        src.mkdir()
        (src / "target.py").write_text("def authorize(role):\n    return True\n")
        host = lab.start_target(src)
        lab._target_future.result(timeout=5)
        rec.responses.append(_ok(b'{"mode":"red","status":"ok"}'))
        lab.run_worker(src, "red", "red")
        run_calls = [c for c in rec.calls if c["argv"][:2] == ["docker", "run"]]
        worker_argv = run_calls[-1]["argv"]
        assert worker_argv[worker_argv.index("--network") + 1] == lab.network
        assert host in worker_argv
        assert "evil.example" not in worker_argv
    finally:
        lab.close()


def test_cleanup_limits_own_names(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    src = staging / "target"
    src.mkdir()
    (src / "target.py").write_text("def authorize(role):\n    return True\n")
    lab.start_target(src)
    lab._target_future.result(timeout=5)
    lab.close()
    stop_calls = [c for c in rec.calls if c["argv"][:2] == ["docker", "stop"]]
    rm_calls = [c for c in rec.calls if c["argv"][:2] == ["docker", "rm"]]
    net_rm = [c for c in rec.calls if c["argv"][:3] == ["docker", "network", "rm"]]
    assert stop_calls, "target must be stopped"
    assert rm_calls, "target must be removed"
    assert net_rm, "network must be removed"
    for c in stop_calls + rm_calls:
        assert c["argv"][2].startswith("aym-")
        assert c["allowed_names"] is not None
    for c in net_rm:
        assert c["argv"][3].startswith("aym-")


def test_cleanup_does_not_mask_original_error(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    src = staging / "target"
    src.mkdir()
    (src / "target.py").write_text("def authorize(role):\n    return True\n")
    lab.start_target(src)
    lab._target_future.result(timeout=5)
    # network inspect fails -> cleanup raises, but original error must win
    rec.inspect_response = _fail(b"inspect failed")
    original = RuntimeError("original")
    with pytest.raises(RuntimeError, match="original"):
        try:
            raise original
        except RuntimeError as exc:
            lab.__exit__(RuntimeError, exc, exc.__traceback__)
            raise


def test_timeout_passed_to_run_docker(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        src = staging / "target"
        src.mkdir()
        (src / "target.py").write_text("def authorize(role):\n    return True\n")
        lab.start_target(src)
        lab._target_future.result(timeout=5)
        run_calls = [c for c in rec.calls if c["argv"][:2] == ["docker", "run"]]
        assert run_calls[0]["timeout"] == 240.0
    finally:
        lab.close()


def test_health_wait_finite(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        src = staging / "target"
        src.mkdir()
        (src / "target.py").write_text("def authorize(role):\n    return True\n")
        lab.start_target(src)
        lab._target_future.result(timeout=5)
        rec.responses.append(_ok(b'{"mode":"probe","status":"ok"}'))
        assert lab.wait_healthy(timeout=1.0) is True
        with pytest.raises(ValueError):
            lab.wait_healthy(timeout=0)
    finally:
        lab.close()


def test_worker_cannot_inject_authority(staging: Path, monkeypatch) -> None:
    rec = _Recorder()
    lab = _make_lab(staging)
    _enter_lab(lab, rec, monkeypatch)
    try:
        src = staging / "target"
        src.mkdir()
        (src / "target.py").write_text("def authorize(role):\n    return True\n")
        lab.start_target(src)
        lab._target_future.result(timeout=5)
        rec.responses.append(_ok(b'{"mode":"red","status":"ok","verdict":"pass"}'))
        out = lab.run_worker(src, "red", "red")
        # worker output is data only; no verdict authority is granted
        assert "verdict" in out
        assert not hasattr(lab, "verdict")
    finally:
        lab.close()


class _FakeRuntime:
    """Minimal runtime matching the real ObservationBroker interface."""

    def __init__(self) -> None:
        self.runs: Dict[str, Any] = {}
        self.objects: Dict[str, Dict[str, Any]] = {}
        self.events: List[Dict[str, Any]] = []
        self._counter = 0

    def add_run(self, run_id: str, phase: str, status: str = "running") -> None:
        self.runs[run_id] = {"phase": phase, "status": status}

    def get_run(self, run_id: str) -> Any:
        return self.runs.get(run_id)

    class _Store:
        def __init__(self, parent: "_FakeRuntime") -> None:
            self._parent = parent

        def put_object(self, obj: Dict[str, Any]) -> str:
            self._parent._counter += 1
            ref = "cas-{}".format(self._parent._counter)
            self._parent.objects[ref] = obj
            return ref

    @property
    def store(self) -> "_FakeRuntime._Store":
        return _FakeRuntime._Store(self)

    def record_event(self, run_id, event_type, actor, evidence_refs=(), extensions=None):
        self.events.append({
            "run_id": run_id,
            "event_type": event_type,
            "actor": actor,
            "evidence_refs": tuple(evidence_refs),
            "extensions": dict(extensions or {}),
        })


def _make_fake_runtime(phase: str = "red", status: str = "running") -> _FakeRuntime:
    rt = _FakeRuntime()
    rt.add_run("run-1", phase, status)
    return rt


def test_broker_accepts_valid_observation() -> None:
    rt = _make_fake_runtime("red")
    broker = worker_mod.ObservationBroker(rt, "run-1", "red")
    rec = broker.submit_observation({
        "kind": "http_response",
        "data": {"status": 200, "path": "/health"},
        "observed_at": "2024-01-01T00:00:00Z",
    })
    assert rec["role"] == "red"
    assert rec["phase"] == "red"
    assert rec["kind"] == "http_response"
    assert rec["data_ref"] in rt.objects
    assert len(rt.events) == 1
    assert rt.events[0]["event_type"] == "telemetry_ingested"
    assert rt.events[0]["actor"] == "orchestrator"
    assert rt.events[0]["evidence_refs"] == (rec["data_ref"],)


def test_broker_rejects_extra_keys() -> None:
    rt = _make_fake_runtime("red")
    broker = worker_mod.ObservationBroker(rt, "run-1", "red")
    with pytest.raises(ValueError):
        broker.submit_observation({
            "kind": "http_response",
            "data": {},
            "observed_at": "2024-01-01T00:00:00Z",
            "actor": "worker",
        })


def test_broker_rejects_forbidden_nested_keys() -> None:
    rt = _make_fake_runtime("red")
    broker = worker_mod.ObservationBroker(rt, "run-1", "red")
    for key in ("actor", "agent_id", "sequence", "hash", "approval",
                "source", "event_type"):
        with pytest.raises(ValueError):
            broker.submit_observation({
                "kind": "detection",
                "data": {"nested": {key: "x"}},
                "observed_at": "2024-01-01T00:00:00Z",
            })


def test_broker_rejects_unknown_kind() -> None:
    rt = _make_fake_runtime("red")
    broker = worker_mod.ObservationBroker(rt, "run-1", "red")
    with pytest.raises(ValueError):
        broker.submit_observation({
            "kind": "verdict",
            "data": {},
            "observed_at": "2024-01-01T00:00:00Z",
        })


def test_broker_rejects_oversize_data() -> None:
    rt = _make_fake_runtime("red")
    broker = worker_mod.ObservationBroker(rt, "run-1", "red")
    big = "x" * (worker_mod._OBS_DATA_CAP + 1)
    with pytest.raises(ValueError):
        broker.submit_observation({
            "kind": "telemetry",
            "data": {"blob": big},
            "observed_at": "2024-01-01T00:00:00Z",
        })


def test_broker_rejects_invalid_timestamp() -> None:
    rt = _make_fake_runtime("red")
    broker = worker_mod.ObservationBroker(rt, "run-1", "red")
    with pytest.raises(ValueError):
        broker.submit_observation({
            "kind": "test_result",
            "data": {},
            "observed_at": "not-a-timestamp",
        })


def test_broker_role_phase_binding() -> None:
    rt = _make_fake_runtime("red")
    red = worker_mod.ObservationBroker(rt, "run-1", "red")
    blue = worker_mod.ObservationBroker(rt, "run-1", "blue")
    assert red.phase == "red"
    assert blue.phase == "blue_blind"
    with pytest.raises(ValueError):
        worker_mod.ObservationBroker(rt, "run-1", "oracle")


def test_broker_rejects_phase_mismatch() -> None:
    rt = _make_fake_runtime("blue_blind")
    broker = worker_mod.ObservationBroker(rt, "run-1", "red")
    with pytest.raises(ValueError):
        broker.submit_observation({
            "kind": "detection",
            "data": {},
            "observed_at": "2024-01-01T00:00:00Z",
        })


def test_broker_rejects_not_running() -> None:
    rt = _make_fake_runtime("red", status="completed")
    broker = worker_mod.ObservationBroker(rt, "run-1", "red")
    with pytest.raises(ValueError):
        broker.submit_observation({
            "kind": "detection",
            "data": {},
            "observed_at": "2024-01-01T00:00:00Z",
        })


def test_broker_real_store_integration(tmp_path: Path) -> None:
    from ai_yori_murasaki.runtime import Runtime
    from ai_yori_murasaki.storage import Store
    from ai_yori_murasaki.jsonio import digest

    secret = "super-secret-token"
    root = tmp_path / "runtime"
    trusted = tmp_path / "trusted"
    root.mkdir()
    trusted.mkdir()
    store = Store(root, trusted, secrets=(secret,))
    runtime = Runtime(store)
    scope = {"targets": [{"kind": "fixture", "identifier": "broker-test"}],
             "max_targets": 1}
    auth = {"operator_id": "op1", "granted_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope)}
    campaign = runtime.create_campaign("INT-TEST-BROKER", scope, auth, "broker-test")
    runtime.transition(campaign["campaign_id"], "running")
    run = runtime.start_run(campaign["campaign_id"], "red", 1, "red-test")
    run_id = run["run_id"]
    broker = worker_mod.ObservationBroker(runtime, run_id, "red")
    rec = broker.submit_observation({
        "kind": "http_response",
        "data": {"status": 200, "path": "/record", "response": secret},
        "observed_at": "2024-01-01T00:00:00Z",
    })
    assert rec["data_ref"]
    assert rec["data_sha256"]
    # original secret must not be stored anywhere in the runtime root
    for path in root.rglob("*"):
        if path.is_file():
            assert secret.encode("utf-8") not in path.read_bytes()
    events = store.read_events(run_id)
    ingested = [e for e in events if e.get("event_type") == "telemetry_ingested"]
    assert ingested, "telemetry_ingested event must be recorded"
    assert ingested[-1]["actor"]["role"] == "worker-red"
    # wrong phase rejected
    blue_broker = worker_mod.ObservationBroker(runtime, run_id, "blue")
    with pytest.raises(ValueError):
        blue_broker.submit_observation({
            "kind": "detection",
            "data": {},
            "observed_at": "2024-01-01T00:00:00Z",
        })
    # wrong authority rejected
    with pytest.raises(ValueError):
        worker_mod.ObservationBroker(runtime, run_id, "oracle")
