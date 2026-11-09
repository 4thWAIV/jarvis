from jarvis import worker_activity as activity
from jarvis.worker_inventory import build
from jarvis.session_watch import SessionState, Snapshot, Subagent


def session(sid='parent', provider='anthropic', **kwargs):
    return SessionState(session_id=sid, provider=provider, cwd='/code', project='project', state='working', **kwargs)


def test_stream_expiry_and_tool_wait():
    evidence = activity.observation([{'type': 'stream_event', 'timestamp': '2026-01-01T00:00:00Z',
                                      'event': {'type': 'content_block_delta'}}], 'anthropic')
    at = evidence['observed_at']
    assert activity.current(evidence, at + 1)['generating']
    assert not activity.current(evidence, at + 3)['generating']
    tool = activity.observation([{'type': 'assistant', 'message': {'content': [{'type': 'tool_use'}]}}], 'anthropic')
    assert activity.current(tool, at)['state'] == 'tool_wait'


def test_provider_output_is_not_current_generation():
    for provider, obj in [('anthropic', {'type': 'assistant', 'message': {'content': [{'type': 'text'}]}}),
                          ('openai', {'type': 'event_msg', 'payload': {'type': 'token_count'}})]:
        obj['timestamp'] = '2026-01-01T00:00:00Z'
        evidence = activity.observation([obj], provider)
        assert not activity.current(evidence, evidence['observed_at'])['generating']


def test_nested_workers_dedup_workflow_and_provider_identity():
    ev = {'state': 'generating', 'observed_at': 100}
    child = Subagent('child', 'Child', 'workflow-subagent', False, 1, 'working', 99, 100,
                     worker_activity=ev, workflow_run='workflow')
    parent = session(subagents=[child])
    nested = session('nested', parent_session_id='child', worker_activity=ev)
    duplicate = session('child', parent_session_id='parent', worker_activity=ev)
    other = session('parent', 'openai')
    result = build(Snapshot([parent, nested, duplicate, other], 100), runs=[{'id': 'child', 'status': 'running'}], now=100)
    assert result['total']['workers'] == 4
    assert result['total']['generating'] == 2
    assert next(w for w in result['workers'] if w['id'] == 'anthropic:parent')['counts']['workers'] == 3


def test_restart_and_stale_discovery_clear_current_generation():
    s = session(worker_activity={'state': 'generating', 'observed_at': 100})
    assert build(Snapshot([s], 80), now=100)['total']['generating'] == 0
    assert build(Snapshot(), now=100)['gaps']
    assert build(Snapshot(), brain_events={'generation': 'new', 'events': []}, now=100)['total']['generating'] == 0


def test_brain_tool_wait_and_terminal_event_clear_stream():
    events = [{'kind': 'turn', 'stage': 'streaming', 'provider': 'openai', 'at': 100},
              {'kind': 'tool', 'stage': 'started', 'provider': 'openai', 'at': 100}]
    result = build(Snapshot([],100), brain_events={'events': events}, now=100)
    assert result['total']['generating'] == 0
    assert result['total']['tool_wait'] == 1


def test_queued_and_unknown_are_not_idle_or_generating():
    result = build(Snapshot([session()], 100), runs=[{'id': 'queued', 'status': 'queued'}], now=100)
    assert result['total']['unknown'] == 1
    assert result['total']['queued'] == 1
    assert result['total']['generating'] == 0


def test_codex_descendants_use_parent_metadata_not_folder(tmp_path):
    import json
    from jarvis.codex_sessions import child_sessions
    threads = {}
    for sid, parent in [('child', 'root'), ('nested', 'child'), ('unrelated', 'other')]:
        path = tmp_path / f'{sid}.jsonl'
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {'parent_thread_id': parent, 'agent_path': sid}}) + '\n' +
                        json.dumps({'type': 'event_msg', 'timestamp': '2026-01-01T00:00:00Z', 'payload': {'type': 'task_started'}}))
        threads[sid] = {'thread_source': 'subagent', 'rollout_path': str(path), 'cwd': '/code'}
    children = child_sessions(threads, [session('root', 'openai')])
    assert {s.session_id: s.parent_session_id for s in children} == {'child': 'root', 'nested': 'child'}
    assert all(s.provider == 'openai' for s in children)


def test_managed_partial_observations_are_not_renewed_by_reads():
    activity.record_managed('anthropic', 'run', {'type': 'stream_event', 'event': {'type': 'content_block_delta'}})
    evidence = activity.managed('anthropic', 'run')
    at = evidence['observed_at']
    assert activity.current(evidence, at)['generating']
    assert not activity.current(activity.managed('anthropic', 'run'), at + 10)['generating']
    activity.forget_managed('anthropic', 'run')
    assert not activity.managed('anthropic', 'run')


def test_api_and_brain_tool_return_identical_inventory(monkeypatch):
    import asyncio, json
    from jarvis import server
    expected = {'workers': [], 'total': {'generating': 0, 'unknown': 1}}
    monkeypatch.setattr(server, '_worker_inventory', lambda: expected)
    assert asyncio.run(server.api_workers()) == expected
    assert json.loads(server.TOOL_HANDLERS['list_workers']({})) == expected


def test_provider_failure_does_not_look_like_empty_complete_inventory():
    from jarvis.session_watch import build_snapshot
    class Broken:
        name = 'openai'
        def discover(self, now): raise OSError('unavailable')
    snapshot = build_snapshot(providers=[Broken()], now=100)
    result = build(snapshot, now=100)
    assert result['gaps'] == ['openai discovery unavailable: OSError']


def test_parent_cycle_is_reported_and_remains_visible():
    result = build(Snapshot([session('one', parent_session_id='two'), session('two', parent_session_id='one')], 100), now=100)
    assert any('cycle' in gap for gap in result['gaps'])
    assert any(w['parent_id'] is None for w in result['workers'])


def test_claude_api_request_is_not_token_generation():
    ev = activity.observation([{'type': 'api-request', 'timestamp': '2026-01-01T00:00:00Z'}], 'anthropic')
    assert ev['state'] == 'model_work'
    assert not activity.current(ev, ev['observed_at'])['generating']


def test_managed_stream_attributes_children_and_excludes_shell_tasks():
    stream = activity.ManagedStream('managed-root')
    try:
        stream.record({'type': 'system', 'subtype': 'task_started', 'task_id': 'child',
                       'task_type': 'local_agent', 'tool_use_id': 'spawn'})
        stream.record({'type': 'system', 'subtype': 'task_started', 'task_id': 'shell',
                       'task_type': 'local_bash'})
        stream.record({'type': 'assistant', 'agent_id': 'child', 'parent_tool_use_id': 'spawn',
                       'message': {'content': [{'type': 'tool_use'}]}})
        assert activity.managed('anthropic', 'child')['state'] == 'tool_wait'
        assert not activity.managed('anthropic', 'managed-root')
        assert not activity.managed('anthropic', 'shell')
        result = build(Snapshot([], 100), runs=[{'id': 'managed-root', 'status': 'running'}], now=100)
        assert result['total']['workers'] == 2
        assert result['total']['generating'] == 0
        assert next(w for w in result['workers'] if w['session_id'] == 'child')['parent_id'] == 'anthropic:managed-root'
        stream.record({'type': 'system', 'subtype': 'task_notification', 'task_id': 'child'})
        assert activity.managed('anthropic', 'child')['state'] == 'idle'
    finally:
        stream.close()
    assert not activity.managed_children()
    assert not activity.managed('anthropic', 'child')


def test_claude_native_child_output_never_credits_parent_generation():
    stream = activity.ManagedStream('managed-root')
    try:
        stream.record({'type': 'assistant', 'parent_tool_use_id': 'unknown-spawn',
                       'message': {'content': [{'type': 'text'}]}})
        assert not activity.managed('anthropic', 'managed-root')
        stream.record({'type': 'system', 'subtype': 'thinking_tokens', 'estimated_tokens_delta': 5})
        assert activity.managed('anthropic', 'managed-root')['state'] == 'generating'
    finally:
        stream.close()


def test_managed_background_membership_does_not_finish_foreground_child():
    stream = activity.ManagedStream('managed-root')
    try:
        stream.record({'type': 'system', 'subtype': 'task_started', 'task_id': 'foreground', 'task_type': 'local_agent'})
        stream.record({'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': [
            {'task_id': 'background', 'task_type': 'local_agent', 'parent_task_id': 'foreground'}]})
        stream.record({'type': 'system', 'subtype': 'background_tasks_changed', 'tasks': []})
        assert activity.managed('anthropic', 'background')['state'] == 'idle'
        assert activity.managed('anthropic', 'foreground')['state'] == 'awaiting_model'
    finally:
        stream.close()


def test_loaded_codex_child_remains_visible_without_its_parent(tmp_path):
    import json
    from jarvis.codex_sessions import child_sessions
    path=tmp_path/'child.jsonl'
    path.write_text(json.dumps({'type':'session_meta','payload':{'parent_thread_id':'unloaded'}})+'\n')
    threads={'child':{'thread_source':'subagent','rollout_path':str(path),'cwd':'/project'}}
    children=child_sessions(threads,[],include={'child'})
    assert len(children)==1 and children[0].parent_session_id=='unloaded'
    result=build(Snapshot(children,100),now=100)
    assert result['total']['workers']==1
    assert any('Parent not discovered' in gap for gap in result['gaps'])
