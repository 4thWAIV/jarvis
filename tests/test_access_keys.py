"""Access keys: a key admits its holder to its categories' routes and to
nothing else; only its digest is stored; revoking it takes effect at once."""

import json
import os
import stat

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis import access_keys
from jarvis import data_paths
from jarvis import web_auth


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    return tmp_path


def _client():
    """Every kind of route the categories name, and some none does, behind
    the real guard."""
    async def ok(request):
        return PlainTextResponse("ok")

    async def ws(socket):
        await socket.accept()
        await socket.send_text("ok")
        await socket.close()

    app = Starlette(routes=[
        Route("/api/capture/{n}/{action}", ok, methods=["POST"]),
        Route("/api/runs", ok, methods=["POST"]),
        Route("/api/settings/keys", ok, methods=["POST"]),
        Route("/api/restart", ok, methods=["POST"]),
        Route("/internal/tool", ok, methods=["POST"]),
        WebSocketRoute("/ws/sessions", ws),
        WebSocketRoute("/ws/voice", ws),
    ])
    return TestClient(web_auth.OriginGuard(app))


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


def test_a_captures_key_opens_the_capture_buttons_and_nothing_else(data):
    key = access_keys.create("hammer", ["captures"])
    client = _client()

    for action in ("read", "accept", "delete", "send", "restore"):
        assert client.post(f"/api/capture/3/{action}", headers=_bearer(key)).status_code == 200
    for path in ("/api/runs", "/api/settings/keys", "/api/restart", "/internal/tool",
                 "/api/capture/3/other"):
        assert client.post(path, headers=_bearer(key)).status_code == 403, path


def test_a_key_opens_only_the_sockets_its_categories_name(data):
    key = access_keys.create("watcher", ["watch"])
    client = _client()

    with client.websocket_connect("/ws/sessions", headers=_bearer(key)) as socket:
        assert socket.receive_text() == "ok"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/voice", headers=_bearer(key)):
            pass


def test_the_tool_token_and_the_page_still_open_everything(data):
    client = _client()
    token = data_paths.ensure_tool_token()
    assert client.post("/api/restart", headers=_bearer(token)).status_code == 200
    assert client.post("/api/restart",
                       headers={"Origin": "http://localhost:5173"}).status_code == 200


def test_a_revoked_key_or_a_made_up_one_opens_nothing(data):
    key = access_keys.create("hammer", ["captures"])
    client = _client()
    assert access_keys.revoke("HAMMER")
    assert client.post("/api/capture/1/accept", headers=_bearer(key)).status_code == 403
    assert client.post("/api/capture/1/accept",
                       headers=_bearer(access_keys.PREFIX + "guess")).status_code == 403
    assert not access_keys.revoke("hammer")


def test_only_a_digest_is_kept_in_a_file_only_the_user_can_read(data):
    key = access_keys.create("hammer", ["captures", "agents"])
    path = data_paths.access_keys_path()

    assert key not in path.read_text()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    (entry,) = json.loads(path.read_text())["keys"]
    assert entry["categories"] == ["agents", "captures"]
    assert access_keys.listing() == [{"label": "hammer", "categories": ["agents", "captures"],
                                      "created_at": entry["created_at"]}]


@pytest.mark.parametrize("label, categories", [
    ("hammer", ["everything"]), ("hammer", []), ("", ["captures"]), ("a/b", ["captures"])])
def test_a_key_is_refused_for_an_unknown_category_or_a_bad_label(data, label, categories):
    with pytest.raises(access_keys.AccessKeyRefused):
        access_keys.create(label, categories)
    assert access_keys.listing() == []


def test_one_label_holds_one_key(data):
    access_keys.create("hammer", ["captures"])
    with pytest.raises(access_keys.AccessKeyRefused):
        access_keys.create("Hammer", ["runs"])


def test_a_missing_or_broken_store_admits_nothing(data):
    assert not access_keys.allows(access_keys.PREFIX + "x", "POST", "/api/capture/1/accept")
    data_paths.access_keys_path().write_text("not json")
    assert not access_keys.allows(access_keys.PREFIX + "x", "POST", "/api/capture/1/accept")


def test_the_command_makes_lists_and_revokes(data, capsys):
    assert access_keys.main(["access_keys.py", "create", "hammer", "captures"]) == 0
    key = capsys.readouterr().out.splitlines()[-1]
    assert access_keys.allows(key, "POST", "/api/capture/1/send")
    assert access_keys.main(["access_keys.py", "list"]) == 0
    assert capsys.readouterr().out.startswith("hammer: captures (made ")
    assert access_keys.main(["access_keys.py", "revoke", "hammer"]) == 0
    assert access_keys.main(["access_keys.py", "create", "x", "nonsense"]) == 1
    assert access_keys.main(["access_keys.py"]) == 2
