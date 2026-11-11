"""Runtime: campaign/run lifecycle, state machine, crash resume, oracle recording.

Owns: create_campaign, start_run, record_event, get_run/list_runs,
get_campaign/list_campaigns, finish_run, seal_run, transition,
resume_after_crash, record_oracle, register_record, list_records.

All state guards are computed from the real ledger/CAS, never from
client-supplied guard_ctx self-claims.  Actor/role is resolved from phase
via a trusted fixed mapping; client actor/seq/hash/approval are rejected.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    ContractError,
    derive_remediation_success,
    semantic_validate,
    validate,
)
from .jsonio import DataError, canonical, digest
from .storage import Store

PHASES = ("red", "blue_blind", "purple", "remediation", "reevaluation", "verify")
PHASE_ROLE = {
    "red": "worker-red",
    "blue_blind": "worker-blue",
    "purple": "orchestrator",
    "remediation": "orchestrator",
    "reevaluation": "oracle",
    "verify": "auditor",
}
TERMINAL = {"completed", "incomplete", "cancelled", "adapter_unavailable"}
CAUSES = {"none", "policy_denied", "adapter_unavailable", "crash", "timeout",
          "evidence_missing", "evidence_mismatch", "not_executed"}

# campaign state machine: from -> {to: guard_name}
TRANSITIONS: dict[str, dict[str, str]] = {
    "created": {"running": "scope_validated"},
    "running": {"blind_completed": "blind_completed",
                "incomplete": "any_incomplete",
                "cancelled": "any_cancelled"},
    "blind_completed": {"purple_open": "blind_sealed_evaluated",
                        "incomplete": "any_incomplete",
                        "cancelled": "any_cancelled"},
    "purple_open": {"remediation_in_progress": "patch_drafted",
                    "incomplete": "any_incomplete",
                    "cancelled": "any_cancelled"},
    "remediation_in_progress": {"review_pending": "remediation_ready",
                                "incomplete": "any_incomplete",
                                "cancelled": "any_cancelled"},
    "review_pending": {"completed": "approval_approved",
                       "incomplete": "any_incomplete",
                       "cancelled": "any_cancelled"},
}

# phase -> allowed campaign statuses for public start_run
PHASE_STATUS = {
    "red": {"running"},
    "blue_blind": {"running"},
    "purple": {"purple_open"},
    "remediation": {"remediation_in_progress"},
    "reevaluation": {"remediation_in_progress"},
    "verify": {"created", "running", "blind_completed", "purple_open",
               "remediation_in_progress", "review_pending", "completed",
               "incomplete", "cancelled"},
}

# phase -> required prior phase completion (sealed) for public start_run
PHASE_PRIOR = {
    "red": None,
    "blue_blind": ("red", True),
    "purple": ("blue_blind", True),
    "remediation": ("purple", True),
    "reevaluation": ("remediation", True),
    "verify": None,
}

RECORD_KINDS = ("finding", "remediation", "approval", "final_verdict",
                "oracle_result", "test_report")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:16]


def _input_digest(payload: Any) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def _idem_digest(kind: str, key: str) -> str:
    return hashlib.sha256((kind + "\x00" + key).encode("utf-8")).hexdigest()


class Runtime:
    def __init__(self, store: Store, policy: dict | None = None) -> None:
        self.store = store
        self.policy = policy or {}

    # ------------------------------------------------------------------ #
    # idempotency (persisted under workspace/idempotency.json)
    # ------------------------------------------------------------------ #
    def _idem_path(self):
        return self.store.workspace / "idempotency.json"

    def _load_idem(self) -> dict:
        path = self._idem_path()
        if not path.exists():
            return {}
        from .jsonio import loads
        value = loads(path.read_bytes())
        if not isinstance(value, dict):
            raise DataError("idempotency index corrupt", "hold")
        return value

    def _check_idem(self, kind: str, key: str, payload: Any) -> dict | None:
        """Return prior result if same key+digest; reject changed payload.

        Must be called under store.lock() so check+mutation+save is atomic.
        """
        if not isinstance(key, str) or not key:
            raise DataError("idempotency_key required", "input_invalid")
        index = self._load_idem()
        entry = index.get(_idem_digest(kind, key))
        if entry is None:
            return None
        if entry.get("input_digest") != _input_digest(payload):
            raise DataError("idempotency key reused with changed payload",
                            "policy_denied")
        result_ref = entry.get("result_ref")
        if not isinstance(result_ref, str):
            raise DataError("idempotency entry incomplete", "hold")
        try:
            return self.store.get_object(result_ref)
        except (DataError, FileNotFoundError) as exc:
            raise DataError("idempotency result missing (incomplete)", "hold") from exc

    def _save_idem(self, kind: str, key: str, payload: Any, result: dict) -> None:
        """Persist idempotency entry with CAS result.  Caller holds lock."""
        result_ref = self.store.put_object(result)
        index = self._load_idem()
        index[_idem_digest(kind, key)] = {
            "input_digest": _input_digest(payload),
            "result_ref": result_ref,
        }
        self.store.atomic_json(self._idem_path(), index)

    # ------------------------------------------------------------------ #
    # campaign
    # ------------------------------------------------------------------ #
    def create_campaign(self, intent_id: str, scope: dict, authorization: dict,
                        idempotency_key: str) -> dict:
        payload = {"intent_id": intent_id, "scope": scope,
                   "authorization": authorization}
        with self.store.lock():
            prior = self._check_idem("campaign", idempotency_key, payload)
            if prior is not None:
                return prior
            campaign_id = _new_id("c")
            created_at = _now()
            campaign = {
                "schema_version": "1.0.0",
                "campaign_id": campaign_id,
                "intent_id": intent_id,
                "scope": scope,
                "authorization": authorization,
                "created_at": created_at,
                "operator_id": authorization.get("operator_id"),
                "status": "created",
            }
            validate("campaign", campaign)
            semantic_validate("campaign", campaign,
                               {"get_object": self.store.get_object})
            # private bootstrap: initial audit run in verify phase
            run = self._bootstrap_run(campaign_id, "verify", 0,
                                      f"init:{campaign_id}", campaign)
            self.record_event(run["run_id"], "scope_validated", "orchestrator",
                              extensions={"campaign_snapshot": campaign,
                                          "run_snapshot": run})
            self.finish_run(run["run_id"], "completed")
            self.seal_run(campaign_id, run["run_id"])
            self._save_idem("campaign", idempotency_key, payload, campaign)
            return campaign

    def _campaign_snapshots(self) -> list[tuple[str, dict]]:
        """Collect (event_timestamp, campaign_snapshot) from verified ledgers.

        Snapshots are read from events on chains that pass hash verification;
        the catalog projection is not trusted.  The event timestamp is used
        for recency ordering so Campaign.created_at is never mutated.
        """
        snaps: list[tuple[str, dict]] = []
        runs_dir = self.store.workspace / "runs"
        if not runs_dir.exists():
            return snaps
        for child in sorted(runs_dir.iterdir()):
            if not child.is_dir():
                continue
            run_id = child.name
            chain = self.store.verify_chain(run_id)
            if chain.get("result") != "PASS":
                continue
            try:
                events = self.store.read_events(run_id)
            except DataError:
                continue
            for event in events:
                ext = event.get("extensions") or {}
                if not isinstance(ext, dict):
                    continue
                snap = ext.get("campaign_snapshot")
                if not isinstance(snap, dict):
                    continue
                ts = event.get("timestamp")
                if not isinstance(ts, str):
                    continue
                snaps.append((ts, snap))
        return snaps

    def get_campaign(self, campaign_id: str) -> dict:
        snaps = [(ts, s) for ts, s in self._campaign_snapshots()
                 if s.get("campaign_id") == campaign_id]
        if not snaps:
            raise DataError("campaign not found", "input_invalid")
        snaps.sort(key=lambda pair: pair[0])
        return snaps[-1][1]

    def list_campaigns(self) -> list[dict]:
        latest: dict[str, tuple[str, dict]] = {}
        for ts, snap in self._campaign_snapshots():
            cid = snap.get("campaign_id")
            if not isinstance(cid, str):
                continue
            prev = latest.get(cid)
            if prev is None or ts >= prev[0]:
                latest[cid] = (ts, snap)
        return [snap for _, snap in latest.values()]

    # ------------------------------------------------------------------ #
    # run
    # ------------------------------------------------------------------ #
    def _bootstrap_run(self, campaign_id: str, phase: str, seed: int,
                       idempotency_key: str, campaign: dict,
                       evaluation_context: dict | None = None,
                       imported_source_ref: str | None = None) -> dict:
        """Private: create a run without public phase/status validation."""
        if phase not in PHASES:
            raise DataError("invalid phase", "input_invalid")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 or seed >= 2 ** 63:
            raise DataError("invalid seed", "input_invalid")
        if evaluation_context is not None:
            if phase != "verify":
                raise DataError(
                    "evaluation_context only allowed for verify phase",
                    "input_invalid")
            if not isinstance(evaluation_context, dict):
                raise DataError("evaluation_context must be an object",
                                "input_invalid")
            if set(evaluation_context.keys()) != {
                    "evaluation_manifest_ref", "evaluation_manifest_id"}:
                raise DataError(
                    "evaluation_context must have exactly "
                    "evaluation_manifest_ref and evaluation_manifest_id",
                    "input_invalid")
            ref = evaluation_context.get("evaluation_manifest_ref")
            mid = evaluation_context.get("evaluation_manifest_id")
            if not isinstance(ref, str) or not ref:
                raise DataError("evaluation_manifest_ref required",
                                "input_invalid")
            if not isinstance(mid, str) or not mid:
                raise DataError("evaluation_manifest_id required",
                                "input_invalid")
            try:
                actual = self.store.get_object(ref)
            except (DataError, FileNotFoundError) as exc:
                raise DataError("evaluation manifest not found",
                                "input_invalid") from exc
            if not isinstance(actual, dict):
                raise DataError("evaluation manifest invalid",
                                "input_invalid")
            if actual.get("status") != "frozen":
                raise DataError("evaluation manifest not frozen",
                                "input_invalid")
            if actual.get("manifest_id") != mid:
                raise DataError("evaluation manifest id mismatch",
                                "input_invalid")
        payload = {"campaign_id": campaign_id, "phase": phase, "seed": seed}
        if evaluation_context is not None:
            payload["evaluation_context"] = evaluation_context
        if imported_source_ref is not None:
            payload["imported_source_ref"] = imported_source_ref
        prior = self._check_idem("run", idempotency_key, payload)
        if prior is not None:
            return prior
        catalog = self.store.load_catalog()
        for entry in catalog.get("runs", {}).values():
            if (entry.get("campaign_id") == campaign_id
                    and entry.get("status") == "running"):
                raise DataError("campaign already has a running run",
                                "policy_denied")
        run_id = _new_id("r")
        role = PHASE_ROLE[phase]
        actor = {"role": role, "agent_id": "orchestrator", "model": None}
        started_at = _now()
        budget = {"max_tool_calls": 1000, "max_wall_seconds": 3600,
                  "max_tokens": 100000}
        # Deadline is computed from the actual start time and the wall-clock
        # budget used by this run; it is not a false pin.
        try:
            start_dt = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S.%fZ")
            start_dt = start_dt.replace(tzinfo=timezone.utc)
            deadline = (start_dt.timestamp() + budget["max_wall_seconds"])
            deadline_iso = datetime.fromtimestamp(deadline, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ")
        except (ValueError, OverflowError):
            deadline_iso = None
        # Manifest carries the actual scope/auth/policy snapshot and the
        # model status (unverified/null until RSI API sets it later).  No
        # identity manifest is fabricated here.
        manifest = {
            "campaign_id": campaign_id,
            "run_id": run_id,
            "phase": phase,
            "role": role,
            "seed": seed,
            "scope": campaign.get("scope"),
            "authorization": campaign.get("authorization"),
            "policy_snapshot": self.policy if self.policy else None,
            "policy_digest": digest(self.policy) if self.policy else None,
            "model_status": {"status": "unverified", "model": None},
            "budget": budget,
            "deadline": deadline_iso,
            "started_at": started_at,
        }
        if evaluation_context is not None:
            manifest["evaluation_manifest_ref"] = \
                evaluation_context["evaluation_manifest_ref"]
            manifest["evaluation_manifest_id"] = \
                evaluation_context["evaluation_manifest_id"]
        if imported_source_ref is not None:
            manifest["input_mode"] = "imported_logs"
            manifest["imported_source_ref"] = imported_source_ref
        manifest_ref = self.store.put_object(manifest)
        run = {
            "schema_version": "1.0.0",
            "campaign_id": campaign_id,
            "run_id": run_id,
            "phase": phase,
            "actor": actor,
            "started_at": started_at,
            "ended_at": None,
            "status": "running",
            "completion": None,
            "cause": None,
            "seed": seed,
            "identity": {"operator_id": "orchestrator", "worker_id": None},
            "budget": budget,
            "manifest_ref": manifest_ref,
        }
        if imported_source_ref is not None:
            run["extensions"] = {
                "input_mode": "imported_logs",
                "imported_source_ref": imported_source_ref,
            }
        validate("run", run)
        self.store.save_manifest(run_id, {"run": run, "sealed": False})
        self.store.update_catalog(run_id, {
            "campaign_id": campaign_id, "phase": phase,
            "status": "running", "run_id": run_id,
            "manifest_ref": manifest_ref,
            "campaign": campaign,
        })
        if imported_source_ref is not None:
            # Durable provenance is on both the manifest and verified ledger.
            # This must never be mistaken for a synthetic Red run.
            self.record_event(run_id, "state_captured", "orchestrator",
                              evidence_refs=(imported_source_ref,),
                              extensions={"input_mode": "imported_logs",
                                          "imported_source_ref": imported_source_ref})
        self._save_idem("run", idempotency_key, payload, run)
        return run

    def _prior_phase_sealed(self, campaign_id: str, phase: str) -> bool:
        prior = PHASE_PRIOR.get(phase)
        if prior is None:
            return True
        prior_phase, need_sealed = prior
        for run in self.list_runs(campaign_id):
            if run["phase"] != prior_phase:
                continue
            if run["status"] != "completed":
                continue
            if need_sealed and not self.store._is_sealed(run["run_id"]):
                continue
            return True
        return False

    def start_run(self, campaign_id: str, phase: str, seed: int,
                  idempotency_key: str, *,
                  evaluation_context: dict | None = None,
                  imported_source_ref: str | None = None) -> dict:
        if phase not in PHASES:
            raise DataError("invalid phase", "input_invalid")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 or seed >= 2 ** 63:
            raise DataError("invalid seed", "input_invalid")
        if evaluation_context is not None:
            if phase != "verify":
                raise DataError(
                    "evaluation_context only allowed for verify phase",
                    "input_invalid")
            if not isinstance(evaluation_context, dict):
                raise DataError("evaluation_context must be an object",
                                "input_invalid")
            if set(evaluation_context.keys()) != {
                    "evaluation_manifest_ref", "evaluation_manifest_id"}:
                raise DataError(
                    "evaluation_context must have exactly "
                    "evaluation_manifest_ref and evaluation_manifest_id",
                    "input_invalid")
            ref = evaluation_context.get("evaluation_manifest_ref")
            mid = evaluation_context.get("evaluation_manifest_id")
            if not isinstance(ref, str) or not ref:
                raise DataError("evaluation_manifest_ref required",
                                "input_invalid")
            if not isinstance(mid, str) or not mid:
                raise DataError("evaluation_manifest_id required",
                                "input_invalid")
            try:
                actual = self.store.get_object(ref)
            except (DataError, FileNotFoundError) as exc:
                raise DataError("evaluation manifest not found",
                                "input_invalid") from exc
            if not isinstance(actual, dict):
                raise DataError("evaluation manifest invalid",
                                "input_invalid")
            if actual.get("status") != "frozen":
                raise DataError("evaluation manifest not frozen",
                                "input_invalid")
            if actual.get("manifest_id") != mid:
                raise DataError("evaluation manifest id mismatch",
                                "input_invalid")
        if imported_source_ref is not None:
            if phase != "blue_blind":
                raise DataError("imported logs only allowed for blue_blind",
                                "input_invalid")
            if not isinstance(imported_source_ref, str) or not imported_source_ref:
                raise DataError("imported_source_ref must be a CAS reference",
                                "input_invalid")
            try:
                imported = self.store.get_object(imported_source_ref)
            except (DataError, FileNotFoundError) as exc:
                raise DataError("imported log source not found",
                                "input_invalid") from exc
            if not isinstance(imported, (dict, list)) or not imported:
                raise DataError("imported log source must be nonempty JSON",
                                "input_invalid")
        payload = {"campaign_id": campaign_id, "phase": phase, "seed": seed}
        if evaluation_context is not None:
            payload["evaluation_context"] = evaluation_context
        if imported_source_ref is not None:
            payload["imported_source_ref"] = imported_source_ref
        with self.store.lock():
            prior = self._check_idem("run", idempotency_key, payload)
            if prior is not None:
                return prior
            campaign = self.get_campaign(campaign_id)
            status = campaign["status"]
            if status not in PHASE_STATUS[phase]:
                raise DataError(
                    f"phase {phase} not allowed in campaign status {status}",
                    "policy_denied")
            # Imported Blue uses an immutable CAS source instead of Red output.
            # Ordinary Blind Blue keeps the original Red seal gate.
            if imported_source_ref is None and not self._prior_phase_sealed(campaign_id, phase):
                raise DataError(
                    f"phase {phase} requires prior phase completed and sealed",
                    "policy_denied")
            return self._bootstrap_run(campaign_id, phase, seed,
                                       idempotency_key, campaign,
                                       evaluation_context=evaluation_context,
                                       imported_source_ref=imported_source_ref)

    def get_run(self, run_id: str) -> dict:
        manifest = self.store.load_manifest(run_id)
        if not manifest or "run" not in manifest:
            raise DataError("run not found", "input_invalid")
        return manifest["run"]

    def list_runs(self, campaign_id: str | None = None) -> list[dict]:
        catalog = self.store.load_catalog()
        result = []
        for run_id, entry in catalog.get("runs", {}).items():
            if campaign_id is not None and entry.get("campaign_id") != campaign_id:
                continue
            try:
                result.append(self.get_run(run_id))
            except DataError:
                continue
        return result

    def finish_run(self, run_id: str, completion: str,
                   cause: str = "none") -> dict:
        if completion not in TERMINAL:
            raise DataError("invalid completion", "input_invalid")
        if cause not in CAUSES:
            raise DataError("invalid cause", "input_invalid")
        with self.store.lock():
            manifest = self.store.load_manifest(run_id)
            if not manifest or "run" not in manifest:
                raise DataError("run not found", "input_invalid")
            run = manifest["run"]
            if run["status"] in TERMINAL:
                return run
            status = completion
            if completion == "completed":
                cause = "none"
            run = dict(run)
            run["status"] = status
            run["completion"] = completion
            run["cause"] = cause
            run["ended_at"] = _now()
            validate("run", run)
            # Record the real terminal snapshot while the persisted manifest
            # is still non-terminal, so the public terminal-append guard in
            # record_event is not relaxed.  Then persist the terminal manifest
            # and catalog entry.
            self.record_event(run_id, "state_captured", "orchestrator",
                              extensions={"run_snapshot": run})
            manifest["run"] = run
            self.store.save_manifest(run_id, manifest)
            catalog = self.store.load_catalog()
            entry = dict(catalog.get("runs", {}).get(run_id, {}))
            entry.update({
                "campaign_id": run["campaign_id"], "phase": run["phase"],
                "status": status, "run_id": run_id,
                "manifest_ref": run["manifest_ref"],
            })
            if "campaign" not in entry:
                try:
                    entry["campaign"] = self.get_campaign(run["campaign_id"])
                except DataError:
                    pass
            self.store.update_catalog(run_id, entry)
            return run

    def seal_run(self, campaign_id: str, run_id: str) -> dict:
        with self.store.lock():
            manifest = self.store.load_manifest(run_id)
            if not manifest or "run" not in manifest:
                raise DataError("run not found", "input_invalid")
            run = manifest["run"]
            if run["campaign_id"] != campaign_id:
                raise DataError("run does not belong to campaign",
                                "policy_denied")
            if manifest.get("sealed"):
                events = self.store.read_events(run_id)
                return {"sealed_at": events[-1]["timestamp"] if events else None,
                        "last_sequence": len(events),
                        "last_event_hash": events[-1]["event_hash"] if events else None}
            if run["status"] not in TERMINAL:
                raise DataError("cannot seal unfinished run", "hold")
            self.record_event(run_id, "chain_sealed", "orchestrator")
            manifest["sealed"] = True
            self.store.save_manifest(run_id, manifest)
            events = self.store.read_events(run_id)
            return {"sealed_at": events[-1]["timestamp"],
                    "last_sequence": len(events),
                    "last_event_hash": events[-1]["event_hash"]}

    # ------------------------------------------------------------------ #
    # events
    # ------------------------------------------------------------------ #
    def record_event(self, run_id: str, event_type: str, source: str,
                     evidence_refs: tuple = (), finding: dict | None = None,
                     extensions: dict | None = None) -> str:
        manifest = self.store.load_manifest(run_id)
        if not manifest or "run" not in manifest:
            raise DataError("run not found", "input_invalid")
        run = manifest["run"]
        if manifest.get("sealed"):
            raise DataError("chain is sealed", "policy_denied")
        if run["status"] in TERMINAL and event_type != "chain_sealed":
            raise DataError("terminal run accepts only chain_sealed",
                            "policy_denied")
        if run["status"] not in TERMINAL and event_type == "chain_sealed":
            raise DataError("cannot seal running run", "policy_denied")
        event = {
            "schema_version": "1.0.0",
            "campaign_id": run["campaign_id"],
            "run_id": run_id,
            "actor": run["actor"],
            "source": source,
            "event_type": event_type,
            "timestamp": _now(),
            "target": {"kind": "fixture", "identifier": "runtime"},
            "finding": finding,
            "evidence_refs": list(evidence_refs),
        }
        if extensions:
            event["extensions"] = extensions
        return self.store.append_event(run_id, event)

    # ------------------------------------------------------------------ #
    # state machine
    # ------------------------------------------------------------------ #
    def _events(self, campaign_id: str) -> list[dict]:
        events: list[dict] = []
        for run in self.list_runs(campaign_id):
            chain = self.store.verify_chain(run["run_id"])
            if chain.get("result") != "PASS":
                continue
            try:
                events.extend(self.store.read_events(run["run_id"]))
            except DataError:
                continue
        return events

    def _find_record(self, campaign_id: str, kind: str, predicate) -> dict | None:
        for ref in self.list_records(campaign_id, kind):
            try:
                obj = self.store.get_object(ref)
            except (DataError, FileNotFoundError):
                continue
            if predicate(obj):
                return obj
        return None

    def _revalidate_final_verdict(self, campaign_id: str, verdict: dict) -> bool:
        """Re-run semantic validation of a stored FinalVerdict against real CAS."""
        try:
            campaign = self.get_campaign(campaign_id)
            runs = self.list_runs(campaign_id)
            findings = [self.store.get_object(r)
                        for r in self.list_records(campaign_id, "finding")]
            remediations = [self.store.get_object(r)
                            for r in self.list_records(campaign_id, "remediation")]
        except (DataError, FileNotFoundError):
            return False
        oracle_eval = None
        if verdict.get("benign_regression_passed") is True:
            oracle_eval = self._find_oracle_wrapper(campaign_id, verdict)
            if oracle_eval is None:
                return False
        ctx = {
            "get_object": self.store.get_object,
            "findings": findings,
            "remediations": remediations,
            "campaign": campaign,
            "runs": runs,
            "oracle_evaluation": oracle_eval,
        }
        try:
            semantic_validate("final_verdict", verdict, ctx)
        except (ContractError, DataError):
            return False
        return True

    def _find_oracle_wrapper(self, campaign_id: str, verdict: dict) -> dict | None:
        """Locate the OraclePredicate wrapper referenced by a FinalVerdict."""
        target_run = verdict.get("run_id")
        for ref in self.list_records(campaign_id, "oracle_result"):
            try:
                obj = self.store.get_object(ref)
            except (DataError, FileNotFoundError):
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("campaign_id") != campaign_id:
                continue
            if target_run is not None and obj.get("evaluated_run_id") != target_run:
                continue
            predicate = obj.get("predicate")
            if not isinstance(predicate, dict):
                continue
            if predicate.get("verdict") != verdict.get("oracle_verdict"):
                continue
            if predicate.get("reason_code") != verdict.get("oracle_reason_code"):
                continue
            return predicate
        return None

    def _revalidate_approval(self, campaign_id: str, approval: dict) -> bool:
        """Re-run semantic validation of a stored Approval at decision time."""
        try:
            remediations = [self.store.get_object(r)
                            for r in self.list_records(campaign_id, "remediation")]
        except (DataError, FileNotFoundError):
            return False
        ctx = {
            "get_object": self.store.get_object,
            "remediations": remediations,
        }
        try:
            semantic_validate("approval", approval, ctx)
        except (ContractError, DataError):
            return False
        return True

    def _guard(self, name: str, campaign_id: str, guard_ctx: dict | None) -> bool:
        events = self._events(campaign_id)
        types = {e.get("event_type") for e in events}
        if name == "scope_validated":
            return "scope_validated" in types
        if name == "blind_completed":
            # actual completed blue_blind run (seal not required here)
            for run in self.list_runs(campaign_id):
                if run["phase"] == "blue_blind" and run["status"] == "completed":
                    return True
            return False
        if name == "blind_sealed_evaluated":
            blue = None
            for run in self.list_runs(campaign_id):
                if run["phase"] == "blue_blind" and run["status"] == "completed":
                    if self.store._is_sealed(run["run_id"]):
                        blue = run
                        break
            if blue is None:
                return False
            # Independent non-LLM Oracle evaluation associated with blue run.
            # The wrapper must reference an independent eval run in phase
            # verify/oracle, and the stored predicate must actually PASS when
            # recomputed against the input CAS.
            from .oracle import Oracle
            oracle = Oracle()
            for ref in self.list_records(campaign_id, "oracle_result"):
                try:
                    obj = self.store.get_object(ref)
                except (DataError, FileNotFoundError):
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("campaign_id") != campaign_id:
                    continue
                if obj.get("evaluated_run_id") != blue["run_id"]:
                    continue
                predicate = obj.get("predicate")
                if not isinstance(predicate, dict):
                    continue
                eval_run_id = obj.get("run_id")
                if not isinstance(eval_run_id, str):
                    continue
                try:
                    eval_run = self.get_run(eval_run_id)
                except DataError:
                    continue
                if eval_run.get("campaign_id") != campaign_id:
                    continue
                if eval_run.get("phase") not in ("verify", "reevaluation"):
                    continue
                if eval_run.get("status") != "completed":
                    continue
                if not self.store._is_sealed(eval_run_id):
                    continue
                try:
                    recomputed = oracle.evaluate(predicate, self.store)
                except (ContractError, DataError):
                    continue
                if recomputed.get("verdict") != "PASS":
                    continue
                return True
            return False
        if name == "patch_drafted":
            return "patch_drafted" in types
        if name == "remediation_ready":
            verdict = self._find_record(
                campaign_id, "final_verdict",
                lambda v: v.get("campaign_id") == campaign_id
                and v.get("remediation_id") is not None)
            if verdict is None:
                return False
            if verdict.get("remediation_success") is not True:
                return False
            if not verdict.get("test_report_refs"):
                return False
            return self._revalidate_final_verdict(campaign_id, verdict)
        if name == "approval_approved":
            verdict = self._find_record(
                campaign_id, "final_verdict",
                lambda v: v.get("campaign_id") == campaign_id
                and v.get("remediation_success") is True
                and v.get("benign_regression_passed") is True)
            if verdict is None:
                return False
            rem_id = verdict.get("remediation_id")
            if not rem_id:
                return False
            if not self._revalidate_final_verdict(campaign_id, verdict):
                return False
            approval = self._find_record(
                campaign_id, "approval",
                lambda a: a.get("remediation_id") == rem_id
                and a.get("decision") == "approved")
            if approval is None:
                return False
            return self._revalidate_approval(campaign_id, approval)
        if name == "any_incomplete":
            return True
        if name == "any_cancelled":
            return True
        return False

    def transition(self, campaign_id: str, to: str,
                   guard_ctx: dict | None = None) -> None:
        with self.store.lock():
            campaign = self.get_campaign(campaign_id)
            current = campaign["status"]
            if current == to:
                return
            allowed = TRANSITIONS.get(current, {})
            if to not in allowed:
                raise DataError(f"invalid transition {current}->{to}",
                                "policy_denied")
            guard = allowed[to]
            if not self._guard(guard, campaign_id, guard_ctx):
                raise DataError(f"guard {guard} not satisfied", "hold")
            campaign = dict(campaign)
            campaign["status"] = to
            validate("campaign", campaign)
            # record as new control run, not appended to sealed chain
            run = self._bootstrap_run(
                campaign_id, "verify", 0,
                f"transition:{campaign_id}:{current}->{to}", campaign)
            self.record_event(run["run_id"], "scope_validated", "orchestrator",
                              extensions={"campaign_snapshot": campaign,
                                          "run_snapshot": run})
            self.finish_run(run["run_id"], "completed")
            self.seal_run(campaign_id, run["run_id"])

    # ------------------------------------------------------------------ #
    # crash resume
    # ------------------------------------------------------------------ #
    def resume_after_crash(self, run_id: str) -> dict:
        with self.store.lock():
            manifest = self.store.load_manifest(run_id)
            if not manifest or "run" not in manifest:
                raise DataError("run not found", "input_invalid")
            run = manifest["run"]
            # Inherit evaluation identity from the old run's actual CAS
            # manifest BEFORE the idempotency payload/lookup so that a retry
            # of the same crash resume produces the same payload and returns
            # the previously created new run.  A bad CAS manifest must fail
            # closed (Store error), not be swallowed to None.
            inherited_ctx = None
            inherited_imported_source_ref = None
            old_ref = run.get("manifest_ref")
            if isinstance(old_ref, str) and old_ref:
                old_manifest = self.store.get_object(old_ref)
                if isinstance(old_manifest, dict):
                    if old_manifest.get("input_mode") == "imported_logs":
                        inherited_imported_source_ref = old_manifest.get("imported_source_ref")
                        if not isinstance(inherited_imported_source_ref, str):
                            raise DataError("imported log source reference invalid", "hold")
                        self.store.get_object(inherited_imported_source_ref)
                    mref = old_manifest.get("evaluation_manifest_ref")
                    mid = old_manifest.get("evaluation_manifest_id")
                    if isinstance(mref, str) and mref and isinstance(mid, str) and mid:
                        inherited_ctx = {
                            "evaluation_manifest_ref": mref,
                            "evaluation_manifest_id": mid,
                        }
            # Deterministic resume result lookup: a retry of the same crash
            # resume must return the previously created new run.
            idem_key = f"resume:{run_id}"
            payload = {"campaign_id": run["campaign_id"],
                       "phase": run["phase"], "seed": run["seed"]}
            if inherited_ctx is not None:
                payload["evaluation_context"] = inherited_ctx
            if inherited_imported_source_ref is not None:
                payload["imported_source_ref"] = inherited_imported_source_ref
            prior = self._check_idem("run", idem_key, payload)
            if prior is not None:
                return prior
            # Only a crash-incomplete old run is resumable; other terminal
            # states (completed/cancelled/adapter_unavailable) are rejected.
            if run["status"] in TERMINAL:
                if run["status"] == "incomplete" and run.get("cause") == "crash":
                    raise DataError("crash resume already recorded", "hold")
                raise DataError("cannot resume terminal run", "policy_denied")
            if self.store._is_sealed(run_id):
                raise DataError("cannot resume sealed run", "policy_denied")
            # Record crash/finish on the original incomplete run; the original
            # run's prior events remain preserved.
            self.finish_run(run_id, "incomplete", "crash")
            new_run = self._bootstrap_run(
                run["campaign_id"], run["phase"], run["seed"],
                idem_key,
                self.get_campaign(run["campaign_id"]),
                evaluation_context=inherited_ctx,
                imported_source_ref=inherited_imported_source_ref)
            return new_run

    # ------------------------------------------------------------------ #
    # oracle recording
    # ------------------------------------------------------------------ #
    def record_oracle(self, run_id: str, predicate: dict,
                      evaluated_run_id: str | None = None) -> dict:
        from .oracle import Oracle
        oracle = Oracle()
        with self.store.lock():
            eval_run = self.get_run(run_id)
            campaign_id = eval_run["campaign_id"]
            if eval_run["phase"] not in ("verify", "reevaluation"):
                raise DataError("oracle recording requires verify/reevaluation phase",
                                "policy_denied")
            # The eval run must be active; a completed run cannot accept
            # new events (record_event would reject the append anyway).
            if eval_run["status"] != "running":
                raise DataError("oracle recording requires active run",
                                "policy_denied")
            if evaluated_run_id is not None:
                # Blue-independent path: explicit evaluated_run_id must
                # reference a completed, sealed blue_blind run in the same
                # campaign.  This guard is never relaxed.
                if not isinstance(evaluated_run_id, str):
                    raise DataError("evaluated_run_id must be a string",
                                    "input_invalid")
                try:
                    evaluated = self.get_run(evaluated_run_id)
                except DataError as exc:
                    raise DataError("evaluated run not found",
                                    "input_invalid") from exc
                if evaluated["campaign_id"] != campaign_id:
                    raise DataError("evaluated run outside campaign",
                                    "policy_denied")
                if evaluated["phase"] != "blue_blind":
                    raise DataError("evaluated run must be blue_blind",
                                    "policy_denied")
                if evaluated["status"] != "completed":
                    raise DataError("evaluated run must be completed",
                                    "policy_denied")
                if not self.store._is_sealed(evaluated_run_id):
                    raise DataError("evaluated run must be sealed",
                                    "policy_denied")
                target = evaluated_run_id
            else:
                # Default path: evaluate the eval run's own input CAS.
                # Used for Red baseline / remediation reevaluation / verify
                # where the run itself is the evaluation target.
                target = run_id
            # Recompute the predicate from real CAS; never trust a self-claim.
            result = oracle.evaluate(predicate, self.store)
            wrapper = {
                "campaign_id": campaign_id,
                "run_id": run_id,
                "evaluated_run_id": target,
                "predicate": result,
            }
            ref = self.store.put_object(wrapper)
            self.record_event(run_id, "oracle_replay_result", "oracle",
                              evidence_refs=(ref,),
                              extensions={"record_kind": "oracle_result",
                                          "record_ref": ref,
                                          "verdict": result["verdict"],
                                          "reason_code": result["reason_code"],
                                          "evaluated_run_id": target})
            return result

    # ------------------------------------------------------------------ #
    # records
    # ------------------------------------------------------------------ #
    def list_records(self, campaign_id: str, kind: str) -> list[str]:
        """Return record refs attached to verified campaign ledger."""
        refs: list[str] = []
        for run in self.list_runs(campaign_id):
            chain = self.store.verify_chain(run["run_id"])
            if chain.get("result") != "PASS":
                continue
            for event in self.store.read_events(run["run_id"]):
                ext = event.get("extensions") or {}
                if not isinstance(ext, dict):
                    continue
                if ext.get("record_kind") == kind:
                    ref = ext.get("record_ref")
                    if isinstance(ref, str):
                        refs.append(ref)
        return refs

    def register_record(self, kind: str, value: dict,
                        run_id: str | None = None,
                        oracle_evaluation: dict | None = None) -> str:
        if kind not in RECORD_KINDS:
            raise DataError("unknown record kind", "input_invalid")
        if not isinstance(value, dict):
            raise DataError("record must be an object", "input_invalid")
        with self.store.lock():
            campaign_id = value.get("campaign_id")
            if not isinstance(campaign_id, str):
                raise DataError("record campaign_id required", "input_invalid")
            campaign = self.get_campaign(campaign_id)
            runs = self.list_runs(campaign_id)
            findings = [self.store.get_object(r)
                        for r in self.list_records(campaign_id, "finding")]
            remediations = [self.store.get_object(r)
                            for r in self.list_records(campaign_id, "remediation")]
            ctx = {
                "get_object": self.store.get_object,
                "findings": findings,
                "remediations": remediations,
                "campaign": campaign,
                "runs": runs,
                "oracle_evaluation": oracle_evaluation,
            }
            if kind == "oracle_result":
                from .oracle import Oracle
                if not isinstance(oracle_evaluation, dict):
                    raise DataError("oracle_result requires oracle_evaluation wrapper",
                                    "input_invalid")
                wrapper = oracle_evaluation
                if wrapper.get("campaign_id") != campaign_id:
                    raise DataError("oracle wrapper campaign mismatch",
                                    "policy_denied")
                predicate = wrapper.get("predicate")
                if not isinstance(predicate, dict):
                    raise DataError("oracle wrapper predicate required",
                                    "input_invalid")
                recomputed = Oracle().evaluate(predicate, self.store)
                stored = dict(wrapper)
                stored["predicate"] = recomputed
                value = stored
            elif kind in ("finding", "remediation", "approval", "final_verdict"):
                semantic_validate(kind, value, ctx)
            else:
                validate(kind, value)
            ref = self.store.put_object(value)
            target_run = run_id
            if target_run is None:
                for run in runs:
                    if run["status"] == "running":
                        target_run = run["run_id"]
                        break
            if target_run is None:
                raise DataError("no active run to attach record", "hold")
            target = self.get_run(target_run)
            if target["campaign_id"] != campaign_id:
                raise DataError("target run outside campaign", "policy_denied")
            if kind == "oracle_result":
                if value.get("run_id") != target_run:
                    raise DataError("oracle wrapper run_id mismatch",
                                    "policy_denied")
            self.record_event(target_run, "state_captured", "orchestrator",
                              evidence_refs=(ref,),
                              extensions={"record_kind": kind,
                                          "record_ref": ref})
            return ref


# --------------------------------------------------------------------------- #
# module-level wrappers -> default Runtime
# --------------------------------------------------------------------------- #
_default: Runtime | None = None


def _runtime() -> Runtime:
    global _default
    if _default is None:
        _default = Runtime(Store.from_env())
    return _default


def create_campaign(intent_id: str, scope: dict, authorization: dict,
                    idempotency_key: str) -> dict:
    return _runtime().create_campaign(intent_id, scope, authorization, idempotency_key)


def start_run(campaign_id: str, phase: str, seed: int,
              idempotency_key: str, *,
              evaluation_context: dict | None = None,
              imported_source_ref: str | None = None) -> dict:
    return _runtime().start_run(campaign_id, phase, seed, idempotency_key,
                                evaluation_context=evaluation_context,
                                imported_source_ref=imported_source_ref)


def record_event(run_id: str, event_type: str, source: str,
                 evidence_refs: tuple = (), finding: dict | None = None,
                 extensions: dict | None = None) -> str:
    return _runtime().record_event(run_id, event_type, source, evidence_refs,
                                   finding, extensions)


def get_run(run_id: str) -> dict:
    return _runtime().get_run(run_id)


def list_runs(campaign_id: str | None = None) -> list[dict]:
    return _runtime().list_runs(campaign_id)


def get_campaign(campaign_id: str) -> dict:
    return _runtime().get_campaign(campaign_id)


def list_campaigns() -> list[dict]:
    return _runtime().list_campaigns()


def finish_run(run_id: str, completion: str, cause: str = "none") -> dict:
    return _runtime().finish_run(run_id, completion, cause)


def seal_run(campaign_id: str, run_id: str) -> dict:
    return _runtime().seal_run(campaign_id, run_id)


def transition(campaign_id: str, to: str, guard_ctx: dict | None = None) -> None:
    return _runtime().transition(campaign_id, to, guard_ctx)


def resume_after_crash(run_id: str) -> dict:
    return _runtime().resume_after_crash(run_id)


def record_oracle(run_id: str, predicate: dict,
                  evaluated_run_id: str | None = None) -> dict:
    return _runtime().record_oracle(run_id, predicate, evaluated_run_id)


def register_record(kind: str, value: dict, run_id: str | None = None,
                    oracle_evaluation: dict | None = None) -> str:
    return _runtime().register_record(kind, value, run_id, oracle_evaluation)


def list_records(campaign_id: str, kind: str) -> list[str]:
    return _runtime().list_records(campaign_id, kind)
