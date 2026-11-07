from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .jsonio import DataError
from .adapters.base import AdapterUnavailable, _run_once


# ---------------------------------------------------------------------------
# Docker profile API
# ---------------------------------------------------------------------------

_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_FILE_BYTES = 128 * 1024
_MAX_OUTPUT_BYTES = 1024 * 1024
_RUN_TIMEOUT = 90.0
_INSPECT_TIMEOUT = 30.0
_CREATE_TIMEOUT = 30.0
_RM_TIMEOUT = 30.0

_ALLOWLISTS = {
    "blue": frozenset({"detector.py", "target.py", "environment.json"}),
    "oracle": frozenset({"runner.py", "target.py", "tls_probe.py"}),
}

_ENTRYPOINTS = {
    "blue": "/app/detector.py",
    "oracle": "/app/runner.py",
}


def _reject_symlink_chain(path: Path) -> None:
    """Reject symlink/reparse points in the path and all ancestors."""
    try:
        p = path.absolute()
    except OSError as exc:
        raise DataError(f"cannot resolve stage path: {exc}") from exc
    parts: List[Path] = []
    cur = p
    while True:
        parts.append(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    for node in reversed(parts):
        try:
            st = os.lstat(node)
        except OSError as exc:
            raise DataError(f"cannot stat path component {node}: {exc}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise DataError(f"symlink in path component: {node}")
        if hasattr(st, "st_file_attributes"):
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if st.st_file_attributes & reparse:
                raise DataError(f"reparse point in path component: {node}")


def _validate_stage(stage: Path, mode: str) -> Path:
    if not isinstance(stage, Path):
        try:
            stage = Path(stage)
        except Exception as exc:
            raise DataError(f"invalid stage path: {exc}") from exc
    _reject_symlink_chain(stage)
    try:
        st = os.lstat(stage)
    except OSError as exc:
        raise DataError(f"cannot stat stage: {exc}") from exc
    if not stat.S_ISDIR(st.st_mode):
        raise DataError("stage is not a directory")
    if hasattr(st, "st_file_attributes"):
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if st.st_file_attributes & reparse:
            raise DataError("stage is a reparse point")

    allow = _ALLOWLISTS[mode]
    try:
        entries = list(os.scandir(stage))
    except OSError as exc:
        raise DataError(f"cannot list stage: {exc}") from exc

    seen = set()
    for entry in entries:
        name = entry.name
        if name not in allow:
            raise DataError(f"unexpected file in stage: {name}")
        if name in seen:
            raise DataError(f"duplicate file in stage: {name}")
        seen.add(name)
        try:
            est = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise DataError(f"cannot stat stage entry {name}: {exc}") from exc
        if stat.S_ISLNK(est.st_mode):
            raise DataError(f"symlink in stage: {name}")
        if hasattr(est, "st_file_attributes"):
            reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if est.st_file_attributes & reparse:
                raise DataError(f"reparse point in stage: {name}")
        if not stat.S_ISREG(est.st_mode):
            raise DataError(f"non-regular file in stage: {name}")
        if est.st_size > _MAX_FILE_BYTES:
            raise DataError(f"file too large: {name}")
        if est.st_size < 0:
            raise DataError(f"invalid file size: {name}")

    if seen != allow:
        missing = allow - seen
        raise DataError(f"missing required files: {sorted(missing)}")

    return stage


def _validate_image_id(image_id: str) -> str:
    if not isinstance(image_id, str) or not _IMAGE_RE.match(image_id):
        raise DataError("invalid image id")
    return image_id


def _validate_mode(mode: str) -> str:
    if mode not in _ALLOWLISTS:
        raise DataError("invalid mode")
    return mode


def _validate_deps(deps: str) -> str:
    if deps not in ("old", "fixed"):
        raise DataError("invalid deps")
    return deps


def _validate_seed(seed: int) -> int:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DataError("invalid seed")
    if seed < 0 or seed > 2147483647:
        raise DataError("seed out of range")
    return seed


def _mount_source(stage: Path) -> str:
    src = str(stage)
    if "," in src:
        raise DataError("comma in mount source path")
    return src


def _expected_mount_source(stage: Path) -> str:
    """Return the exact deterministic source string Docker will report."""
    src = str(stage)
    if os.name == "nt" or src.startswith("\\\\") or re.match(r"^[A-Za-z]:[\\/]", src):
        # Windows Docker Desktop maps host paths under /run/desktop/mnt/host/<drive>/...
        norm = src.replace("\\", "/")
        m = re.match(r"^([A-Za-z]):/(.*)$", norm)
        if m:
            drive = m.group(1).lower()
            rest = m.group(2)
            return f"/run/desktop/mnt/host/{drive}/{rest}"
    return src


def _parse_json_bytes(data: bytes, what: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise DataError(f"invalid JSON from {what}: {exc}") from exc


def _inspect_container(name: str) -> dict:
    res = _run_once(
        ["docker", "inspect", "--type", "container", name],
        _INSPECT_TIMEOUT,
        _MAX_OUTPUT_BYTES,
    )
    if res["returncode"] != 0:
        raise DataError("docker inspect failed")
    parsed = _parse_json_bytes(res["stdout"], "docker inspect")
    if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], dict):
        raise DataError("unexpected docker inspect shape")
    return parsed[0]


def _check_create_inspect(
    info: dict,
    image_id: str,
    stage: Path,
) -> None:
    if info.get("Image") != image_id:
        raise DataError("inspect image id mismatch")

    host = info.get("HostConfig") or {}
    if not isinstance(host, dict):
        raise DataError("inspect HostConfig missing")

    if host.get("NetworkMode") != "none":
        raise DataError("inspect network mode mismatch")
    if host.get("ReadonlyRootfs") is not True:
        raise DataError("inspect readonly rootfs mismatch")
    if host.get("Privileged") is not False:
        raise DataError("inspect privileged mismatch")

    if host.get("PidsLimit") != 128:
        raise DataError("inspect pids limit mismatch")
    if host.get("Memory") != 536870912:
        raise DataError("inspect memory mismatch")
    if host.get("NanoCpus") != 1000000000:
        raise DataError("inspect nano cpus mismatch")

    cap_drop = host.get("CapDrop")
    if not isinstance(cap_drop, list) or "ALL" not in cap_drop:
        raise DataError("inspect cap drop mismatch")
    cap_add = host.get("CapAdd")
    if cap_add not in (None, []):
        raise DataError("inspect cap add mismatch")

    sec = host.get("SecurityOpt")
    if not isinstance(sec, list) or "no-new-privileges:true" not in sec:
        raise DataError("inspect security opt mismatch")

    devices = host.get("Devices")
    if devices not in (None, []):
        raise DataError("inspect devices mismatch")
    dev_reqs = host.get("DeviceRequests")
    if dev_reqs not in (None, []):
        raise DataError("inspect device requests mismatch")

    port_bindings = host.get("PortBindings")
    if port_bindings not in (None, {}):
        raise DataError("inspect port bindings mismatch")

    binds = host.get("Binds")
    if binds not in (None, []):
        raise DataError("inspect unexpected binds")

    mounts = info.get("Mounts")
    if not isinstance(mounts, list) or len(mounts) != 1:
        raise DataError("inspect mount count mismatch")
    mount = mounts[0]
    if not isinstance(mount, dict):
        raise DataError("inspect mount shape mismatch")
    if mount.get("Type") != "bind":
        raise DataError("inspect mount type mismatch")
    if mount.get("Destination") != "/app":
        raise DataError("inspect mount destination mismatch")
    if mount.get("RW") is not False:
        raise DataError("inspect mount not readonly")

    expected_src = _expected_mount_source(stage)
    exact_src = str(stage)
    actual_src = mount.get("Source")
    if actual_src not in (exact_src, expected_src):
        raise DataError("inspect mount source mismatch")

    cfg = info.get("Config") or {}
    if not isinstance(cfg, dict):
        raise DataError("inspect Config missing")
    if cfg.get("User") != "65534:65534":
        raise DataError("inspect user mismatch")
    if cfg.get("WorkingDir") != "/app":
        raise DataError("inspect workdir mismatch")

    state = info.get("State") or {}
    if not isinstance(state, dict):
        raise DataError("inspect State missing")
    if state.get("Running") is True:
        raise DataError("container already running before start")


def run_fixture(
    stage: Path,
    image_id: str,
    mode: str,
    seed: int = 1,
    deps: str = "old",
) -> dict:
    mode = _validate_mode(mode)
    image_id = _validate_image_id(image_id)
    seed = _validate_seed(seed)
    deps = _validate_deps(deps)
    stage = _validate_stage(stage, mode)
    try:
        stage = Path(os.path.realpath(str(stage)))
    except OSError as exc:
        raise DataError(f"cannot canonicalize stage path: {exc}") from exc
    src = _mount_source(stage)

    container_name = "aym-assess-" + uuid.uuid4().hex
    created = False
    started = False
    start_time = time.monotonic()

    entry = _ENTRYPOINTS[mode]
    if mode == "blue":
        cmd = ["python", entry]
    else:
        cmd = ["python", entry, "--mode", "oracle", "--seed", str(seed), "--deps", deps]

    create_argv = [
        "docker", "create",
        "--name", container_name,
        "--network", "none",
        "--read-only",
        "--user", "65534:65534",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--pids-limit", "128",
        "--memory", "512m",
        "--cpus", "1",
        "--mount", f"type=bind,src={src},dst=/app,readonly",
        "--workdir", "/app",
        "--shm-size", "128m",
        image_id,
    ] + cmd

    try:
        try:
            create_res = _run_once(create_argv, _CREATE_TIMEOUT, _MAX_OUTPUT_BYTES)
        except AdapterUnavailable:
            # Create may have succeeded before timeout; mark for cleanup of our own name.
            created = True
            raise
        if create_res["returncode"] != 0:
            raise DataError("docker create failed")
        created = True

        info = _inspect_container(container_name)
        _check_create_inspect(info, image_id, stage)

        start_res = _run_once(
            ["docker", "start", "-a", container_name],
            _RUN_TIMEOUT,
            _MAX_OUTPUT_BYTES,
        )
        started = True
        duration = time.monotonic() - start_time

        if start_res["returncode"] != 0:
            raise DataError("container exited nonzero")

        output = _parse_json_bytes(start_res["stdout"], "container output")

        post = _inspect_container(container_name)
        state = post.get("State") or {}
        if not isinstance(state, dict):
            raise DataError("post-run state missing")
        if state.get("OOMKilled") is True:
            raise DataError("container OOM killed")
        exit_code = state.get("ExitCode")
        if exit_code != 0:
            raise DataError("container exit code nonzero")
        err = state.get("Error")
        if err not in (None, ""):
            raise DataError("container reported error")

        return {
            "output": output,
            "isolation": {
                "image_id": image_id,
                "network": "none",
                "user": "65534:65534",
                "readonly": True,
                "mounts_verified": True,
            },
            "execution": {
                "duration_seconds": duration,
                "exit_code": exit_code,
                "oom_killed": False,
            },
        }
    except AdapterUnavailable as exc:
        raise DataError(f"docker adapter unavailable: {exc}") from exc
    finally:
        if created or started:
            try:
                _run_once(
                    ["docker", "rm", "-f", container_name],
                    _RM_TIMEOUT,
                    _MAX_OUTPUT_BYTES,
                )
            except Exception:
                pass
