"""JARVIS answering while its records move (`jarvis/records_move.py`): the
server serves at once, says where the move is and the time left, answers
every route that needs the records 503 with that reason, and once the move
is done starts everything else; a move that fails leaves it up saying why.
The move's plan refuses too little free disk before anything is written."""
from __future__ import annotations

import asyncio
import sqlite3
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from jarvis import data_paths, records_cutover, records_move
from jarvis import knowledge_move as move

PAGE = {"Origin": "http://localhost:5173"}


@pytest.fixture(autouse=True)
def _ready_after():
    records_move.reset()
    yield
    records_move.reset()


@pytest.fixture
def jarvis_db(monkeypatch):
    monkeypatch.setattr(move, "RECORD_MODULES", ())
    path = data_paths.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE things (id TEXT PRIMARY KEY, n INTEGER);
        INSERT INTO things VALUES ('a', 1), ('b', 2), ('c', 3);
    """)
    conn.commit()
    conn.close()
    return path


def _gated_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(records_move.Gate)
    records_move.mount(app)

    @app.get("/api/health")
    async def health():
        return {"status": "online"}

    @app.get("/api/runs")
    async def runs():
        return {"runs": []}

    @app.websocket("/ws/voice")
    async def voice(ws):
        await ws.accept()
        await ws.close()

    return app


def test_the_plan_counts_rows_times_each_step_and_weighs_the_disk(jarvis_db):
    plan = records_cutover.plan(jarvis_db)
    assert (plan.tables, plan.rows) == (1, 3)
    assert set(plan.expected) == {records_cutover.COPY, records_cutover.CHECK, records_cutover.CHECK_BACK}
    assert plan.needed_bytes == records_cutover.space_needed(plan.file_bytes) > plan.file_bytes
    assert plan.free_bytes > 0


def test_too_little_free_disk_refuses_the_move_before_anything_is_written(jarvis_db, monkeypatch):
    monkeypatch.setattr(records_cutover, "free_space", lambda folder: 10 << 20)
    steps = []
    with pytest.raises(records_cutover.NotEnoughSpace) as refused:
        records_cutover.cut_over(snapshot=False, on_step=lambda s, f: steps.append(s))
    assert "free disk" in str(refused.value) and "untouched" in str(refused.value)
    assert steps == [records_cutover.SPACE]
    assert not move.held(records_cutover.records.uds())


def test_each_step_is_told_as_it_begins(jarvis_db):
    steps = []
    records_cutover.cut_over(snapshot=False, on_step=lambda s, f: steps.append(s))
    assert steps == [records_cutover.SPACE, records_cutover.COPY, records_cutover.CHECK,
                     records_cutover.CHECK_BACK]


def test_while_moving_the_records_routes_answer_503_and_the_page_is_told_where_it_is():
    records_move.begin()
    records_move._on_step(records_cutover.SPACE, {"expected": {"copy": 60.0, "check": 40.0,
                                                               "check_back": 50.0},
                                                  "clearing": False})
    records_move._on_step(records_cutover.COPY, {})
    with TestClient(_gated_app()) as client:
        st = client.get("/api/records/move").json()
        assert st["state"] == "moving" and st["step"] == "copy"
        assert 140 < st["left"] <= 150 and "safe to stop" in st["safe_to_stop"]
        assert st["steps"] == ["space", "copy", "check", "check_back"]
        assert client.get("/api/health").status_code == 200
        r = client.get("/api/runs")
        assert r.status_code == 503 and r.json()["error"] == "records_moving"
        assert int(r.headers["Retry-After"]) >= 5
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/ws/voice") as ws:
                ws.receive_text()
        assert closed.value.code == 1013
        records_move.failed("CutoverFailed: the records differ")
        r = client.get("/api/runs")
        assert r.status_code == 503 and r.json()["error"] == "records_move_failed"
        assert "the records differ" in r.json()["detail"]
        records_move.started()
        assert client.get("/api/runs").status_code == 200


@pytest.mark.asyncio
async def test_run_moves_in_the_background_then_says_starting():
    release = threading.Event()

    def cut_over(on_step):
        on_step(records_cutover.SPACE, {"expected": {"copy": 1.0}, "clearing": False})
        on_step(records_cutover.COPY, {})
        release.wait(5)
        return records_cutover.Outcome("cut over", {"total": 1.0}, 3, 1)


    records_move.begin()
    task = asyncio.create_task(records_move.run(cut_over))
    for _ in range(100):
        if records_move.status()["step"] == "copy":
            break
        await asyncio.sleep(0.01)
    assert records_move.status()["state"] == "moving"
    release.set()
    assert await task is True
    st = records_move.status()
    assert st["state"] == "starting" and "copy" in st["done_steps"]


@pytest.mark.asyncio
async def test_a_failed_move_leaves_the_server_saying_why():
    def cut_over(on_step):
        raise records_cutover.CutoverFailed("a file exported from the engine differs from jarvis.db")

    records_move.begin()
    assert await records_move.run(cut_over) is False
    st = records_move.status()
    assert st["state"] == "failed" and "differs from jarvis.db" in st["reason"]
    assert "failed" in records_move.said() and "untouched" in records_move.said()


def test_the_server_answers_during_the_move_and_starts_the_rest_after(jarvis_db, monkeypatch):
    from jarvis import server
    release = threading.Event()
    real = records_cutover.cut_over
    began = []

    def slow(**kw):
        began.append(time.monotonic())
        release.wait(10)
        return real(snapshot=False, **kw)

    monkeypatch.setattr(records_cutover, "cut_over", slow)
    with TestClient(server.app, headers=PAGE) as client:
        st = client.get("/api/records/move").json()
        assert st["state"] == "moving"
        r = client.get("/api/runs")
        assert r.status_code == 503 and r.json()["error"] == "records_moving"
        release.set()
        for _ in range(500):
            if client.get("/api/records/move").json()["state"] == "ready":
                break
            time.sleep(0.02)
        assert client.get("/api/records/move").json()["state"] == "ready"
        assert client.get("/api/runs").status_code == 200
        assert "proven both ways" in server.records_move.said(records_cutover.done())
    assert began


@pytest.mark.asyncio
async def test_the_brain_reads_the_move_as_the_page_does(jarvis_db):
    from jarvis import server
    records_move.begin()
    records_move._on_step(records_cutover.SPACE, {"expected": {"copy": 30.0}, "clearing": False})
    records_move._on_step(records_cutover.COPY, {})
    said = await server.tool_records_move({})
    assert "moving into the engine" in said and "safe to stop" in said
    records_move.reset()
    records_cutover.cut_over(snapshot=False)
    said = await server.tool_records_move({})
    assert "3 rows of 1 tables, proven both ways" in said
    assert "records_move" in server.TAINT_EXEMPT_TOOLS
