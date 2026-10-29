"""The network: node updates, ownership, ordering, presence, brain summaries
and the server's own health."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect

from conftest import (ADMIN, ALICE, BASE, browser_token, make_config, pair,
                      wait_initialized)
from teamserver import clock, network
from teamserver.app import create_app
from teamserver.models import NetworkSnapshot, ServerHealth

PATH = "/api/v1/network/nodes/self"
CLAUDE = "acct-claude-000000001"
OPENAI = "acct-openai-000000001"


def week(used, expected=None, resets_in=timedelta(days=3), age_ms=0):
    return {"window": "seven_day", "used_percent": used,
            "expected_used_percent": expected,
            "resets_at": (clock.now() + resets_in).isoformat(), "age_ms": age_ms}


def at(text):
    return datetime.fromisoformat(text)


def brain(ref=CLAUDE, provider="anthropic", role="brain", state="measured",
          windows=None):
    return {"account_ref": ref, "provider": provider, "role": role, "state": state,
            "windows": windows if windows is not None else [week(40.0, 50.0)]}


def update(node_id, boot_id, seq=1, **over):
    body = {"node_id": str(node_id), "boot_id": str(boot_id), "seq": seq,
            "label": "TIM'S MAC MINI", "jarvis_version": "1.0.0", "os": "macOS 26.1",
            "resources": {"state": "measured", "scope": "host", "cpu_percent": 12.5,
                          "memory_used_bytes": 8 << 30, "memory_total_bytes": 16 << 30,
                          "age_ms": 400},
            "brains": [], "activity": None}
    body.update(over)
    return body


class Node:
    """One JARVIS backend: a device session, a node ID, a boot, a sequence."""

    def __init__(self, client, token):
        self.client, self.token = client, token
        self.node_id, self.boot_id, self.seq = uuid.uuid4(), uuid.uuid4(), 0

    def send(self, **over):
        self.seq += 1
        body = {**update(self.node_id, self.boot_id, self.seq), **over}
        return self.client.post(PATH, json=body,
                                headers={"Authorization": f"Bearer {self.token}"})

    def snapshot(self, **over) -> dict:
        r = self.send(**over)
        assert r.status_code == 200, r.text
        return r.json()

    def view(self, snap: dict) -> dict:
        return next(n for n in snap["nodes"] if n["node_id"] == str(self.node_id))


def add_member(client, admin, identity):
    roster = client.get("/api/v1/team/members", headers={"Authorization": f"Bearer {admin}"})
    r = client.put(f"/api/v1/team/members/{identity.id}",
                   headers={"Authorization": f"Bearer {admin}"},
                   json={"expected_revision": roster.json()["revision"]})
    assert r.status_code == 200, r.text


@pytest.fixture
def admin(client, github):
    return browser_token(client, github, ADMIN)


@pytest.fixture
def node(client, admin):
    return Node(client, pair(client, admin))


@pytest.fixture
def alice(client, github, admin):
    add_member(client, admin, ALICE)
    return Node(client, pair(client, browser_token(client, github, ALICE)))


# -- the exchange ----------------------------------------------------------------

def test_self_node_comes_back_in_the_snapshot(node):
    first = node.snapshot()
    NetworkSnapshot.model_validate(first)
    assert first["you_node_id"] == str(node.node_id)
    mine = node.view(first)
    assert mine["owner_login"] == ADMIN.login and mine["owner_github_id"] == ADMIN.id
    assert mine["presence"] == "online" and mine["label"] == "TIM'S MAC MINI"
    assert mine["resources"]["cpu_percent"] == 12.5
    assert mine["activity_state"] == "never_observed"
    assert mine["last_human_activity_at"] is None
    assert first["server"]["database"] == "connected"
    assert first["server"]["last_write_ok_at"]
    assert node.snapshot()["revision"] == first["revision"] + 1


def test_two_nodes_of_one_person_appear_separately(client, admin, node):
    other = Node(client, pair(client, admin))
    node.snapshot()
    snap = other.snapshot()
    assert {n["node_id"] for n in snap["nodes"]} == {str(node.node_id), str(other.node_id)}
    assert snap["you_node_id"] == str(other.node_id)


def test_browser_session_cannot_publish(client, admin):
    r = client.post(PATH, json=update(uuid.uuid4(), uuid.uuid4()),
                    headers={"Authorization": f"Bearer {admin}"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "device_required"


def test_no_session_refused(client):
    r = client.post(PATH, json=update(uuid.uuid4(), uuid.uuid4()))
    assert r.status_code == 401


# -- ownership -------------------------------------------------------------------

def test_forged_owner_is_ignored_and_another_members_node_refused(client, node, alice):
    snap = node.snapshot(owner_github_id=ALICE.id, owner_login=ALICE.login)
    assert node.view(snap)["owner_github_id"] == ADMIN.id
    alice.node_id = node.node_id
    r = alice.send()
    assert r.status_code == 403 and r.json()["error"]["code"] == "node_not_yours"
    assert node.view(node.snapshot())["owner_github_id"] == ADMIN.id


def test_another_teams_session_refused(client, node, github, mongo):
    other_db = "ts_test_" + uuid.uuid4().hex[:12]
    try:
        with TestClient(create_app(make_config(other_db), github=github),
                        base_url=BASE) as other:
            wait_initialized(other)
            stranger = Node(other, pair(other, browser_token(other, github, ADMIN)))
            assert stranger.send().status_code == 200
            stranger.client = client
            r = stranger.send()
            assert r.status_code == 401
    finally:
        mongo.drop_database(other_db)
    assert len(node.snapshot()["nodes"]) == 1


def test_suspended_member_refused_and_their_nodes_withdrawn(client, admin, node, alice):
    alice.snapshot()
    assert len(node.snapshot()["nodes"]) == 2
    roster = client.get("/api/v1/team/members", headers={"Authorization": f"Bearer {admin}"})
    r = client.put(f"/api/v1/team/members/{ALICE.id}",
                   headers={"Authorization": f"Bearer {admin}"},
                   json={"expected_revision": roster.json()["revision"], "status": "suspended"})
    assert r.status_code == 200, r.text
    assert alice.send().status_code in (401, 403)
    assert [n["owner_login"] for n in node.snapshot()["nodes"]] == [ADMIN.login]


def test_node_limit_per_member(client, admin, node, monkeypatch):
    monkeypatch.setattr(network, "NODES_PER_MEMBER", 2)
    node.snapshot()
    Node(client, node.token).snapshot()
    r = Node(client, node.token).send()
    assert r.status_code == 409 and r.json()["error"]["code"] == "node_limit"


# -- ordering ----------------------------------------------------------------------

def test_older_sequence_and_older_boot_refused(node):
    node.seq = 4
    node.snapshot()                      # boot A, seq 5
    node.seq = 4
    r = node.send()                      # seq 5 again
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_sequence"
    node.seq = 3
    assert node.send().status_code == 409  # seq 4
    old_boot = node.boot_id
    node.boot_id, node.seq = uuid.uuid4(), 0
    node.snapshot()                      # boot B, seq 1: a new run starts again
    node.boot_id, node.seq = old_boot, 10
    r = node.send()
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_boot"


def test_repeated_activity_seq_does_not_move_the_time(node, frozen):
    start = frozen.t
    snap = node.snapshot(activity={"activity_seq": 3, "age_ms": 2000, "source": "gui"})
    first = node.view(snap)
    assert first["activity_state"] == "available"
    assert first["last_human_activity_source"] == "gui"
    assert at(first["last_human_activity_at"]) == start - timedelta(seconds=2)
    frozen.advance(seconds=10)
    again = node.view(node.snapshot(
        activity={"activity_seq": 3, "age_ms": 0, "source": "speech"}))
    assert again["last_human_activity_at"] == first["last_human_activity_at"]
    assert again["last_human_activity_source"] == "gui"
    newer = node.view(node.snapshot(
        activity={"activity_seq": 4, "age_ms": 1000, "source": "typed"}))
    assert at(newer["last_human_activity_at"]) == start + timedelta(seconds=9)
    # A new sequence carrying an older time never moves it back.
    older = node.view(node.snapshot(
        activity={"activity_seq": 5, "age_ms": 60_000, "source": "gui"}))
    assert older["last_human_activity_at"] == newer["last_human_activity_at"]
    # No activity in an update leaves it as it was.
    assert node.view(node.snapshot())["last_human_activity_at"] == newer["last_human_activity_at"]


# -- limits ----------------------------------------------------------------------

@pytest.mark.parametrize("over", [
    {"label": ""},
    {"label": "x" * 65},
    {"os": "x" * 41},
    {"jarvis_version": "x" * 33},
    {"seq": -1},
    {"node_id": "not-a-uuid"},
    {"resources": {"state": "measured", "scope": "host", "cpu_percent": 101, "age_ms": 0}},
    {"resources": {"state": "measured", "scope": "host", "age_ms": 60_001}},
    {"resources": {"state": "measured", "scope": "host", "memory_total_bytes": 0,
                   "age_ms": 0}},
    {"resources": {"state": "fine", "scope": "host", "age_ms": 0}},
    {"brains": [brain(ref=f"acct-{i:016d}") for i in range(9)]},
    {"brains": [brain(ref="tim@example.com")]},
    {"brains": [brain(windows=[week(40.0)] * 5)]},
    {"brains": [brain(windows=[week(101.0)])]},
    {"brains": [brain(windows=[week(40.0, age_ms=-1)])]},
    {"brains": [brain(provider="gemini")]},
    {"activity": {"activity_seq": 1, "age_ms": 86_400_001, "source": "gui"}},
    {"activity": {"activity_seq": 1, "age_ms": -1, "source": "gui"}},
    {"activity": {"activity_seq": -1, "age_ms": 0, "source": "gui"}},
    {"activity": {"activity_seq": 1, "age_ms": 0, "source": "agent"}},
])
def test_out_of_range_payloads_refused(node, over):
    r = node.send(**over)
    assert r.status_code == 422, r.text


def test_oversized_payload_refused(node):
    r = node.send(label="x" * 20_000)
    assert r.status_code == 413


# -- presence --------------------------------------------------------------------

@pytest.mark.parametrize("after,expected", [(15, "online"), (16, "delayed"),
                                            (45, "delayed"), (46, "offline")])
def test_presence_boundaries(client, node, frozen, after, expected):
    node.snapshot()
    watcher = Node(client, node.token)
    frozen.advance(seconds=after)
    assert node.view(watcher.snapshot())["presence"] == expected


# -- brain summary -----------------------------------------------------------------

def summary(node, brains):
    return node.view(node.snapshot(brains=brains))["brains"]


def test_brain_summary_averages_40_and_60(node):
    s = summary(node, [brain(CLAUDE, "anthropic", windows=[week(40.0, 50.0)]),
                       brain(OPENAI, "openai", role="standby",
                             windows=[week(60.0, 50.0)])])
    assert (s["configured"], s["measured"]) == (2, 2)
    assert s["average_remaining_percent"] == 50.0
    assert s["pace_points"] == 0.0
    assert [a["role"] for a in s["accounts"]] == ["brain", "standby"]


def test_one_of_two_measured_reads_as_one_of_two(node):
    s = summary(node, [brain(CLAUDE, windows=[week(40.0, 50.0)]),
                       brain(OPENAI, "openai", state="not_measured", windows=[])])
    assert (s["configured"], s["measured"]) == (2, 1)
    assert s["average_remaining_percent"] == 60.0
    unmeasured = s["accounts"][1]
    assert unmeasured["remaining_percent"] is None and unmeasured["pace_points"] is None


def test_no_brains(node):
    s = summary(node, [])
    assert s == {"configured": 0, "measured": 0, "average_remaining_percent": None,
                 "pace_points": None, "accounts": []}


def test_window_past_its_reset_counts_as_unmeasured(node):
    s = summary(node, [brain(windows=[week(40.0, 50.0, resets_in=timedelta(seconds=-1))])])
    assert (s["configured"], s["measured"]) == (1, 0)


def test_five_hour_window_alone_is_not_weekly(node):
    five = dict(week(90.0), window="five_hour")
    s = summary(node, [brain(windows=[five])])
    assert s["measured"] == 0


def test_shared_account_counts_once_and_never_adds_across_nodes(client, node):
    s = summary(node, [brain(CLAUDE), brain(CLAUDE, role="standby")])
    assert s["configured"] == 1
    other = Node(client, node.token)
    snap = other.snapshot(brains=[brain(CLAUDE)])
    a, b = node.view(snap)["brains"], other.view(snap)["brains"]
    assert a["configured"] == b["configured"] == 1
    assert a["average_remaining_percent"] == b["average_remaining_percent"] == 60.0


def test_pace_and_end_of_week(node, frozen):
    half = timedelta(hours=84)          # half the week gone: an even pace is 50%
    s = summary(node, [
        brain(CLAUDE, windows=[week(30.0, 50.0, resets_in=half)]),
        brain(OPENAI, "openai", windows=[week(80.0, 50.0, resets_in=half, age_ms=5000)]),
    ])
    claude, openai = s["accounts"]
    assert claude["pace_points"] == 20.0 and claude["remaining_percent"] == 70.0
    assert claude["left_at_reset_percent"] == 40.0
    assert claude["runs_out_hours_before_reset"] is None
    assert at(claude["resets_at"]) == frozen.t + half
    assert claude["age_ms"] == 0
    assert openai["pace_points"] == -30.0 and openai["left_at_reset_percent"] is None
    # 80% in 84h is 20% more in 21h: out 63h before the reset.
    assert openai["runs_out_hours_before_reset"] == 63.0
    assert openai["age_ms"] == 5000
    assert s["pace_points"] == -5.0
    frozen.advance(seconds=30)
    later = node.view(Node(node.client, node.token).snapshot())["brains"]["accounts"][1]
    assert later["age_ms"] == 35_000     # quota freshness never refreshed by a heartbeat


# -- the server's health -------------------------------------------------------------

def test_database_failure_answers_503_with_health_alone(node, monkeypatch):
    node.snapshot()

    async def down(*a, **kw):
        raise AutoReconnect("connection refused to a.b.c")
    monkeypatch.setattr(network, "store_node", down)
    r = node.send()
    assert r.status_code == 503
    health = ServerHealth.model_validate(r.json())
    assert health.database == "unreachable"
    assert health.last_database_error == "AutoReconnect"
    assert health.database_failures_in_a_row == 1
    assert health.last_write_ok_at is not None
    assert "a.b.c" not in r.text
    monkeypatch.undo()
    recovered = node.snapshot()["server"]
    assert recovered["database"] == "connected"
    assert recovered["database_failures_in_a_row"] == 0
    assert recovered["last_database_error"] == "AutoReconnect"


def test_session_lookup_failure_also_answers_with_health(node, monkeypatch):
    from teamserver import sessions

    async def down(*a, **kw):
        raise AutoReconnect("x")
    monkeypatch.setattr(sessions, "authenticate", down)
    r = node.send()
    assert r.status_code == 503
    assert ServerHealth.model_validate(r.json()).database == "unreachable"


class FakeDb:
    def __init__(self):
        self.up = True

    async def command(self, name):
        if not self.up:
            raise AutoReconnect("down")
        return {"ok": 1}


def test_health_history_one_point_per_interval(frozen, monkeypatch):
    health = network.HealthMonitor()
    db = FakeDb()

    async def run():
        await health.tick(db)                    # schedules the first point
        assert health.database == "connected" and health.ping_ms is not None
        health.wrote(clock.now())
        frozen.advance(seconds=30)
        await health.tick(db)
        db.up = False
        health._checked_mono = None              # the next beat probes again
        health.failed(AutoReconnect("x"), write=True)
        frozen.advance(seconds=30)
        await health.tick(db)
        for _ in range(40):
            frozen.advance(seconds=30)
            await health.tick(db)
    asyncio.run(run())
    report = health.report()
    assert len(report.history) == network.HISTORY_POINTS
    assert report.database == "unreachable" and report.database_ping_ms is None
    assert report.last_database_error == "AutoReconnect"


def test_health_history_records_write_results():
    health = network.HealthMonitor()
    db = FakeDb()

    async def run(clock_):
        await health.tick(db)
        health.wrote(clock_())
        health._point_due = clock_()
        await health.tick(db)
        health.failed(AutoReconnect("x"), write=True)
        health.wrote(clock_())
        health._point_due = clock_()
        await health.tick(db)
        health._point_due = clock_()
        await health.tick(db)
    asyncio.run(run(clock.now))
    assert [p.write_ok for p in health.history] == [True, False, None]


# -- CPU and memory --------------------------------------------------------------

class Mono:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_container_resources_with_scope(tmp_path):
    cg = "sys/fs/cgroup/"
    write(tmp_path, cg + "memory.max", "1073741824\n")
    write(tmp_path, cg + "memory.current", "600000000\n")
    write(tmp_path, cg + "memory.stat", "anon 1\ninactive_file 100000000\n")
    write(tmp_path, cg + "cpu.max", "200000 100000\n")
    write(tmp_path, cg + "cpu.stat", "usage_usec 10000000\nuser_usec 1\n")
    mono = Mono()
    s = network.ResourceSampler(root=tmp_path, monotonic=mono)
    s.sample()
    first = s.resources()
    assert first.state == "warming_up" and first.cpu_percent is None
    assert first.scope == "container" and first.memory_used_bytes == 500_000_000
    assert first.memory_total_bytes == 1_073_741_824
    mono.t += 5
    write(tmp_path, cg + "cpu.stat", "usage_usec 15000000\n")   # 5 s busy of 2 cpus x 5 s
    s.sample()
    mono.t += 2
    second = s.resources()
    assert second.state == "measured" and second.cpu_percent == 50.0
    assert second.age_ms == 2000


def test_host_resources_from_proc(tmp_path):
    write(tmp_path, "proc/meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n")
    write(tmp_path, "proc/stat", "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 1 1 1\n")
    s = network.ResourceSampler(root=tmp_path, monotonic=Mono())
    s.sample()
    write(tmp_path, "proc/stat", "cpu  130 0 130 740 100 0 0 0 0 0\n")
    s.sample()
    r = s.resources()
    assert r.scope == "host" and r.cpu_percent == 60.0
    assert r.memory_used_bytes == 600 * 1024 and r.memory_total_bytes == 1000 * 1024


def test_unsupported_and_failing_samplers(tmp_path):
    s = network.ResourceSampler(root=tmp_path, monotonic=Mono())
    s.sample()
    assert s.resources().state == "unsupported"
    write(tmp_path, "proc/meminfo", "garbage\n")
    write(tmp_path, "proc/stat", "cpu x\n")
    s.sample()
    assert s.resources().state == "error" and s.resources().cpu_percent is None


def test_resources_age_is_bounded(tmp_path):
    mono = Mono()
    s = network.ResourceSampler(root=tmp_path, monotonic=mono)
    s.sample()
    mono.t += 600
    assert s.resources().age_ms == 60_000
