"""Ephemeral local AYM fixture for dual-view browser tests."""
from __future__ import annotations

import tempfile
from pathlib import Path
from ai_yori_murasaki import intake
from ai_yori_murasaki.cli import main as run_cli
from ai_yori_murasaki.jsonio import canonical, digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import create_server
from ai_yori_murasaki.sources import Sources
from ai_yori_murasaki.storage import Store


def seed_verification_history(runtime: Runtime, campaign_id: str) -> None:
    store = runtime.store
    directory = store.workspace / "verification"
    directory.mkdir(exist_ok=True)
    [initial] = runtime.list_runs(campaign_id)
    (directory / (initial["run_id"] + ".jsonl")).touch()

    run = runtime.start_run(campaign_id, "verify", 1, "journal-browser")
    run_id = run["run_id"]
    reference = Sources(store).add(provider="github", source_url="https://example.test/notes/1",
                                   title="画面試験の参考資料", body="合成資料の本文", links=[])
    runtime.record_event(run_id, "state_captured", "orchestrator", evidence_refs=(reference["ref"],),
                         finding={"finding_id": "browser-note", "severity": "info", "title": "合成イベントの件名"},
                         extensions={"record_type": "agent_turn", "reason": "合成イベントの理由"})
    runtime.finish_run(run_id, "completed")
    runtime.seal_run(campaign_id, run_id)
    store.seal_checkpoint(campaign_id, run_id, "browser-test")

    # Model a long-lived installation, with a historical failure on the second page.
    legacy = {"verify_id": run_id, "run_id": run_id, "campaign_id": campaign_id,
              "checked_at": "2026-11-16T00:00:00Z", "result": "PASS", "reason_code": "ok"}
    passed = canonical(legacy) + b"\n"
    failed = canonical({**legacy, "result": "FAIL", "reason_code": "checkpoint_mismatch"}) + b"\n"
    with (directory / (run_id + ".jsonl")).open("wb") as stream:
        stream.write(passed * (1024 * 1024 // len(passed) + 1))
        stream.write(failed)
        stream.write(passed * 24)
    rc = run_cli(["--workspace", str(store.workspace), "--trusted-root", str(store.trusted_root),
                  "verify", "checkpoint", "--campaign-id", campaign_id, "--run-id", run_id])
    assert rc == 0


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="aym-dual-browser-") as base:
        root = Path(base)
        ws, tr = root / "workspace", root / "trusted"
        ws.mkdir()
        tr.mkdir()
        runtime = Runtime(Store(ws, tr))
        scope = {"targets": [{"kind": "fixture", "identifier": "demo"}], "max_targets": 1}
        auth = {
            "operator_id": "browser-test", "granted_at": "2026-11-16T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope),
        }
        campaign = runtime.create_campaign("INT-DUAL-BROWSER", scope, auth, "dual-browser-fixture")
        receipt = intake.register(runtime.store, "画面試験の受付", "browser-linked")
        intake.register(runtime.store, "画面試験の受付", "browser-unlinked")
        intake.link(runtime.store, receipt["receipt_id"], campaign["campaign_id"])
        seed_verification_history(runtime, campaign["campaign_id"])
        server = create_server(runtime, "127.0.0.1:8765")
        try:
            print("AYM dual-view browser fixture listening", flush=True)
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
