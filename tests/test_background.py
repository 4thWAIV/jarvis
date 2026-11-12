"""python -m jarvis.background: what it starts, finds and stops is decided by
identity, never by a pid alone. The real start, survival and stop run
natively in the Windows startup workflow; these check the decisions."""
import json
import os
import subprocess
import sys

from jarvis import background, data_paths, process_inspect


def test_nothing_recorded_is_not_running(capsys):
    assert background.status() == 3
    out = capsys.readouterr().out
    assert "backend: not running" in out and "vite: not running" in out


def test_start_refuses_while_a_port_is_taken(monkeypatch, capsys):
    monkeypatch.setattr(background, "_port_open", lambda port: port == background.VITE_PORT)
    spawned = []
    monkeypatch.setattr(background, "_spawn", lambda *a: spawned.append(a))
    assert background.start() == 1
    assert spawned == []
    assert "5173" in capsys.readouterr().out


def test_start_refuses_while_one_already_runs(monkeypatch, capsys):
    monkeypatch.setattr(background, "find_backend", lambda: background.Found(
        "backend", os.getpid(), process_inspect.State.ALIVE))
    spawned = []
    monkeypatch.setattr(background, "_spawn", lambda *a: spawned.append(a))
    assert background.start() == 1
    assert spawned == [] and "Already running" in capsys.readouterr().out


def test_a_vite_record_whose_process_is_gone_is_not_running():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    background._write_state({"vite": {"pid": child.pid, "start_token": None}})
    assert not background.find_vite().running


def test_stop_leaves_a_process_it_cannot_confirm_alone(monkeypatch):
    ended = []
    monkeypatch.setattr(background, "_end_tree", ended.append)
    found = background.Found("vite", 4242, process_inspect.State.UNKNOWN)
    assert background._stop_one(found) is False
    assert ended == []


def test_a_reused_pid_is_not_ours(monkeypatch):
    """A recorded creation token that differs from the process on that pid
    now: the recorded Vite has exited, and stop ends nothing."""
    me = os.getpid()
    background._write_state({"vite": {"pid": me, "start_token": "win-filetime:1"}})
    monkeypatch.setattr(process_inspect, "_inspect_posix", lambda pid: process_inspect.Inspection(
        process_inspect.State.ALIVE, process_inspect.Identity(pid, "win-filetime:2"), "running"))
    ended = []
    monkeypatch.setattr(background, "_end_tree", ended.append)
    found = background.find_vite()
    assert found.state is process_inspect.State.EXITED
    assert background.stop() == 0 and ended == []


def test_the_backend_is_the_process_the_server_recorded(monkeypatch):
    path = data_paths.server_address_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"scheme": "https", "host": "127.0.0.1", "port": 8340,
                                "pid": os.getpid()}))
    found = background.find_backend()
    assert found.running and found.pid == os.getpid()
    assert "https://127.0.0.1:8340" in found.line()
