import importlib
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    run_store.init_db()
    return run_store


def test_create_run_returns_uuid(store):
    run_id = store.create_run("build a thing", "proj", "/tmp/proj", "voice")
    assert len(run_id) == 36
    assert run_id.count("-") == 4


def test_created_run_starts_queued(store):
    run_id = store.create_run("build", "proj", "/tmp/proj", "voice")
    run = store.get_run(run_id)
    assert run["status"] == store.RunStatus.QUEUED
    assert run["prompt"] == "build"
    assert run["origin"] == "voice"
    assert run["cost_usd"] == 0
    assert run["started_at"] is None


def test_get_run_missing_returns_none(store):
    assert store.get_run("nope") is None


def test_update_run_sets_fields(store):
    run_id = store.create_run("build", "proj", "/tmp/proj", "api")
    store.update_run(run_id, status=store.RunStatus.RUNNING, pid=4242)
    run = store.get_run(run_id)
    assert run["status"] == store.RunStatus.RUNNING
    assert run["pid"] == 4242


def test_update_run_rejects_unknown_column(store):
    run_id = store.create_run("build", "proj", "/tmp/proj", "api")
    with pytest.raises(ValueError):
        store.update_run(run_id, bogus_column="x")


def test_list_runs_newest_first(store):
    first = store.create_run("a", "p", "/tmp/p", "voice")
    time.sleep(0.01)
    second = store.create_run("b", "p", "/tmp/p", "voice")
    runs = store.list_runs()
    assert [r["id"] for r in runs] == [second, first]


def test_list_runs_filters_by_status(store):
    a = store.create_run("a", "p", "/tmp/p", "voice")
    store.create_run("b", "p", "/tmp/p", "voice")
    store.update_run(a, status=store.RunStatus.SUCCEEDED)
    runs = store.list_runs(status=[store.RunStatus.SUCCEEDED])
    assert [r["id"] for r in runs] == [a]


def test_list_runs_filters_by_project(store):
    a = store.create_run("a", "alpha", "/tmp/a", "voice")
    store.create_run("b", "beta", "/tmp/b", "voice")
    runs = store.list_runs(project="alpha")
    assert [r["id"] for r in runs] == [a]


def test_events_round_trip_in_seq_order(store):
    run_id = store.create_run("a", "p", "/tmp/p", "voice")
    for i in (1, 2, 3):
        store.append_event(run_id, i, "assistant", f'{{"n":{i}}}')
    events = store.get_events(run_id)
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[0]["kind"] == "assistant"


def test_get_events_after_seq(store):
    run_id = store.create_run("a", "p", "/tmp/p", "voice")
    for i in (1, 2, 3):
        store.append_event(run_id, i, "assistant", "{}")
    assert [e["seq"] for e in store.get_events(run_id, after_seq=1)] == [2, 3]


def test_next_seq_increments(store):
    run_id = store.create_run("a", "p", "/tmp/p", "voice")
    assert store.next_seq(run_id) == 1
    store.append_event(run_id, 1, "system", "{}")
    assert store.next_seq(run_id) == 2


def test_count_events(store):
    run_id = store.create_run("a", "p", "/tmp/p", "voice")
    assert store.count_events(run_id) == 0
    for i in (1, 2, 3):
        store.append_event(run_id, i, "assistant", "{}")
    assert store.count_events(run_id) == 3


def test_sweep_marks_running_as_failed(store):
    run_id = store.create_run("a", "p", "/tmp/p", "voice")
    store.update_run(run_id, status=store.RunStatus.RUNNING)
    assert store.sweep_stale_runs() == 1
    run = store.get_run(run_id)
    assert run["status"] == store.RunStatus.FAILED
    assert "server restarted" in run["error"]
    assert run["ended_at"] is not None


def test_sweep_leaves_terminal_runs_alone(store):
    run_id = store.create_run("a", "p", "/tmp/p", "voice")
    store.update_run(run_id, status=store.RunStatus.SUCCEEDED)
    assert store.sweep_stale_runs() == 0
    assert store.get_run(run_id)["status"] == store.RunStatus.SUCCEEDED


def test_stats_aggregates_cost_and_counts(store):
    a = store.create_run("a", "p", "/tmp/p", "voice")
    b = store.create_run("b", "p", "/tmp/p", "voice")
    store.update_run(a, status=store.RunStatus.SUCCEEDED, cost_usd=0.25, input_tokens=100)
    store.update_run(b, status=store.RunStatus.FAILED, cost_usd=0.75, input_tokens=50)
    s = store.stats("day")
    assert s["total_cost_usd"] == 1.0
    assert s["by_status"][store.RunStatus.SUCCEEDED] == 1
    assert s["by_status"][store.RunStatus.FAILED] == 1
    assert s["total_input_tokens"] == 150


def test_requested_model_round_trips(store):
    run_id = store.create_run("build", "proj", "/tmp/proj", "api")
    store.update_run(run_id, requested_model="haiku")
    run = store.get_run(run_id)
    assert run["requested_model"] == "haiku"


# -- a run written before a field existed: the old column backfill -----------
# A run document written before `requested_model` (or `is_error`,
# `folder_id`...) existed has no such field. It must read with the field's
# default, as the ALTER TABLE backfill gave old rows, without disturbing what
# is there, and take the field on its next update.

# The fields of a run as the oldest `runs` table had them.
_OLD_RUN_FIELDS = ("id", "project_name", "project_path", "prompt", "origin", "status",
                   "resume_from", "result_text", "summary", "error", "exit_code", "pid",
                   "cost_usd", "input_tokens", "output_tokens", "cache_read_tokens",
                   "cache_creation_tokens", "num_turns", "model", "created_at",
                   "started_at", "ended_at")


def _old_run(store, run_id="old-run"):
    """A run document in the pre-`requested_model` shape, written directly,
    bypassing create_run: what a record looks like before this change."""
    from jarvis import records
    data = {"id": run_id, "project_name": "proj", "project_path": "/tmp/proj",
            "prompt": "legacy prompt", "origin": "voice", "status": "succeeded",
            "resume_from": None, "result_text": "", "summary": "", "error": "",
            "exit_code": None, "pid": None, "cost_usd": 0.0, "input_tokens": 0,
            "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "num_turns": 0, "model": "", "created_at": time.time(), "started_at": None,
            "ended_at": None}
    assert set(data) == set(_OLD_RUN_FIELDS)
    records.write(store._run_path(run_id), data)
    return run_id


def test_existing_run_without_requested_model_still_opens(store):
    _old_run(store)
    store.init_db()                 # must not raise, and changes nothing

    old = store.get_run("old-run")
    assert old is not None
    assert old["prompt"] == "legacy prompt"
    assert old["requested_model"] == ""

    new_id = store.create_run("new prompt", "proj", "/tmp/proj", "voice")
    store.update_run(new_id, requested_model="opus")
    assert store.get_run(new_id)["requested_model"] == "opus"
    store.update_run("old-run", requested_model="haiku")
    assert store.get_run("old-run")["requested_model"] == "haiku"
    assert store.get_run("old-run")["prompt"] == "legacy prompt"


# -- `is_error`: the CLI's own verdict on the turn, kept ---------------------
# The CLI reports an auth failure as `subtype: "success"` with
# `is_error: true`, AND exits 0. Deciding a run's fate on the exit code alone
# recorded that as `succeeded`. The flag is now a field so the reading is
# not lost the moment the stream is over.


def test_is_error_defaults_to_zero_and_round_trips(store):
    run_id = store.create_run("p", "proj", "/tmp/proj", "voice")
    assert store.get_run(run_id)["is_error"] == 0
    store.update_run(run_id, is_error=1)
    assert store.get_run(run_id)["is_error"] == 1


def test_existing_run_without_is_error_still_opens(store):
    _old_run(store)
    store.init_db()

    old = store.get_run("old-run")
    assert old is not None
    assert old["is_error"] == 0

    new_id = store.create_run("new prompt", "proj", "/tmp/proj", "voice")
    store.update_run(new_id, is_error=1)
    assert store.get_run(new_id)["is_error"] == 1


# -- `folder_id`: the registered folder a run worked in ----------------------
# A run the brain starts in a registered folder (spawn_run's `folder_id`)
# keeps that folder's ID, so the views can say where it ran. A run written
# before the field reads None.


def test_folder_id_is_recorded_and_absent_by_default(store):
    plain = store.create_run("p", "proj", "/tmp/proj", "voice")
    assert store.get_run(plain)["folder_id"] is None
    fid = "0b7c1d2e-3f40-4a5b-8c6d-7e8f90a1b2c3"
    in_folder = store.create_run("p", "denice", "/tmp/denice", "voice", folder_id=fid)
    assert store.get_run(in_folder)["folder_id"] == fid


def test_existing_run_without_folder_id_still_opens(store):
    _old_run(store)
    store.init_db()
    store.init_db()          # repeatable

    assert store.get_run("old-run")["folder_id"] is None
