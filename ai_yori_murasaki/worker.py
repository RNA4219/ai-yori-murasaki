"""Docker lab orchestrator wrapper and observation broker (v1).

Owns: DockerLab (disposable isolated lab lifecycle) and ObservationBroker
(role-bound, schema-validated observation intake). All docker execution
goes through adapters.base.run_docker with finite argv; no shell.

DockerLab:
  - strict absolute staging_root outside the project tree
  - unique aym-* internal network created via base.run_docker
  - single default image unless an explicit approved image list is bound
  - target launched in a background ThreadPoolExecutor via run_docker
  - finite health wait (<=20s) via a fresh probe worker
  - stop/cleanup only own names; inspect network labels/internal first
  - never deletes user files

ObservationBroker:
  - binds role/phase (red / blue_blind)
  - submit_observation accepts only kind/data/observed_at
  - kind enum: http_response/detection/telemetry/test_result
  - rejects forbidden nested authority keys
  - data cap 64KiB, observed_at must be a valid timestamp
  - trusted runtime creates CAS + record; worker output never controls
    final verdict or state
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .adapters import base as base_mod


DEFAULT_IMAGE = "ai-yori-murasaki-lab:1"

_NAME_RE = re.compile(r"^aym-[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_HEALTH_TIMEOUT = 20.0
_TARGET_TIMEOUT = 240.0
_DOCKER_TIMEOUT = 30.0
_MAX_BYTES = 1048576

_ROLE_MODES: Dict[str, Tuple[str, ...]] = {
    "blue": ("blue",),
    "red": ("red", "probe"),
    "oracle": ("benign", "probe", "unit"),
}

_OBS_KINDS = {"http_response", "detection", "telemetry", "test_result"}
_OBS_KEYS = {"kind", "data", "observed_at"}
_OBS_FORBIDDEN = {
    "actor", "agent_id", "sequence", "hash", "approval", "source",
    "event_type",
}
_OBS_DATA_CAP = 64 * 1024

_ROLE_PHASES: Dict[str, str] = {
    "red": "red",
    "blue": "blue_blind",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return "{}-{}".format(prefix, secrets.token_hex(8))


def _is_absolute(p: str) -> bool:
    if not isinstance(p, str) or not p:
        return False
    if p.startswith("/"):
        return True
    try:
        return Path(p).is_absolute()
    except (ValueError, OSError):
        return False


def _has_dot_segments(p: str) -> bool:
    norm = p.replace("\\", "/")
    return ".." in norm.split("/")


def _project_root() -> str:
    return os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _validate_staging_root(staging_root: Path) -> str:
    if not isinstance(staging_root, Path):
        raise ValueError("staging_root must be a pathlib.Path")
    raw = str(staging_root)
    if not _is_absolute(raw):
        raise ValueError("staging_root must be absolute")
    if _has_dot_segments(raw):
        raise ValueError("staging_root must not contain dot segments")
    real = os.path.realpath(raw)
    root = _project_root()
    try:
        common = os.path.commonpath([root, real])
    except ValueError as exc:
        raise ValueError("staging_root outside project") from exc
    if common == root:
        raise ValueError("staging_root must be outside the project tree")
    return real


def _validate_image(image: str, approved: Optional[Iterable[str]]) -> str:
    if not isinstance(image, str) or not image:
        raise ValueError("image must be non-empty string")
    if approved is None:
        if image != DEFAULT_IMAGE:
            raise ValueError("image not approved: {!r}".format(image))
        return image
    allowed = set(approved)
    if image not in allowed:
        raise ValueError("image not approved: {!r}".format(image))
    return image


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError("invalid lab name: {!r}".format(name))
    return name


def _decode_output(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, bytearray):
        return bytes(value).decode("utf-8", "replace")
    if isinstance(value, str):
        return value
    return str(value)


def _parse_worker_json(stdout: str) -> Dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise ValueError("worker produced no output")
    line = text.splitlines()[-1]
    try:
        doc = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError("worker output is not JSON") from exc
    if not isinstance(doc, dict):
        raise ValueError("worker output must be a JSON object")
    return doc


# ---------------------------------------------------------------------------
# DockerLab
# ---------------------------------------------------------------------------


class DockerLab:
    """Disposable isolated Docker lab orchestrator.

    Use as a context manager. All docker calls go through
    adapters.base.run_docker with finite argv and allowed names/images.
    """

    def __init__(self, staging_root: Path, image: str = DEFAULT_IMAGE,
                 approved_images: Optional[Iterable[str]] = None) -> None:
        self._staging_root = _validate_staging_root(staging_root)
        if approved_images is None:
            self._approved_images: Optional[List[str]] = None
            self._image = _validate_image(image, None)
        else:
            approved = [str(x) for x in approved_images]
            self._approved_images = approved
            self._image = _validate_image(image, approved)
        self._lab_id = _new_id("lab")
        self._network = _validate_name("aym-net-{}".format(self._lab_id))
        self._target_name = _validate_name("aym-tgt-{}".format(self._lab_id))
        self._own_names: List[str] = [self._target_name]
        self._network_created = False
        self._target_future: Optional[Future] = None
        self._target_host: Optional[str] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()
        self._closed = False
        self._cleanup_errors: List[BaseException] = []

    # -- properties -------------------------------------------------------

    @property
    def network(self) -> str:
        return self._network

    @property
    def image(self) -> str:
        return self._image

    @property
    def staging_root(self) -> str:
        return self._staging_root

    # -- docker helpers ---------------------------------------------------

    def _run(self, argv: List[str], timeout: float = _DOCKER_TIMEOUT) -> Dict[str, Any]:
        return base_mod.run_docker(
            argv,
            timeout=timeout,
            max_bytes=_MAX_BYTES,
            retry_cap=0,
            staging_root=self._staging_root,
            allowed_images=self._approved_images if self._approved_images is not None else [DEFAULT_IMAGE],
            allowed_names=list(self._own_names) + [self._network],
        )

    def _create_network(self) -> None:
        argv = [
            "docker", "network", "create",
            "--internal",
            "--label", "ai-yori-murasaki=lab",
            self._network,
        ]
        res = self._run(argv)
        if res.get("returncode") != 0:
            raise RuntimeError("network create failed: {}".format(
                _decode_output(res.get("stderr"))))
        self._network_created = True

    def _inspect_network(self) -> Dict[str, Any]:
        argv = ["docker", "network", "inspect", self._network]
        res = self._run(argv)
        if res.get("returncode") != 0:
            raise RuntimeError("network inspect failed: {}".format(
                _decode_output(res.get("stderr"))))
        text = _decode_output(res.get("stdout")).strip()
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("network inspect output not JSON") from exc
        if isinstance(doc, list):
            if not doc:
                raise RuntimeError("network inspect returned empty list")
            doc = doc[0]
        if not isinstance(doc, dict):
            raise RuntimeError("network inspect output malformed")
        if doc.get("Name") != self._network:
            raise RuntimeError("network inspect name mismatch")
        if doc.get("Internal") is not True:
            raise RuntimeError("network inspect not internal")
        labels = doc.get("Labels")
        if not isinstance(labels, dict) or labels.get("ai-yori-murasaki") != "lab":
            raise RuntimeError("network inspect label mismatch")
        return doc

    def _remove_network(self) -> None:
        argv = ["docker", "network", "rm", self._network]
        try:
            self._run(argv)
        except Exception:
            pass

    def _stop_container(self, name: str) -> None:
        try:
            self._run(["docker", "stop", name])
        except Exception:
            pass
        try:
            self._run(["docker", "rm", name])
        except Exception:
            pass

    # -- lifecycle --------------------------------------------------------

    def __enter__(self) -> "DockerLab":
        try:
            self._create_network()
        except Exception:
            self._network_created = False
            raise
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aym-lab")
        return self

    @property
    def cleanup_errors(self) -> tuple:
        return tuple("{}: {}".format(type(e).__name__, e)
                     for e in self._cleanup_errors)

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            self.close()
        except BaseException:  # noqa: BLE001
            if exc_type is None:
                raise
        return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        original: Optional[BaseException] = None
        try:
            self.stop_target()
        except BaseException as exc:  # noqa: BLE001
            original = exc
            self._cleanup_errors.append(exc)
        try:
            if self._executor is not None:
                self._executor.shutdown(wait=False)
        except BaseException as exc:  # noqa: BLE001
            if original is None:
                original = exc
            self._cleanup_errors.append(exc)
        finally:
            self._executor = None
        if self._network_created:
            try:
                self._inspect_network()
            except BaseException as exc:  # noqa: BLE001
                if original is None:
                    original = exc
                self._cleanup_errors.append(exc)
            else:
                try:
                    self._remove_network()
                except BaseException as exc:  # noqa: BLE001
                    if original is None:
                        original = exc
                    self._cleanup_errors.append(exc)
            self._network_created = False
        if original is not None:
            raise original

    # -- target -----------------------------------------------------------

    def _build_target_argv(self, staged_source: str) -> List[str]:
        plan = {
            "id": self._target_name.removeprefix("aym-"),
            "image": self._image,
            "network": self._network,
            "staged_source": staged_source,
            "script": "/app/target.py",
            "args": [],
        }
        return base_mod._build_docker_argv_from_plan(plan)

    def _launch_target(self, staged_source: str) -> Dict[str, Any]:
        argv = self._build_target_argv(staged_source)
        return self._run(argv, timeout=_TARGET_TIMEOUT)

    def start_target(self, source_dir: Path) -> str:
        """Launch the target container in the background; return hostname."""
        if self._closed:
            raise RuntimeError("lab is closed")
        if self._target_future is not None:
            raise RuntimeError("target already started")
        if not isinstance(source_dir, Path):
            raise ValueError("source_dir must be a pathlib.Path")
        raw = str(source_dir)
        if not _is_absolute(raw):
            raise ValueError("source_dir must be absolute")
        if _has_dot_segments(raw):
            raise ValueError("source_dir must not contain dot segments")
        real = os.path.realpath(raw)
        try:
            common = os.path.commonpath([self._staging_root, real])
        except ValueError as exc:
            raise ValueError("source_dir outside staging_root") from exc
        if common != self._staging_root:
            raise ValueError("source_dir outside staging_root")
        if not os.path.isdir(real):
            raise ValueError("source_dir does not exist")
        if self._executor is None:
            raise RuntimeError("lab not entered")
        self._target_host = self._target_name
        self._target_future = self._executor.submit(self._launch_target, real)
        return self._target_host

    def _probe_health(self, timeout: float = _DOCKER_TIMEOUT) -> bool:
        if self._target_host is None:
            return False
        try:
            stage = stage_worker(Path(self._staging_root), "oracle")
        except Exception:
            return False
        plan = {
            "id": _new_id("probe"),
            "image": self._image,
            "network": self._network,
            "staged_source": str(stage),
            "script": "/app/worker.py",
            "args": ["probe", "--host", self._target_host],
        }
        argv = base_mod._build_docker_argv_from_plan(plan)
        probe_name = argv[argv.index("--name") + 1]
        self._own_names.append(probe_name)
        try:
            res = self._run(argv, timeout=timeout)
        finally:
            try:
                self._own_names.remove(probe_name)
            except ValueError:
                pass
        if res.get("returncode") != 0:
            return False
        try:
            doc = _parse_worker_json(_decode_output(res.get("stdout")))
        except ValueError:
            return False
        return doc.get("status") == "ok"

    def wait_healthy(self, timeout: float = _HEALTH_TIMEOUT) -> bool:
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("timeout must be positive")
        if timeout > _HEALTH_TIMEOUT:
            timeout = _HEALTH_TIMEOUT
        deadline = time.monotonic() + float(timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            probe_timeout = min(remaining, 10.0)
            if self._probe_health(timeout=probe_timeout):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    def stop_target(self) -> None:
        with self._lock:
            future = self._target_future
            self._target_future = None
            host = self._target_host
            self._target_host = None
        if self._target_name in self._own_names:
            self._stop_container(self._target_name)
        if future is not None:
            try:
                future.result(timeout=10.0)
            except BaseException:  # noqa: BLE001
                pass
        _ = host

    # -- telemetry --------------------------------------------------------

    def telemetry(self) -> List[Dict[str, Any]]:
        """Return sanitized telemetry rows from docker logs of the target."""
        if self._target_name not in self._own_names:
            return []
        res = self._run(["docker", "logs", self._target_name])
        if res.get("returncode") != 0:
            return []
        text = _decode_output(res.get("stdout"))
        rows: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            rows.append(_sanitize_telemetry_row(row))
        return rows

    # -- worker -----------------------------------------------------------

    def run_worker(self, source_dir: Path, role: str, mode: str,
                   args: Tuple[str, ...] = ()) -> Dict[str, Any]:
        """Run worker.py in a network-none container; return parsed JSON."""
        if self._closed:
            raise RuntimeError("lab is closed")
        if not isinstance(role, str) or role not in _ROLE_MODES:
            raise ValueError("unknown role: {!r}".format(role))
        if not isinstance(mode, str) or mode not in _ROLE_MODES[role]:
            raise ValueError("mode {!r} not allowed for role {!r}".format(mode, role))
        if not isinstance(args, tuple):
            raise ValueError("args must be a tuple")
        if args:
            raise ValueError("args must be empty")
        for a in args:
            if not isinstance(a, str) or "\x00" in a:
                raise ValueError("args entries must be strings")
        if not isinstance(source_dir, Path):
            raise ValueError("source_dir must be a pathlib.Path")
        raw = str(source_dir)
        if not _is_absolute(raw):
            raise ValueError("source_dir must be absolute")
        if _has_dot_segments(raw):
            raise ValueError("source_dir must not contain dot segments")
        real = os.path.realpath(raw)
        try:
            common = os.path.commonpath([self._staging_root, real])
        except ValueError as exc:
            raise ValueError("source_dir outside staging_root") from exc
        if common != self._staging_root:
            raise ValueError("source_dir outside staging_root")
        if not os.path.isdir(real):
            raise ValueError("source_dir does not exist")
        worker_args: List[str] = [mode]
        if mode in ("probe", "red", "benign"):
            if self._target_host is None:
                raise RuntimeError("target not started")
            worker_args.extend(["--host", self._target_host])
        worker_args.extend(args)
        if role == "blue":
            stage_name = os.path.basename(real)
            if not stage_name.startswith("stage-blue-"):
                raise ValueError("blue source must be stage-blue-*")
            entries = sorted(os.listdir(real))
            if entries not in (["worker.py"], ["telemetry.json", "worker.py"]):
                raise ValueError("blue source must contain only worker.py and telemetry.json")
            worker_path = os.path.join(real, "worker.py")
            if os.path.islink(worker_path) or not os.path.isfile(worker_path):
                raise ValueError("blue worker.py must be a regular file")
            if os.path.islink(real):
                raise ValueError("blue source must not be a link")
            asset = Path(__file__).resolve().parent / "lab_assets" / "worker.py"
            with open(worker_path, "rb") as fh:
                actual = hashlib.sha256(fh.read()).hexdigest()
            with open(str(asset), "rb") as fh:
                expected = hashlib.sha256(fh.read()).hexdigest()
            if actual != expected:
                raise ValueError("blue worker.py hash mismatch")
            network = "none"
        elif mode in ("probe", "red", "benign"):
            network = self._network
        else:
            network = "none"
        plan = {
            "id": _new_id("wrk"),
            "image": self._image,
            "network": network,
            "staged_source": real,
            "script": "/app/worker.py",
            "args": worker_args,
        }
        argv = base_mod._build_docker_argv_from_plan(plan)
        worker_name = argv[argv.index("--name") + 1]
        self._own_names.append(worker_name)
        try:
            res = self._run(argv, timeout=_TARGET_TIMEOUT)
        finally:
            try:
                self._own_names.remove(worker_name)
            except ValueError:
                pass
        if res.get("returncode") != 0:
            raise RuntimeError("worker failed: {}".format(
                _decode_output(res.get("stderr"))))
        return _parse_worker_json(_decode_output(res.get("stdout")))


# ---------------------------------------------------------------------------
# staging
# ---------------------------------------------------------------------------


_TELEMETRY_FIELDS = ("timestamp", "method", "path", "status", "role")
_SECRET_QUERY_RE = re.compile(
    r"(?i)(token|secret|password|passwd|key|auth|session|sig|signature)=")


def _sanitize_telemetry_row(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for field in _TELEMETRY_FIELDS:
        if field in row:
            out[field] = row[field]
    path = out.get("path")
    if isinstance(path, str):
        if "?" in path:
            path = path.split("?", 1)[0]
        out["path"] = path
    return out


def stage_worker(root: Path, role: str, telemetry: Optional[List[Dict[str, Any]]] = None) -> Path:
    """Create a staging dir containing only worker.py plus projected telemetry.

    Returns the staged directory path. The directory is created under root
    and contains exactly worker.py and (optionally) telemetry.json.
    """
    if not isinstance(root, Path):
        raise ValueError("root must be a pathlib.Path")
    raw = str(root)
    if not _is_absolute(raw):
        raise ValueError("root must be absolute")
    if _has_dot_segments(raw):
        raise ValueError("root must not contain dot segments")
    if not isinstance(role, str) or role not in _ROLE_MODES:
        raise ValueError("unknown role: {!r}".format(role))
    real_root = os.path.realpath(raw)
    os.makedirs(real_root, exist_ok=True)
    stage = Path(real_root) / _new_id("stage-{}".format(role))
    os.makedirs(str(stage), exist_ok=False)
    worker_src = Path(__file__).resolve().parent / "lab_assets" / "worker.py"
    worker_dst = stage / "worker.py"
    with open(str(worker_src), "rb") as fh:
        data = fh.read()
    with open(str(worker_dst), "wb") as fh:
        fh.write(data)
    if telemetry is not None:
        if not isinstance(telemetry, list):
            raise ValueError("telemetry must be a list")
        lines: List[str] = []
        for row in telemetry:
            if not isinstance(row, dict):
                raise ValueError("telemetry rows must be objects")
            lines.append(json.dumps(_sanitize_telemetry_row(row),
                                    separators=(",", ":"), sort_keys=True))
        with open(str(stage / "telemetry.json"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
            if lines:
                fh.write("\n")
    return stage


# ---------------------------------------------------------------------------
# ObservationBroker
# ---------------------------------------------------------------------------


class ObservationBroker:
    """Role-bound observation intake.

    The trusted runtime creates the CAS object and the evidence record; the
    worker output never controls the final verdict or state.
    """

    def __init__(self, runtime: Any, run_id: str, role: str) -> None:
        if not isinstance(run_id, str) or not _ID_RE.match(run_id):
            raise ValueError("invalid run_id")
        if not isinstance(role, str) or role not in _ROLE_PHASES:
            raise ValueError("unknown role: {!r}".format(role))
        self._runtime = runtime
        self._run_id = run_id
        self._role = role
        self._phase = _ROLE_PHASES[role]

    @property
    def role(self) -> str:
        return self._role

    @property
    def phase(self) -> str:
        return self._phase

    def submit_observation(self, observation: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(observation, dict):
            raise ValueError("observation must be an object")
        if set(observation.keys()) != _OBS_KEYS:
            raise ValueError("observation keys must be exactly {}".format(
                sorted(_OBS_KEYS)))
        kind = observation["kind"]
        if not isinstance(kind, str) or kind not in _OBS_KINDS:
            raise ValueError("unknown observation kind: {!r}".format(kind))
        data = observation["data"]
        _reject_forbidden(data)
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                             sort_keys=True, allow_nan=False).encode("utf-8")
        if len(encoded) > _OBS_DATA_CAP:
            raise ValueError("observation data exceeds 64KiB")
        observed_at = observation["observed_at"]
        if not _valid_timestamp(observed_at):
            raise ValueError("observed_at must be a valid timestamp")
        run = self._runtime.get_run(self._run_id)
        if run is None:
            raise ValueError("unknown run_id")
        run_phase = getattr(run, "phase", None)
        if run_phase is None and isinstance(run, dict):
            run_phase = run.get("phase")
        if run_phase != self._phase:
            raise ValueError("run phase mismatch")
        run_status = getattr(run, "status", None)
        if run_status is None and isinstance(run, dict):
            run_status = run.get("status")
        if run_status != "running":
            raise ValueError("run not running")
        digest = hashlib.sha256(encoded).hexdigest()
        cas_ref = self._runtime.store.put_object({
            "kind": kind,
            "observed_at": observed_at,
            "data": data,
        })
        if not isinstance(cas_ref, str) or not cas_ref:
            raise RuntimeError("store.put_object returned invalid ref")
        self._runtime.record_event(
            self._run_id,
            "telemetry_ingested",
            "orchestrator",
            evidence_refs=(cas_ref,),
            extensions={"observation_kind": kind},
        )
        return {
            "run_id": self._run_id,
            "role": self._role,
            "phase": self._phase,
            "kind": kind,
            "observed_at": observed_at,
            "data_ref": cas_ref,
            "data_sha256": digest,
        }


def _reject_forbidden(value: Any) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k in _OBS_FORBIDDEN:
                raise ValueError("forbidden observation key: {!r}".format(k))
            _reject_forbidden(v)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden(item)


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True
