import json
from pathlib import Path
import sys
import socket
import subprocess
import time

import pytest

from ai_yori_murasaki import cli
from ai_yori_murasaki.jsonio import DataError, canonical
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import handle_request
from ai_yori_murasaki.sources import Sources, safe_url, MAX_TEXT
from ai_yori_murasaki.source_mcp import import_mcp, validate_request
from ai_yori_murasaki.storage import Store


@pytest.fixture
def sources(tmp_path):
    return Sources(Store(tmp_path / "ws", tmp_path / "tr"))


def add(sources, **changes):
    args = dict(provider="github", source_url="https://github.com/example/repo/issues/7",
                title="Example", body="Reference text", links=["https://example.com/spec#section"])
    args.update(changes)
    return sources.add(**args)


def test_import_retry_update_and_old_snapshot(sources):
    first = add(sources)
    assert first["status"] == "imported"
    assert add(sources)["status"] == "unchanged"
    changed = add(sources, body="Revised text")
    assert changed["document_id"] == first["document_id"]
    assert changed["status"] == "updated"
    current = sources.get(first["document_id"])
    assert current["previous_ref"] == first["ref"]
    assert sources.store.get_object(first["ref"])["body"] == "Reference text"
    assert current["status"] == "reference_only"
    assert len(sources.list()) == 1
    assert Runtime(sources.store).list_campaigns() == []


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,bad", "file:///etc/passwd",
    "https://user:password@example.com", "https://example.com/?token=secret",
    "obsidian://open?vault=x&file=..%2Fsecret", "https://example.com/\nheader"])
def test_unsafe_links_rejected_atomically(sources, url):
    with pytest.raises((DataError, ValueError)):
        add(sources, links=[url])
    assert sources.list() == []


def test_obsidian_unicode_note_and_vault_boundary(sources, tmp_path):
    vault = tmp_path / "資料"
    (vault / "メモ").mkdir(parents=True)
    note = vault / "メモ" / "認可.md"
    note.write_text("# 認可のメモ\n\n本文", encoding="utf-8")
    result = sources.obsidian(vault, Path("メモ/認可.md"))
    document = sources.get(result["document_id"])
    assert document["body"].endswith("本文")
    assert document["source_url"].startswith("obsidian://open?vault=")
    assert "%E8" in document["source_url"]
    with pytest.raises(DataError):
        sources.obsidian(vault, Path("../secret.md"))


def test_empty_oversized_and_corrupt_documents(sources):
    for body in ("", " " * 5, "x" * (MAX_TEXT + 1)):
        with pytest.raises(DataError):
            add(sources, body=body)
    first = add(sources)
    sources.store._object_path(first["ref"]).write_bytes(b"{}")
    with pytest.raises(DataError):
        sources.get(first["document_id"])


def test_http_source_projection_and_blind_boundary(sources):
    first = add(sources, body="<script>window.bad=true</script>")
    runtime = Runtime(sources.store)
    def request(path):
        return handle_request(runtime, "GET", path, headers={"Host": "127.0.0.1:8765"}, port=8765)
    status, _, data = request("/api/sources")
    assert status == 200 and len(json.loads(data)["sources"]) == 1
    status, _, data = request("/api/sources/" + first["document_id"])
    assert status == 200
    assert json.loads(data)["source"]["body"].startswith("<script>")
    for path in ("/api/sources?view=blue", "/api/sources/" + first["document_id"] + "?view=blue"):
        status, _, data = request(path)
        assert status == 200 and json.loads(data)["status"] == "withheld"
        assert b"Example" not in data and b"document_id" not in data


def test_cli_import_and_list_with_source_links(sources, tmp_path, capsys):
    note = tmp_path / "issue.md"
    note.write_text("Issue source", encoding="utf-8")
    prefix = ["--workspace", str(sources.store.workspace), "--trusted-root", str(sources.store.trusted_root)]
    assert cli.main(prefix + ["sources", "import-file", "--file", str(note), "--provider", "github",
        "--title", "Issue", "--source-url", "https://github.com/example/repo/issues/7",
        "--link", "https://example.com/spec"]) == 0
    capsys.readouterr()
    assert cli.main(prefix + ["sources", "list"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["data"]["sources"][0]["links"] == ["https://example.com/spec"]


@pytest.mark.parametrize("provider,tool,args", [
    ("github", "delete_issue", {}), ("github", "issue_read", {"method": "update"}),
    ("notion", "create_page", {}), ("obsidian", "write_note", {})])
def test_mcp_write_tools_are_denied(provider, tool, args):
    with pytest.raises(DataError):
        validate_request(provider, tool, args)


@pytest.mark.parametrize("provider,tool,args,url", [
    ("github", "issue_read", {"method": "get", "owner": "example", "repo": "repo", "issue_number": 7},
     "https://github.com/example/repo/issues/7"),
    ("notion", "fetch", {"url": "https://www.notion.so/example"}, "https://www.notion.so/example"),
    ("obsidian", "read_note", {"path": "example.md"}, "obsidian://open?vault=Notes&file=example"),
])
def test_real_stdio_mcp_reader(sources, tmp_path, monkeypatch, provider, tool, args, url):
    pytest.importorskip("mcp")
    monkeypatch.setenv("AYM_FIXTURE_SECRET", "fixture-credential-must-not-persist")
    config = tmp_path / "mcp.json"
    config.write_bytes(canonical({"transport": "stdio", "command": sys.executable,
        "args": [str(Path(__file__).parent / "fixtures/source_mcp_server.py")],
        "env": {"FIXTURE_SECRET": "AYM_FIXTURE_SECRET"}}))
    result = import_mcp(sources, provider=provider, tool=tool, arguments=args,
                        config_file=config, source_url=url, title="MCP source")
    document = sources.get(result["document_id"])
    assert document["transport"] == "mcp"
    assert "example" in document["body"].lower()
    assert "fixture-credential-must-not-persist" not in document["body"]


def test_real_http_mcp_reader_with_bearer(sources, tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    process = subprocess.Popen([sys.executable, str(Path(__file__).parent / "fixtures/source_mcp_server.py"),
                                "--http", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 12
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    pytest.fail("local MCP HTTP fixture did not start")
                time.sleep(.05)
        monkeypatch.setenv("AYM_HTTP_FIXTURE_TOKEN", "fixture-http-credential")
        config = tmp_path / "http.json"
        config.write_bytes(canonical({"transport": "http", "url": f"http://127.0.0.1:{port}/mcp",
                                     "bearer_env": "AYM_HTTP_FIXTURE_TOKEN"}))
        result = import_mcp(sources, provider="notion", tool="fetch", arguments={"url": "https://example.com/note"},
            config_file=config, source_url="https://example.com/note", title="HTTP source")
        assert "Notion example" in sources.get(result["document_id"])["body"]
        assert "fixture-http-credential" not in sources.store.get_object(result["ref"])["body"]
    finally:
        process.terminate()
        process.wait(timeout=5)
