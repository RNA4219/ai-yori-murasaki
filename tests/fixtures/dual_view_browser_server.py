"""Ephemeral local AYM fixture for dual-view browser tests."""
from __future__ import annotations

import tempfile
from pathlib import Path
from ai_yori_murasaki.jsonio import digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import create_server
from ai_yori_murasaki.storage import Store


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
        runtime.create_campaign("INT-DUAL-BROWSER", scope, auth, "dual-browser-fixture")
        server = create_server(runtime, "127.0.0.1:8765")
        try:
            print("AYM dual-view browser fixture listening", flush=True)
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
