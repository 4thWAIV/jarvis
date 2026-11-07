"""The knowledge engine's process, gate and supervision (build plan step 1).

The unit tests run the routes, the gate and the HTTP server in this
process; the tests marked `knowledge_engine` start the real engine."""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from jarvis import data_paths
from jarvis import knowledge_client as kc
from jarvis.knowledge import address as engine_address
from jarvis.knowledge import auth
from jarvis.knowledge.engine import Engine, EngineLocked, lock_is_free
from jarvis.knowledge.serve import EngineHTTPServer, dispatch


@pytest.fixture
def engine(tmp_path):
    e = Engine.open(tmp_path / "knowledge")
    yield e
    e.close()


# --- the gate -------------------------------------------------------------

def _headers(**kw):
    return {k.replace("_", "-").title() if k != "Host" else k: v for k, v in kw.items()}


def test_gate_admits_loopback_host_with_the_token():
    assert auth.check({"Host": "127.0.0.1:4100", "Authorization": "Bearer t0k"}, 4100, "t0k") is None
    assert auth.check({"Host": "localhost:4100", "Authorization": "Bearer t0k"}, 4100, "t0k") is None


@pytest.mark.parametrize("headers, code", [
    ({"Host": "127.0.0.1:4100"}, "token_refused"),
    ({"Host": "127.0.0.1:4100", "Authorization": "Bearer nope"}, "token_refused"),
    ({"Host": "127.0.0.1:4100", "Authorization": "Basic t0k"}, "token_refused"),
    ({"Host": "127.0.0.1:4100", "Authorization": "Bearer t0k", "Origin": "http://localhost:5173"}, "origin_refused"),
    ({"Host": "127.0.0.1:4100", "Authorization": "Bearer t0k", "Origin": "null"}, "origin_refused"),
    ({"Host": "evil.example:4100", "Authorization": "Bearer t0k"}, "host_refused"),
    ({"Host": "127.0.0.1:8340", "Authorization": "Bearer t0k"}, "host_refused"),
    ({"Authorization": "Bearer t0k"}, "host_refused"),
])
def test_gate_refuses(headers, code):
    refusal = auth.check(headers, 4100, "t0k")
    assert refusal is not None and refusal.code == code


def test_gate_refuses_everything_without_a_token_configured():
    assert auth.check({"Host": "127.0.0.1:1", "Authorization": "Bearer "}, 1, "").code == "token_refused"


# --- routes ---------------------------------------------------------------

def test_health_and_pace_routes(engine):
    assert dispatch(engine, "GET", "/v1/health").payload["state"] == "ok"
    assert dispatch(engine, "POST", "/v1/pace", {"mode": "hold"}).payload == {"pace": "hold"}
    assert dispatch(engine, "POST", "/v1/pace", {"mode": "sideways"}).status == 400
    assert dispatch(engine, "GET", "/v1/pace").status == 405
    assert dispatch(engine, "GET", "/v1/nothing").status == 404


def test_a_second_engine_on_one_directory_refuses(engine, tmp_path):
    with pytest.raises(EngineLocked):
        Engine.open(tmp_path / "knowledge")
    assert not lock_is_free(tmp_path / "knowledge")


def test_closing_frees_the_lock(tmp_path):
    e = Engine.open(tmp_path / "k")
    e.close()
    assert lock_is_free(tmp_path / "k")
    Engine.open(tmp_path / "k").close()


def test_the_directory_is_private(engine):
    assert (engine.directory.stat().st_mode & 0o777) == 0o700


# --- HTTP, in this process --------------------------------------------------

@pytest.fixture
def http_engine(engine):
    server = EngineHTTPServer(engine, "secret-token")
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _get(server, headers):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.putrequest("GET", "/v1/health", skip_host=True)
    for k, v in headers.items():
        conn.putheader(k, v)
    conn.endheaders()
    r = conn.getresponse()
    body = json.loads(r.read())
    conn.close()
    return r.status, body


def test_http_listens_on_loopback_only(http_engine):
    assert http_engine.server_address[0] == "127.0.0.1"


def test_http_gate(http_engine):
    port = http_engine.server_address[1]
    ok = {"Host": f"127.0.0.1:{port}", "Authorization": "Bearer secret-token"}
    assert _get(http_engine, ok)[0] == 200
    assert _get(http_engine, {"Host": f"127.0.0.1:{port}"}) == (401, {"error": "token_refused"})
    assert _get(http_engine, {**ok, "Origin": "http://localhost:5173"})[0] == 403
    assert _get(http_engine, {**ok, "Host": f"rebound.example:{port}"})[0] == 421
    status, body = _get(http_engine, ok)
    assert "Access-Control-Allow-Origin" not in body


def test_the_client_sends_its_token_only_to_its_own_child(http_engine, monkeypatch, tmp_path):
    host, port = http_engine.server_address[:2]
    monkeypatch.setattr(data_paths, "ensure_knowledge_token", lambda: "secret-token")
    engine_address.record(engine_address.EngineAddress("http", host, port, os.getpid(), "b"))
    sent = []

    def token():
        sent.append(True)
        return "secret-token"
    stranger = kc.KnowledgeClient(kc.HTTPTransport(lambda: os.getpid() + 1, token=token))
    with pytest.raises(kc.EngineUnavailable):
        stranger.health()
    assert sent == []
    nobody = kc.KnowledgeClient(kc.HTTPTransport(lambda: None, token=token))
    with pytest.raises(kc.EngineUnavailable):
        nobody.health()
    assert sent == []
    ours = kc.KnowledgeClient(kc.HTTPTransport(os.getpid, token=token))
    assert ours.health()["state"] == "ok"
    assert sent == [True]


def test_the_address_record_is_private_and_forgotten_only_by_its_engine(tmp_path):
    engine_address.record(engine_address.EngineAddress("http", "127.0.0.1", 1, os.getpid(), "mine"))
    path = data_paths.knowledge_address_path()
    assert (path.stat().st_mode & 0o777) == 0o600
    engine_address.forget("someone-else")
    assert path.exists()
    engine_address.forget("mine")
    assert not path.exists()


def test_the_knowledge_token_is_not_the_tool_token():
    assert data_paths.ensure_knowledge_token() != data_paths.ensure_tool_token()
    assert (data_paths.knowledge_token_path().stat().st_mode & 0o777) == 0o600
    assert data_paths.ensure_knowledge_token() == data_paths.ensure_knowledge_token()


def test_the_engine_imports_nothing_from_the_server():
    code = ("import sys, jarvis.knowledge.serve, jarvis.knowledge.__main__;"
            "bad = [m for m in ('jarvis.server', 'fastapi', 'starlette', 'uvicorn') if m in sys.modules];"
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parents[1])
    assert done.returncode == 0, done.stdout + done.stderr


def test_client_maps_errors(engine):
    client = kc.KnowledgeClient(kc.InProcessTransport(engine))
    assert client.health()["state"] == "ok"
    with pytest.raises(kc.KnowledgeRefused):
        client.call("GET", "/v1/nothing")


# --- supervision policy ----------------------------------------------------

class FakeProc:
    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self.stdin = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def no_previous(monkeypatch):
    monkeypatch.setattr(kc, "end_previous_engine", lambda **kw: None)


def _supervisor(clock, healthy=lambda: True, footprint=lambda pid: 10 << 20):
    spawned = []

    def spawn():
        spawned.append(FakeProc(100 + len(spawned)))
        return spawned[-1]

    def health():
        if not healthy():
            raise kc.EngineUnavailable("late")
        return {"state": "ok"}
    sup = kc.Supervisor(spawn=spawn, clock=clock, health=health, footprint=footprint, budget_mb=100)
    return sup, spawned


def test_restarts_with_backoff_then_stays_down(no_previous):
    clock = Clock()
    sup, spawned = _supervisor(clock)
    sup.start()
    delays = []
    for _ in range(3):
        spawned[-1].returncode = 1
        assert sup.tick() == "restarting"
        start = clock.t
        while sup.state == "restarting":
            clock.t += 0.5
            sup.tick()
        delays.append(round(clock.t - start))
    assert delays == [1, 5, 30]
    spawned[-1].returncode = 1
    assert sup.tick() == "down"
    assert len(spawned) == 4


def test_restarts_spread_over_more_than_ten_minutes_never_give_up(no_previous):
    clock = Clock()
    sup, spawned = _supervisor(clock)
    sup.start()
    for _ in range(6):
        spawned[-1].returncode = 1
        sup.tick()
        clock.t += kc.RESTART_WINDOW_SEC + 1
        sup.tick()
        assert sup.state == "running"


def test_two_failed_pings_restart_a_hung_engine(no_previous):
    clock = Clock()
    alive = {"ok": True}
    sup, spawned = _supervisor(clock, healthy=lambda: alive["ok"])
    sup.start()
    alive["ok"] = False
    assert sup.tick() == "running"
    assert sup.tick() == "restarting"
    assert spawned[0].returncode is not None
    assert "health" in sup.last_reason


def test_footprint_over_the_limit_restarts(no_previous):
    clock = Clock()
    sup, spawned = _supervisor(clock, footprint=lambda pid: 151 << 20)
    sup.start()
    assert sup.tick() == "restarting"
    sup2, _ = _supervisor(clock, footprint=lambda pid: 149 << 20)
    sup2.start()
    assert sup2.tick() == "running"


def test_footprint_reads_a_process():
    if sys.platform != "darwin":
        pytest.skip("macOS only")
    used = kc.footprint_bytes(os.getpid())
    assert used is not None and used > 1 << 20


def test_memory_budget_setting(monkeypatch):
    monkeypatch.setenv("JARVIS_KNOWLEDGE_MEMORY_MB", "123")
    assert kc.memory_budget_mb() == 123
    monkeypatch.setenv("JARVIS_KNOWLEDGE_MEMORY_MB", "")
    assert kc.memory_budget_mb() in (256, 384)


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_KNOWLEDGE", raising=False)
    assert not kc.enabled()


# --- the real process --------------------------------------------------------

def _wait(predicate, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.mark.knowledge_engine
def test_real_engine_serves_refuses_a_second_and_exits_with_its_pipe():
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen([sys.executable, "-m", "jarvis.knowledge", "serve"],
                            stdin=subprocess.PIPE, cwd=repo)
    try:
        assert _wait(lambda: (a := engine_address.running()) is not None and a.pid == proc.pid)
        client = kc.KnowledgeClient(kc.HTTPTransport(lambda: proc.pid))
        assert client.health()["pid"] == proc.pid
        second = subprocess.run([sys.executable, "-m", "jarvis.knowledge", "serve"],
                                stdin=subprocess.DEVNULL, cwd=repo, capture_output=True, timeout=30)
        assert second.returncode == 3
        proc.stdin.close()
        assert proc.wait(timeout=20) == 0
        assert lock_is_free(data_paths.knowledge_dir())
        assert engine_address.read() is None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.knowledge_engine
def test_end_previous_engine_ends_one_that_keeps_the_lock():
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen([sys.executable, "-m", "jarvis.knowledge", "serve", "--no-stdin-watch"],
                            stdin=subprocess.PIPE, cwd=repo)
    try:
        assert _wait(lambda: engine_address.running() is not None)
        kc.end_previous_engine(wait_sec=0.3, grace_sec=10)
        assert proc.wait(timeout=20) is not None
        assert lock_is_free(data_paths.knowledge_dir())
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.knowledge_engine
def test_the_engine_opens_no_file_outside_its_own_directory(tmp_path):
    """I2: besides the code and system libraries it loads, every file the
    running engine holds open is inside its own directory."""
    import shutil
    if shutil.which("lsof") is None:
        pytest.skip("no lsof")
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen([sys.executable, "-m", "jarvis.knowledge", "serve"],
                            stdin=subprocess.PIPE, cwd=repo)
    try:
        assert _wait(lambda: (a := engine_address.running()) is not None and a.pid == proc.pid)
        client = kc.KnowledgeClient(kc.HTTPTransport(lambda: proc.pid))
        client.call("POST", "/v1/txn", {"puts": [{"_key": "chunk:1", "_kind": "chunk", "text": "hello"}]})
        client.call("POST", "/v1/snapshot", {})
        listed = subprocess.run(["lsof", "-Fn", "-p", str(proc.pid)], capture_output=True, text=True).stdout
        paths = [line[1:] for line in listed.splitlines() if line.startswith("n/")]
        own = str(data_paths.knowledge_dir().resolve())
        allowed = ("/System/", "/usr/lib/", "/usr/share/", "/Library/Preferences/", "/private/var/db/",
                   "/dev/", os.path.realpath(sys.base_prefix), os.path.realpath(sys.prefix), str(repo))
        strays = [p for p in paths
                  if not (os.path.realpath(p).startswith(own)
                          or p.endswith((".dylib", ".so"))          # libraries the loader mapped
                          # anonymous temporary files a library makes for itself
                          # (libffi's closure pages under ctypes), never user data
                          or (os.path.realpath(p).startswith(os.path.realpath(tempfile.gettempdir()))
                              and os.path.basename(p).startswith("tmp"))
                          or any(os.path.realpath(p).startswith(a) or p.startswith(a) for a in allowed))]
        assert strays == [], strays
        assert any(os.path.realpath(p).startswith(own) for p in paths)
    finally:
        proc.stdin.close()
        proc.wait(timeout=20)
