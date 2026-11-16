"""A status briefing built from the Work items' own records
(status_briefing.py): what needs him first, then what is moving, then what
is done since a time; every fact with the evidence its record holds; an
item that needs nothing of him says so in one line; the handoff is one
make_package takes; --check names pieces with no note or no evidence."""

import importlib
import json
import time

import pytest

PR = "https://github.com/example/jarvis/pull/7"


@pytest.fixture
def sb(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import work_store
    importlib.reload(work_store)
    from jarvis import status_briefing
    importlib.reload(status_briefing)
    return status_briefing


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "speech"
    path.mkdir()
    return str(path)


def _ev(item_id, kind, summary, **detail):
    from jarvis import work_store
    return work_store.record(work_store.WorkEvent(item_id, kind, summary, detail))


TIM = {"kind": "tim"}
AGENT = {"kind": "agent", "ref": "s-1"}


def _busy_item(folder, evidence_path):
    """An item with one of everything: a decision, a held piece, a piece
    reported working, and one reported ready for review."""
    from jarvis import work_store
    item = work_store.create("Speech migration", "folder", folder)
    _ev(item.id, "decision_needed", "Which engine is the default?", key="engine",
        options=["Kroko", "Whisper"], recommendation="Kroko")
    _ev(item.id, "piece", "Engine wiring under way.", key="wire", title="Wire the engine",
        who=AGENT, state="working", evidence=["a1b2c3d"])
    _ev(item.id, "piece", "Waits for the engine choice.", key="default",
        title="Set the default", who=AGENT, state="waiting_on_tim", until="decision:engine")
    _ev(item.id, "piece", "Tests written and passing.", key="tests", title="Write the tests",
        who=AGENT, state="verifying", evidence=[PR, evidence_path])
    return work_store.get(item.id)


def test_needs_him_comes_first_then_moving_then_done(sb, folder, tmp_path):
    item = _busy_item(folder, str(tmp_path / "speech" / "report.md"))
    got = sb.compose_item(item, since=time.time() - 3600)
    assert list(got)[:7] == ["id", "title", "state", "closed", "needs_tim", "moving",
                             "done_since"]
    kinds = [f["kind"] for f in got["needs_tim"]]
    assert kinds[0] == "decision" and "held_piece" in kinds
    decision = got["needs_tim"][0]
    assert "Which engine is the default?" in decision["text"]
    assert decision["options"] == ["Kroko", "Whisper"] and decision["recommendation"] == "Kroko"
    held = next(f for f in got["needs_tim"] if f["kind"] == "held_piece")
    assert held["piece"] == "default" and held["holds"] == ["decision:engine"]
    assert held["who"] == "agent:s-1" and held["since"]
    assert [f["piece"] for f in got["moving"]] == ["wire"]
    assert [f["piece"] for f in got["done_since"]] == ["tests"]
    assert "needs_tim_line" not in got


def test_every_fact_carries_its_records_evidence(sb, folder, tmp_path):
    path = str(tmp_path / "speech" / "report.md")
    item = _busy_item(folder, path)
    got = sb.compose_item(item, since=time.time() - 3600)
    [wire] = got["moving"]
    assert wire["evidence"] == [{"kind": "commit", "value": "a1b2c3d"}]
    assert wire["note"] == "Engine wiring under way."
    [tests] = got["done_since"]
    assert {"kind": "url", "value": PR} in tests["evidence"]
    assert {"kind": "path", "value": path} in tests["evidence"]
    assert "Tests written and passing." in tests["text"]


def test_done_since_keeps_only_what_finished_after_the_time(sb, folder, tmp_path):
    item = _busy_item(folder, str(tmp_path / "speech" / "report.md"))
    assert sb.compose_item(item, since=time.time() + 60)["done_since"] == []
    from jarvis import work_store
    log = work_store.events(item.id)
    old = [work_store.WorkEvent(e.item_id, e.kind, e.summary, e.detail,
                                e.at - 7200 if e.detail.get("key") == "tests" else e.at,
                                e.seq) for e in log]
    since = time.time() - 3600
    assert sb.compose_item(item, since=since, events=old)["done_since"] == []
    assert [f["piece"] for f in sb.compose_item(item, since=since)["done_since"]] == ["tests"]


def test_an_item_with_nothing_needing_him_says_so_in_one_line(sb, folder):
    from jarvis import work_store
    item = work_store.create("Quiet work", "folder", folder)
    _ev(item.id, "piece", "Reading the code.", key="read", title="Read it", who=AGENT,
        state="working", evidence=["deadbee"])
    got = sb.compose_item(work_store.get(item.id), since=time.time() - 3600)
    assert got["needs_tim"] == []
    assert got["needs_tim_line"] == f"Nothing on Quiet work ({item.id}) needs you."
    assert "\n" not in got["needs_tim_line"]


def test_items_needing_him_come_before_those_that_do_not(sb, folder, tmp_path):
    from jarvis import work_store
    quiet = work_store.create("Quiet work", "folder", folder)
    _ev(quiet.id, "piece", "Reading.", key="read", title="Read it", who=AGENT, state="working")
    busy = _busy_item(folder, str(tmp_path / "speech" / "r.md"))
    got = sb.compose([quiet.id, busy.id], since=time.time() - 3600)
    assert [i["id"] for i in got["items"]] == [busy.id, quiet.id]


def test_a_blocked_piece_says_what_holds_it(sb, folder):
    from jarvis import work_store
    item = work_store.create("Build", "folder", folder)
    _ev(item.id, "piece", "CI is down.", key="ci", title="Run CI", who=AGENT, state="blocked",
        until="CI runner offline", evidence=[PR])
    [held] = sb.compose_item(work_store.get(item.id), since=0)["needs_tim"]
    assert held["kind"] == "held_piece" and "is blocked" in held["text"]
    assert "CI runner offline" in held["text"] and held["evidence"] == [{"kind": "url",
                                                                         "value": PR}]


def test_the_handoff_is_one_make_package_takes(sb, folder, tmp_path):
    from jarvis import package_author
    item = _busy_item(folder, str(tmp_path / "speech" / "report.md"))
    out = tmp_path / "briefing"
    path = sb.write([item.id], out, since=time.time() - 3600)
    handed = package_author.read_handoff(path)
    assert handed["evidence"][0] == "./status-evidence.json"
    assert "commit a1b2c3d" in handed["evidence"] and PR in handed["evidence"]
    assert handed["work"] == [item.id]
    asked = handed["decision"]
    assert asked != "information only" and asked["id"] == "approve-status"
    assert [(o["id"], o["says"], o.get("needs_words", False)) for o in asked["options"]] == [
        ("approve", "Approve: this status is complete and accurate", False),
        ("reject", "Reject: say what is missing or wrong", True)]
    assert asked["question"] and "steer" in asked["why_you"]
    assert asked["evidence"][0].startswith("./status-evidence.json")
    assert all(len(line) <= 160 for line in [asked["why_you"], *asked["evidence"]])
    assert (out / "status-evidence.json").exists()
    assert json.loads((out / "status-evidence.json").read_text())["items"][0]["id"] == item.id
    assert sorted(p.name for p in out.iterdir()) == ["handoff.json", "status-evidence.json"]


def test_an_unknown_item_is_refused(sb):
    with pytest.raises(sb.BriefingRefused, match="no-such"):
        sb.compose(["no-such"], since=0)


def test_check_names_pieces_with_no_note_or_no_evidence(sb, folder):
    from jarvis import work_store
    item = work_store.create("Build", "folder", folder)
    _ev(item.id, "piece", "Started.", key="a", title="Piece A", who=AGENT, state="working")
    _ev(item.id, "piece", "Planned.", key="b", title="Piece B", who=AGENT)
    from tests import work_records
    work_records.piece(item.id, "b", note="")
    [line] = sb.check([item.id])
    assert "piece a (Piece A) is activity_unknown with no evidence" in line
    assert "piece b (Piece B) has no note" in line
    assert "piece b (Piece B) is" not in line          # planned: no evidence expected yet


def test_check_writes_nothing_and_exits_one_on_gaps(sb, folder, tmp_path, capsys):
    from jarvis import work_store
    item = work_store.create("Build", "folder", folder)
    _ev(item.id, "piece", "Started.", key="a", title="Piece A", who=AGENT, state="working")
    assert sb.main(["--work", item.id, "--check"]) == 1
    assert "no evidence" in capsys.readouterr().out
    _ev(item.id, "piece", "Commit in.", key="a", evidence=["abc1234"])
    assert sb.main(["--work", item.id, "--check"]) == 0


@pytest.mark.parametrize("said,back", [("24h", 86400), ("7d", 7 * 86400), ("90m", 5400)])
def test_since_reads_a_span_back(sb, said, back):
    assert sb.parse_since(said, 1_000_000.0) == 1_000_000.0 - back


def test_since_reads_epoch_and_iso_and_refuses_words(sb):
    assert sb.parse_since("1700000000", 0) == 1700000000.0
    assert sb.parse_since("2026-11-12T15:42:00+00:00", 0) == 1794498120.0
    assert sb.parse_since(None, 100_000.0) == 100_000.0 - sb.DEFAULT_SINCE_SEC
    with pytest.raises(sb.BriefingRefused):
        sb.parse_since("last tuesday", 0)
