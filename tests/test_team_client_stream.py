"""The team connection's one streamed request (`TeamConnection.stream`),
which attachment uploads and downloads go through: the body is streamed
both ways, the session rules are `call`'s. A fake team server answers
through httpx.MockTransport; the Keychain is conftest's stand-in."""
from __future__ import annotations

import hashlib

import httpx
import pytest

from jarvis.team_client import TeamError
from tests.test_team_client import FakeServer, conn, connect, server  # noqa: F401  (fixtures)

UPLOAD = "/api/v1/attachments/00000000-0000-4000-8000-000000000001/bytes"
DOWNLOAD = "/api/v1/messages/m/attachments/a/content"
BLOB = bytes(range(256)) * 4096          # 1 MiB


class FilesServer(FakeServer):
    def __init__(self):
        super().__init__()
        self.received: list[tuple[str, str, int, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down or self.redirect:
            return super().handler(request)
        auth = request.headers.get("authorization", "").removeprefix("Bearer ")
        if request.url.path == UPLOAD and auth in self.valid_tokens:
            body = request.content
            self.received.append((request.headers["content-type"],
                                   request.headers.get("transfer-encoding", ""), len(body),
                                   hashlib.sha256(body).hexdigest()))
            return httpx.Response(200, json={"received_bytes": len(body)})
        if request.url.path == DOWNLOAD and auth in self.valid_tokens:
            return httpx.Response(200, content=BLOB,
                                  headers={"content-type": "application/octet-stream"})
        return super().handler(request)


@pytest.fixture
def server():  # noqa: F811
    return FilesServer()


async def pieces():
    for i in range(0, len(BLOB), 65536):
        yield BLOB[i:i + 65536]


@pytest.mark.asyncio
async def test_an_upload_streams_its_body_with_the_session(conn, server):  # noqa: F811
    await connect(conn, server)
    async with conn.stream("PUT", UPLOAD, content=pieces()) as resp:
        await resp.aread()
        assert resp.status_code == 200 and resp.json() == {"received_bytes": len(BLOB)}
    assert server.received == [("application/octet-stream", "chunked", len(BLOB),
                                hashlib.sha256(BLOB).hexdigest())]


@pytest.mark.asyncio
async def test_a_download_is_read_as_it_arrives(conn, server):  # noqa: F811
    await connect(conn, server)
    got = bytearray()
    async with conn.stream("GET", DOWNLOAD) as resp:
        assert resp.status_code == 200
        async for chunk in resp.aiter_bytes():
            got += chunk
    assert bytes(got) == BLOB


@pytest.mark.asyncio
async def test_a_401_on_a_stream_ends_the_session_like_any_call(conn, server):  # noqa: F811
    await connect(conn, server)
    server.valid_tokens.clear()
    async with conn.stream("GET", DOWNLOAD) as resp:
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "session_revoked"
    assert conn.state() == "signed_out"
    with pytest.raises(TeamError):
        async with conn.stream("GET", DOWNLOAD):
            pass


@pytest.mark.asyncio
async def test_no_answer_and_redirects_are_team_errors(conn, server):  # noqa: F811
    await connect(conn, server)
    server.down = True
    with pytest.raises(TeamError) as e:
        async with conn.stream("PUT", UPLOAD, content=pieces()):
            pass
    assert e.value.unreachable
    server.down, server.redirect = False, True
    with pytest.raises(TeamError) as e:
        async with conn.stream("GET", DOWNLOAD):
            pass
    assert not e.value.unreachable


@pytest.mark.asyncio
async def test_a_stream_needs_a_connection(conn):  # noqa: F811
    with pytest.raises(TeamError):
        async with conn.stream("GET", DOWNLOAD):
            pass
