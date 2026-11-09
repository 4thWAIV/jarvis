from jarvis.codex_runtime import event_evidence, RuntimeObserver
from jarvis import worker_activity
import json


def test_server_request_with_matching_id_is_not_a_response(tmp_path):
    observer = RuntimeObserver(tmp_path)
    class Socket:
        def __init__(self):
            self.sent = []
            self.messages = iter([
                {'id': 1, 'method': 'item/commandExecution/requestApproval',
                 'params': {'threadId': 'one'}},
                {'method': 'item/reasoning/textDelta', 'params': {'threadId': 'one'}},
                {'id': 1, 'result': {'data': ['one']}},
            ])
        def send(self, message): self.sent.append(json.loads(message))
        def recv(self, **kwargs): return json.dumps(next(self.messages))
    socket = Socket()
    assert observer._request(socket, 'thread/loaded/list', {}) == {'data': ['one']}
    assert len(socket.sent) == 1  # Observation never answers the approval request.
    assert worker_activity.managed('openai', 'one')['state'] == 'generating'
    observer.close()


def test_runtime_generation_only_from_model_deltas():
    for method in ('item/agentMessage/delta', 'item/reasoning/textDelta', 'item/reasoning/summaryTextDelta'):
        sid, evidence = event_evidence({'method': method, 'params': {'threadId': 'child', 'delta': 'private'}}, 100)
        assert sid == 'child'
        assert worker_activity.current(evidence, 100)['generating']
        assert 'private' not in str(evidence)
        assert not worker_activity.current(evidence, 103)['generating']
    assert event_evidence({'method': 'item/commandExecution/outputDelta', 'params': {'threadId': 'child'}}, 100) is None
    assert event_evidence({'method': 'thread/tokenUsage/updated', 'params': {'threadId': 'child'}}, 100) is None


def test_runtime_active_and_reasoning_lifecycle_do_not_claim_tokens():
    assert event_evidence({'method': 'thread/status/changed', 'params': {'threadId': 'one', 'status': {'type': 'active', 'activeFlags': []}}}, 100) is None
    _, evidence = event_evidence({'method': 'item/started', 'params': {'threadId': 'one', 'item': {'type': 'reasoning'}}}, 100)
    assert evidence['state'] == 'model_active'
    assert not worker_activity.current(evidence, 100)['generating']


def test_tool_wait_and_completion_clear_generation(tmp_path):
    observer = RuntimeObserver(tmp_path)
    observer._event({'method': 'item/agentMessage/delta', 'params': {'threadId': 'one'}})
    observer._event({'method': 'item/started', 'params': {'threadId': 'one', 'item': {'type': 'mcpToolCall'}}})
    assert worker_activity.managed('openai', 'one')['state'] == 'tool_wait'
    observer._event({'method': 'turn/completed', 'params': {'threadId': 'one'}})
    assert worker_activity.managed('openai', 'one')['state'] == 'idle'
    observer.close()
    assert not worker_activity.managed('openai', 'one')


def test_refresh_only_rejoins_already_active_threads_without_overrides(tmp_path):
    observer = RuntimeObserver(tmp_path)
    calls = []
    def request(ws, method, params):
        calls.append((method, params))
        if method == 'thread/loaded/list': return {'data': ['active', 'idle']}
        if method == 'thread/read': return {'thread': {'status': {'type': params['threadId']}}}
        return {}
    observer._request = request
    observer._refresh(None, set())
    assert ('thread/resume', {'threadId': 'active', 'excludeTurns': True}) in calls
    assert not any(m == 'thread/resume' and p['threadId'] == 'idle' for m, p in calls)
    assert not any(m in ('thread/start', 'turn/start', 'thread/settings/update') for m, p in calls)
    observer.close()


def test_idle_refresh_unsubscribes_and_clears_prior_delta(tmp_path):
    observer = RuntimeObserver(tmp_path)
    attached = {'one'}
    calls = []
    def request(ws, method, params):
        calls.append((method, params))
        if method == 'thread/loaded/list': return {'data': ['one']}
        if method == 'thread/read': return {'thread': {'status': {'type': 'idle'}}}
        return {}
    observer._request = request
    observer._event({'method': 'item/agentMessage/delta', 'params': {'threadId': 'one'}})
    observer._refresh(None, attached)
    assert ('thread/unsubscribe', {'threadId': 'one'}) in calls
    assert not attached
    assert worker_activity.managed('openai', 'one')['state'] == 'idle'
    observer.close()


def test_unloaded_workers_drop_runtime_evidence(tmp_path):
    observer = RuntimeObserver(tmp_path)
    observer._event({'method': 'item/agentMessage/delta', 'params': {'threadId': 'one'}})
    observer._request = lambda *args: {'data': []}
    observer._refresh(None, {'one'})
    assert not worker_activity.managed('openai', 'one')
    assert observer.snapshot()[0] == {}


def test_reasoning_item_completion_is_not_model_request_completion(tmp_path):
    observer = RuntimeObserver(tmp_path)
    for method in ('item/started', 'item/completed'):
        observer._event({'method': method, 'params': {'threadId': 'one', 'turnId': 'turn',
                         'item': {'id': 'reason', 'type': 'reasoning'}}})
        evidence = worker_activity.managed('openai', 'one')
        result = worker_activity.current(evidence, evidence['observed_at'])
        assert result['model_work'] and not result['generating']
        assert result['item_type'] == 'reasoning'
    assert not worker_activity.current(evidence, evidence['observed_at'] + 121)['model_work']
    observer._event({'method': 'turn/completed', 'params': {'threadId': 'one'}})
    assert worker_activity.managed('openai', 'one')['state'] == 'idle'
    observer.close()


def test_native_waits_and_parallel_tools_do_not_claim_model_work(tmp_path):
    observer = RuntimeObserver(tmp_path)
    for kind in ('sleep', 'subAgentActivity'):
        observer._event({'method': 'item/started', 'params': {'threadId': 'one', 'item': {'id': kind, 'type': kind}}})
    observer._event({'method': 'item/completed', 'params': {'threadId': 'one', 'item': {'id': 'sleep', 'type': 'sleep'}}})
    assert worker_activity.managed('openai', 'one')['state'] == 'tool_wait'
    observer._event({'method': 'item/completed', 'params': {'threadId': 'one', 'item': {'id': 'reason', 'type': 'reasoning'}}})
    assert worker_activity.managed('openai', 'one')['state'] == 'tool_wait'
    observer._event({'method': 'item/completed', 'params': {'threadId': 'one', 'item': {'id': 'subAgentActivity', 'type': 'subAgentActivity'}}})
    assert worker_activity.managed('openai', 'one')['state'] == 'awaiting_model'
    observer.close()


def test_final_answer_item_stops_model_phase_before_turn_terminal():
    _, evidence = event_evidence({'method': 'item/completed', 'params': {'threadId': 'one',
                          'item': {'type': 'agentMessage', 'phase': 'final_answer'}}}, 100)
    assert evidence['state'] == 'finishing'
    assert not worker_activity.current(evidence, 100)['model_work']


def test_first_status_read_does_not_erase_a_live_model_boundary(tmp_path):
    observer = RuntimeObserver(tmp_path)
    observer._event({'method': 'item/started', 'params': {'threadId': 'one', 'item': {'type': 'reasoning'}}})
    def request(ws, method, params):
        if method == 'thread/loaded/list': return {'data': ['one']}
        if method == 'thread/read': return {'thread': {'status': {'type': 'active'}}}
        return {}
    observer._request = request
    observer._refresh(None, set())
    assert worker_activity.managed('openai', 'one')['state'] == 'model_active'
    observer.close()
