"""V1 transaction tests: per-path OS lock depth, multiprocessing appends, atomic_json."""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_yori_murasaki import storage  # noqa: E402
from ai_yori_murasaki.storage import Store  # noqa: E402
from tests.test_v1_storage import _event as valid_event


SCHEMA = "1.0.0"


def _event(i: int, run_id: str = "run-default") -> dict:
    return valid_event(run_id, i, extensions={"sample": i})


def _mp_worker(ws: str, tr: str, run_id: str, count: int, start: int) -> None:
    store = Store(ws, tr)
    for i in range(count):
        store.append_event(run_id, _event(start + i, run_id))


def _make_roots(base: Path, name: str) -> tuple[Path, Path]:
    ws = base / name / "ws"
    tr = base / name / "tr"
    ws.mkdir(parents=True)
    tr.mkdir(parents=True)
    return ws, tr


class LockDepthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_same_workspace_nested_acquires_os_lock_once(self) -> None:
        ws, tr = _make_roots(self.base, "same")
        a = Store(ws, tr)
        b = Store(ws, tr)
        calls = []
        orig = Store._acquire_os_lock

        def spy(self_, deadline):
            calls.append(str(self_._lockfile))
            return orig(self_, deadline)

        with mock.patch.object(Store, "_acquire_os_lock", spy):
            with a.lock():
                with b.lock():
                    pass
        self.assertEqual(len(calls), 1)

    def test_different_workspace_nested_acquires_both_os_locks(self) -> None:
        ws1, tr1 = _make_roots(self.base, "one")
        ws2, tr2 = _make_roots(self.base, "two")
        a = Store(ws1, tr1)
        b = Store(ws2, tr2)
        calls = []
        orig = Store._acquire_os_lock

        def spy(self_, deadline):
            calls.append(str(self_._lockfile))
            return orig(self_, deadline)

        with mock.patch.object(Store, "_acquire_os_lock", spy):
            with a.lock():
                with b.lock():
                    pass
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])

    def test_threads_two_stores_each_ten_appends(self) -> None:
        ws, tr = _make_roots(self.base, "threads")
        run_id = "run-threads"
        errors: list[BaseException] = []

        def worker(start: int) -> None:
            try:
                store = Store(ws, tr)
                for i in range(10):
                    store.append_event(run_id, _event(start + i, run_id))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(0,)),
                   threading.Thread(target=worker, args=(10,))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)
        self.assertFalse(errors, errors)
        store = Store(ws, tr)
        events = store.read_events(run_id)
        self.assertEqual(len(events), 20)
        self.assertEqual(store.verify_chain(run_id)["result"], "PASS")


class MultiprocessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_two_processes_ten_appends_each(self) -> None:
        ws, tr = _make_roots(self.base, "mp")
        run_id = "run-mp"
        ctx = mp.get_context("spawn")
        procs = [ctx.Process(target=_mp_worker, args=(str(ws), str(tr), run_id, 10, 0)),
                 ctx.Process(target=_mp_worker, args=(str(ws), str(tr), run_id, 10, 10))]
        deadline = time.monotonic() + 25
        try:
            for p in procs:
                p.start()
            for p in procs:
                remaining = max(0.1, deadline - time.monotonic())
                p.join(timeout=remaining)
            for p in procs:
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=5)
                    self.fail("child process did not exit in time")
                self.assertEqual(p.exitcode, 0)
        finally:
            for p in procs:
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=5)
        store = Store(ws, tr)
        events = store.read_events(run_id)
        self.assertEqual(len(events), 20)
        self.assertEqual([e["sequence"] for e in events], list(range(1, 21)))
        self.assertEqual(store.verify_chain(run_id)["result"], "PASS")


class AtomicJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_os_replace_failure_preserves_original_and_removes_temp(self) -> None:
        ws, tr = _make_roots(self.base, "atomic")
        store = Store(ws, tr)
        target = ws / "catalog.json"
        store.atomic_json(target, {"schema_version": SCHEMA, "runs": {}})
        original = target.read_bytes()
        before = set(p.name for p in target.parent.iterdir())
        with mock.patch.object(storage.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                store.atomic_json(target, {"schema_version": SCHEMA, "runs": {"x": {}}})
        self.assertEqual(target.read_bytes(), original)
        after = set(p.name for p in target.parent.iterdir())
        self.assertEqual(before, after)
        self.assertFalse(any(n.endswith(".tmp") for n in after))


if __name__ == "__main__":
    unittest.main()
