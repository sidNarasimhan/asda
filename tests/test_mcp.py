from fastapi.testclient import TestClient

from asda.api.main import app
from asda.mcp_server import _rpc, dispatch, tool_manifest


def test_manifest_has_core_tools():
    names = {t["name"] for t in tool_manifest()}
    assert "asda.talk" in names
    assert "asda.activity" in names
    assert "asda.worker.stop" in names


def test_rpc_initialize_and_tools_list():
    init = _rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "asda"
    listed = _rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert any(t["name"] == "asda.status" for t in listed["result"]["tools"])


def test_dispatch_status():
    out = dispatch("asda.status", {})
    assert "setup" in out
    assert "worker" in out


def test_http_jsonrpc_is_real_mcp():
    client = TestClient(app)
    init = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init.status_code == 200
    assert init.json()["result"]["serverInfo"]["name"] == "asda"
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in listed.json()["result"]["tools"]]
    assert "asda.status" in names
    assert "asda.workboard" in names
    assert "asda.memory.recall" in names
    assert "asda.tick" in names
    called = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "asda.status", "arguments": {}},
        },
    )
    assert called.status_code == 200
    text = called.json()["result"]["content"][0]["text"]
    assert "setup" in text
    assert "worker" in text


def test_validate_tool():
    out = dispatch("asda.validate", {})
    assert out["mcp"]["ok"] is True
    assert out["mcp"]["tools"] >= 10
    assert "due_steps" in out["agent"]["loop"]
