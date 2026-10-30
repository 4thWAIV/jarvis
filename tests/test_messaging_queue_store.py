"""The Queue's record and what the page reads out of it
(frontend/src/queueStore.ts), on Tim's queue as JARVIS serves it: every
message really sent through the contract's fake team server and synced into
the local store (tests/messaging_queue_scenario.py), so the work states and
receipts follow the contract's own rules. Ordering, duplicates, late and
out-of-order answers, self-send, replies against related links, and the
safe fallbacks for people and JARVIS instances no longer listed.

Runs the real module under node, bundled with the esbuild Vite ships; skips
without node or the frontend's node_modules.
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire
from tests import messaging_queue_scenario as sc

ROOT = Path(__file__).parent.parent
ESBUILD = ROOT / "frontend" / "node_modules" / ".bin" / "esbuild"


@pytest.fixture(scope="module")
def world():
    return asyncio.run(sc.build())


@pytest.fixture(scope="module")
def got(world, tmp_path_factory):
    if not shutil.which("node") or not ESBUILD.exists():
        pytest.skip("needs node and frontend/node_modules")
    tmp = tmp_path_factory.mktemp("queue")
    snapshot = world.store.snapshot(sc.SERVER, sc.TIM, availability="available", stale=False)
    history = world.store.history(sc.SERVER, sc.TIM, None, ports.HISTORY_PAGE_DEFAULT)
    envelope = sc.network_envelope()["snapshot"]
    data = {
        "snapshot": snapshot.model_dump(mode="json"),
        "history": history.model_dump(mode="json"),
        "threads": {n: str(m.thread_id) for n, m in world.sent.items()},
        "messages": {n: str(m.message_id) for n, m in world.sent.items()},
        "directory": {
            "owner": sc.TIM,
            "members": [{"github_id": g, "login": login, "name": name}
                        for g, (login, name) in sc.ROSTER.items()],
            "nodes": [{k: n[k] for k in ("node_id", "owner_github_id", "owner_login", "label")}
                      for n in envelope["nodes"]],
            "selfNodeId": str(sc.TIM_I9),
        },
        "ids": {"MATT": sc.MATT, "ANA": sc.ANA, "MAX": sc.MAX, "FORMER": sc.FORMER,
                "GONE_NODE": str(sc.GONE_NODE)},
    }
    (tmp / "input.json").write_text(json.dumps(data))
    out = tmp / "cases.mjs"
    subprocess.run([str(ESBUILD), str(ROOT / "tests" / "js" / "queue_store_cases.ts"), "--bundle",
                    "--platform=node", "--format=esm", f"--outfile={out}", "--log-level=error"],
                   check=True)
    result = subprocess.run(["node", str(out), str(tmp / "input.json")], check=True,
                            capture_output=True, text=True)
    return json.loads(result.stdout) | {"_world": world, "_snapshot": snapshot}


def _order(world, placement):
    """The contract's own order: item_order_key, newest first."""
    states = [w for (sv, o, _), w in world.store.work.items() if w.placement == placement]
    names = {m.thread_id: n for n, m in world.sent.items() if m.thread_id == m.message_id}
    return [names[w.thread_id] for w in sorted(states, key=wire.item_order_key, reverse=True)]


def test_the_scenario_is_the_published_shapes(got):
    """Every fixture is a valid QueueSnapshot / HistoryPage (the page's
    generated types come from these same models)."""
    snapshot = got["_snapshot"]
    assert ports.QueueSnapshot.model_validate(snapshot.model_dump(mode="json")) == snapshot


def test_waiting_and_active_are_newest_arrival_first_by_the_contracts_key(got):
    world = got["_world"]
    assert got["waiting"] == _order(world, "queued")
    assert got["active"] == _order(world, "active")
    assert got["waiting"][:2] == ["newest", "creds"], got["waiting"]
    assert set(got["waiting"]) == {"newest", "creds", "long", "former", "self-file", "note-self"}
    assert set(got["active"]) == {"ana-numbers", "deploy", "budget", "release"}
    assert got["attention"] is True


def test_put_back_returns_an_item_to_its_original_place_and_a_refusal_undoes_only_it(got):
    # ana-numbers is the oldest arrival of all: put back, it goes to the
    # bottom, however recently it was worked on.
    assert got["putBackWaiting"] == got["waiting"] + ["ana-numbers"], got["putBackWaiting"]
    assert got["putBackUndone"] == got["active"]


def test_a_repeated_answer_changes_nothing(got):
    assert got["repeatChangesNothing"] is True


def test_a_late_older_snapshot_moves_no_fact_back(got):
    assert got["oldSnapshot"] == {"anaPlacement": "active", "releaseReplyDelivery": "received"}


def test_a_read_before_its_received_is_kept_and_changes_no_turn(got):
    assert got["readBeforeReceived"] == {"delivery": "read", "received": None, "turn": "theirs"}


def test_receipts_turn_and_reply_cues_are_independent(got):
    c = got["cues"]
    # Incoming and unread: the owner's turn.
    assert (c["newest"]["turn"], c["newest"]["unread"], c["newest"]["readByYou"]) == ("mine", True, False)
    # Opened and read, a reply drafted and not sent: still the owner's turn,
    # read by them, not replied.
    assert (c["ana-numbers"]["turn"], c["ana-numbers"]["readByYou"], c["ana-numbers"]["replied"]) == \
        ("mine", True, False)
    assert c["ana-numbers"]["files"] == 1
    # Outgoing: received but unread; read but unanswered.
    assert (c["deploy"]["turn"], c["deploy"]["delivery"]) == ("theirs", "received")
    assert (c["budget"]["turn"], c["budget"]["delivery"]) == ("theirs", "read")
    assert c["deploy"]["turnWords"] == "Matt’s turn"
    # An accepted direct reply is a reply.
    assert (c["release"]["replied"], c["release"]["turn"], c["release"]["delivery"]) == \
        (True, "theirs", "received")
    # A related link is not one, on either side.
    assert c["creds"]["related"] is True and c["creds"]["replied"] is False
    assert c["deploy"]["replied"] is False
    world = got["_world"]
    assert got["repliesToRelease"] == [str(world.sent["release-reply"].message_id)]
    assert got["repliesToDeploy"] == []


def test_a_message_to_oneself_is_one_item_and_always_the_owners_turn(got):
    c = got["cues"]
    for name in ("note-self", "self-file"):
        assert (c[name]["messages"], c[name]["turn"], c[name]["turnWords"]) == (1, "mine", "Your turn"), name
    assert c["self-file"]["files"] == 1
    # Acceptance is no read; both of Tim's JARVIS received it.
    assert c["note-self"]["delivery"] == "received" and c["note-self"]["unread"] is True
    assert got["selfTurnAfterCorrection"] == "mine"
    assert got["selfReadDelivery"] == "received"


def test_an_item_no_longer_listed_leaves_waiting_and_is_kept(got):
    assert got["droppedListed"] is False and got["droppedKept"] is True


def test_a_snapshot_for_another_pairing_replaces_everything(got):
    assert got["otherPairing"] == 0


def test_dismissed_items_come_from_history_and_restore_keeps_their_facts(got):
    assert got["dismissed"] == ["lunch"]
    assert got["restoringWaiting"] is True and got["restoringDismissed"] == []
    assert got["lunchCues"] == {"readByYou": True, "unread": False, "dismissed": True}


def test_opening_a_waiting_item_shows_it_in_active_at_once(got):
    assert got["openingActive"] is True


def test_people_and_jarvis_labels_with_safe_fallbacks(got):
    assert got["labels"] == {
        "me": "Me · Tim’s i9",
        "matt": "Matt · Matt’s MacBook",
        # Offline: still named, still a recipient.
        "ana": "Ana · Ana’s Studio",
        "former": "A member no longer listed",
        "gone": "a JARVIS no longer listed",
        "maxHandle": "@maxfw",
    }
    assert got["sizes"] == ["0 bytes", "1 byte", "812 bytes", "47.1 KB", "3.2 MB"]
