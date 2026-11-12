"""The one definition of working (worker_inventory.working): a turn is open,
waiting on the model and running a tool included, and JARVIS's brain while a
turn of it is open. Every surface reads its answer: the Agents list (each
agent's orb and "N working"), the brain count (the provider's working count),
the main orb (the brain row), Work rows and pieces, workflows."""
from __future__ import annotations

import json
import time

from jarvis import session_watch, worker_activity
from jarvis.session_watch import SessionState, Snapshot, Subagent
from jarvis.worker_inventory import build

NOW = 10_000.0


def session(sid, state, **kwargs):
    return SessionState(session_id=sid, provider='anthropic', cwd='/code', project='p', state=state, **kwargs)


def sub(aid, state, phase='model_work'):
    return Subagent(aid, aid, 'general-purpose', True, 1, state, NOW - 60, NOW - 5,
                    worker_activity={'state': phase, 'observed_at': NOW - 5})


def row(result, sid):
    return next(r for r in result['workers'] if r['session_id'] == sid)


def test_a_session_in_its_turn_is_working_whether_awaiting_the_model_or_in_a_tool():
    model = session('model', session_watch.WORKING, worker_activity={'state': 'awaiting_model', 'observed_at': NOW - 400})
    tool = session('tool', session_watch.WORKING, worker_activity={'state': 'tool_wait', 'observed_at': NOW - 400,
                                                                  'outstanding_tools': ['t1']})
    result = build(Snapshot([model, tool], NOW), now=NOW)
    assert row(result, 'model')['activity']['working'] and row(result, 'model')['activity']['phase'] == 'model'
    assert row(result, 'tool')['activity']['working'] and row(result, 'tool')['activity']['phase'] == 'tool'
    assert result['total']['working'] == 2 and result['total']['unknown'] == 0


def test_a_session_between_turns_or_ended_is_not_working():
    idle = session('idle', session_watch.IDLE, worker_activity={'state': 'model_work', 'observed_at': NOW})
    ended = session('ended', session_watch.GONE)
    result = build(Snapshot([idle, ended], NOW), now=NOW)
    assert not row(result, 'idle')['activity']['working']
    assert row(result, 'idle')['activity']['orb_state'] == 'idle'
    assert all(r['session_id'] != 'ended' for r in result['workers'])
    assert result['total']['working'] == 0


def test_subagents_count_under_their_agent_and_in_the_brain_count():
    parent = session('parent', session_watch.IDLE, subagents=[
        sub('a1', session_watch.AGENT_WORKING), sub('a2', session_watch.AGENT_WORKING, 'tool_wait'),
        sub('a3', session_watch.AGENT_FINISHED, 'idle'), sub('a4', session_watch.AGENT_STALE, 'tool_wait')])
    result = build(Snapshot([parent], NOW), now=NOW)
    agent = row(result, 'parent')
    # The Agents list's line under the name: "2 working".
    assert agent['descendant_counts']['active'] == 2
    assert not agent['activity']['working']
    # The brain count: every worker on that account working now.
    assert result['providers']['anthropic']['active'] == 2
    assert row(result, 'a3')['activity']['work_state'] == 'finished'


def test_the_brain_thinking_is_working_and_the_main_orb_reads_it():
    events = {'open_turns': [{'id': 't1', 'provider': 'anthropic', 'started_at': NOW - 1}], 'events': []}
    result = build(Snapshot([], NOW), brain_events=events, now=NOW)
    brain = row(result, 'brain')
    assert brain['activity']['working'] and brain['activity']['orb_state'] == 'thinking'
    assert result['providers']['anthropic']['active'] == 1
    done = build(Snapshot([], NOW), brain_events={'open_turns': [], 'events': [
        {'kind': 'turn', 'stage': 'completed', 'provider': 'anthropic', 'at': NOW - 1, 'id': 't1'}]}, now=NOW)
    assert not row(done, 'brain')['activity']['working']


def test_a_running_run_is_working_and_a_queued_one_is_not():
    result = build(Snapshot([], NOW), runs=[{'id': 'r1', 'status': 'running'}, {'id': 'r2', 'status': 'queued'}], now=NOW)
    assert row(result, 'r1')['activity']['working']
    assert not row(result, 'r2')['activity']['working']


def _subagent_file(root, cwd, sid, aid, lines, age):
    folder = root / 'projects' / session_watch.encode_cwd(cwd) / sid / 'subagents'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'agent-{aid}.jsonl'
    path.write_text(''.join(json.dumps(x) + '\n' for x in lines))
    import os
    os.utime(path, (time.time() - age, time.time() - age))


TOOL_CALL = {'type': 'assistant', 'message': {'role': 'assistant', 'stop_reason': 'tool_use',
                                              'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash'}]}}
ENDED = {'type': 'assistant', 'message': {'role': 'assistant', 'stop_reason': 'end_turn',
                                          'content': [{'type': 'text', 'text': 'Done.'}]}}


def test_a_subagent_in_a_long_tool_call_stays_working_until_its_window_ends(tmp_path):
    cwd, sid = '/code/x', 'sess'
    _subagent_file(tmp_path, cwd, sid, 'atool', [TOOL_CALL], age=300)          # 5 minutes into a tool
    _subagent_file(tmp_path, cwd, sid, 'agone', [TOOL_CALL], age=session_watch.SUBAGENT_TURN_SILENT_SEC + 60)
    _subagent_file(tmp_path, cwd, sid, 'adone', [ENDED], age=1)
    agents, _ = session_watch.read_subagents(tmp_path, cwd, sid, time.time(), limit=None)
    states = {a.agent_id: a.state for a in agents}
    assert states == {'atool': session_watch.AGENT_WORKING, 'agone': session_watch.AGENT_STALE,
                      'adone': session_watch.AGENT_FINISHED}


def test_a_poll_reads_only_what_a_growing_transcript_appended(tmp_path):
    """The background-work reader keeps where it stopped: a working
    subagent's transcript is never read whole again while it grows."""
    path = tmp_path / 'agent-a.jsonl'
    started = {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't',
                                                        'content': 'Command running in background with ID: b1. Output'}]}}
    path.write_text(json.dumps(started) + '\n')
    first = session_watch._background_tasks(path, path.stat())
    assert list(first) == ['b1']
    with path.open('a') as out:
        out.write(json.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't',
                                                                      'content': 'Successfully stopped task: b1'}]}}) + '\n')
    second = session_watch._background_tasks(path, path.stat())
    assert second == {'b1': None}
    assert session_watch._BG_CACHE[str(path)][2] == path.stat().st_size
    assert session_watch._BG_CACHE_MAX >= 4096


def test_nothing_reads_unknown_or_unconfirmed():
    s = session('s', session_watch.WORKING)  # no evidence at all
    a = build(Snapshot([s], NOW - 120), now=NOW)  # and a late snapshot
    assert a['workers'][0]['activity']['orb_state'] == 'thinking'
    assert a['total']['unknown'] == 0
    assert worker_activity.settle({}, False, NOW)['orb_state'] == 'idle'
