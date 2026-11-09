"""Provider-neutral evidence, not an inference from a worker's job status.

A stream delta proves recent generation, not that an HTTP request is still
spending tokens. Evidence expires after two seconds and tool/turn boundaries
clear it immediately. Transcript-only providers expose their limitation.
"""
from __future__ import annotations

from datetime import datetime

EVIDENCE_SECONDS = 2.0
MODEL_REQUEST_LEASE_SECONDS = 120.0


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (AttributeError, ValueError, TypeError):
        return None


def observation(objects, provider, previous=None):
    state, at, source = 'unknown', None, 'no_activity_evidence'
    request_id, request_at, last_output = None, None, None
    tools = set()
    if previous:
        state, at, source = previous.get('state', state), previous.get('observed_at'), previous.get('source', source)
        request_id, request_at = previous.get('request_id'), previous.get('request_started_at')
        last_output = previous.get('last_output_at')
        tools = set(previous.get('outstanding_tools') or [])
    for obj in objects:
        when = timestamp(obj.get('timestamp'))
        # Native transcripts may append the prompt/tool result after the
        # api-request it caused. File order must not undo a newer boundary.
        if when is not None and at is not None and when < at:
            continue
        p = obj.get('payload') or {}
        kind = obj.get('type')
        next_state = None
        if provider == 'openai':
            event = p.get('type')
            if kind == 'event_msg':
                if event == 'task_started': next_state = 'awaiting_model'
                elif event in ('task_complete', 'turn_aborted'): next_state = 'idle'
                elif event in ('agent_message', 'agent_reasoning', 'token_count'): next_state = 'recent_output'
            elif kind == 'response_item':
                if event in ('function_call', 'custom_tool_call'): next_state = 'tool_wait'
                elif event in ('function_call_output', 'custom_tool_call_output'): next_state = 'awaiting_model'
                elif event in ('message', 'reasoning') and p.get('role') != 'user': next_state = 'recent_output'
        else:
            if kind == 'api-request':
                request_id, request_at = obj.get('id'), when
                tools.clear()  # The provider request has consumed the prior tool batch.
                next_state = 'model_work'
            elif kind == 'system' and obj.get('subtype') == 'turn_duration':
                next_state = 'idle'
            elif kind == 'system' and obj.get('subtype') == 'thinking_tokens' and obj.get('estimated_tokens_delta', 0) > 0:
                next_state = 'generating'
            elif kind == 'tool_progress':
                next_state = 'tool_wait'
            elif kind == 'stream_event':
                event = obj.get('event') or {}
                if event.get('type') == 'message_start': next_state = 'model_work'
                elif event.get('type') == 'content_block_delta': next_state = 'generating'
                elif event.get('type') == 'message_stop': next_state = 'awaiting_model'
            elif kind == 'assistant':
                message = obj.get('message') or {}
                blocks = message.get('content') or []
                calls = [b for b in blocks if isinstance(b, dict) and b.get('type') == 'tool_use']
                if calls:
                    tools.update(b.get('id') for b in calls if b.get('id'))
                    next_state = 'tool_wait'
                elif request_id and obj.get('requestRef') == request_id:
                    # A thinking block may carry end_turn while text is still
                    # being generated. Only whole-worker evidence ends it.
                    next_state = 'model_work'
                    last_output = when
                else: next_state = 'recent_output'
            elif kind == 'user':
                content = (obj.get('message') or {}).get('content') or []
                if isinstance(content, list):
                    tools.difference_update(b.get('tool_use_id') for b in content if isinstance(b, dict) and b.get('type') == 'tool_result')
                next_state = 'tool_wait' if tools else 'awaiting_model'
            elif kind == 'result': next_state = 'idle'
        if next_state:
            state, at, source = next_state, when, f'{provider}:{kind}'
    return {'state': state, 'observed_at': at, 'source': source,
            'request_id': request_id, 'request_started_at': request_at, 'last_output_at': last_output,
            'outstanding_tools': sorted(tools),
            'coverage': 'stream' if provider == 'anthropic' and source.endswith('stream_event') else 'turn_and_output'}


def current(evidence, now):
    evidence = dict(evidence or {})
    state = evidence.get('state', 'unknown')
    at = evidence.get('observed_at')
    if state in ('generating', 'recent_output') and (at is None or not 0 <= now - at <= EVIDENCE_SECONDS):
        state = 'unknown'
    if state in ('model_work', 'model_active') and (at is None or not 0 <= now - at <= MODEL_REQUEST_LEASE_SECONDS):
        state = 'unknown'
    return {**evidence, 'last_phase': evidence.get('last_phase', evidence.get('state', 'unknown')), 'state': state,
            'evidence_age_seconds': max(0, now - at) if at is not None else None,
            'model_work': state in ('model_work', 'model_active', 'generating'),
            'generating': state == 'generating'}

# Managed runs expose partial messages directly. Keep only their latest
# observation, in memory, so a restart cannot resurrect a running claim.
import threading
from datetime import timezone

_lock = threading.Lock()
_managed = {}
_children = {}

def record_runtime(provider, session_id, evidence):
    with _lock:
        _managed[(provider, session_id)] = dict(evidence)



def record_managed(provider, session_id, event):
    event = {**event, 'timestamp': datetime.now(timezone.utc).isoformat()}
    evidence = observation([event], provider, managed(provider, session_id))
    if evidence['state'] == 'unknown':
        return
    with _lock:
        _managed[(provider, session_id)] = evidence


def managed(provider, session_id):
    with _lock:
        return dict(_managed.get((provider, session_id), {}))


def forget_managed(provider, session_id):
    with _lock:
        _managed.pop((provider, session_id), None)


def managed_children():
    with _lock:
        return [dict(row) for row in _children.values()]


class ManagedStream:
    """Attribute Claude's complete child messages without charging the parent.

    Native subagent token deltas are not forwarded by Claude Code. Their
    lifecycle and tool progress remain useful, but never prove generation.
    Only IDs, descriptions and phases are retained, never message content.
    """
    def __init__(self, root):
        self.root = root
        self.tools = {}
        self.children = set()
        self.background = set()

    def child(self, sid, event):
        self.children.add(sid)
        with _lock:
            old = _children.get(sid, {})
            _children[sid] = {
                'session_id': sid, 'owner': self.root,
                'parent_id': event.get('parent_task_id') or old.get('parent_id') or self.root,
                'name': event.get('description') or old.get('name') or 'Claude subagent',
                'state': old.get('state', 'active'),
            }
        if event.get('tool_use_id'):
            self.tools[event['tool_use_id']] = sid

    def phase(self, sid, state):
        record_runtime('anthropic', sid, {
            'state': state, 'observed_at': datetime.now(timezone.utc).timestamp(),
            'source': 'claude_task_lifecycle', 'coverage': 'lifecycle_only',
        })
        with _lock:
            if sid in _children:
                _children[sid]['state'] = 'idle' if state == 'idle' else 'active'

    def record(self, event):
        subtype = event.get('subtype')
        sid = event.get('task_id')
        if event.get('type') == 'system':
            if subtype == 'task_started' and sid and event.get('task_type') in ('local_agent', 'remote_agent'):
                self.child(sid, event)
                self.phase(sid, 'awaiting_model')
                return
            if subtype in ('task_notification', 'task_progress') and sid in self.children:
                self.phase(sid, 'idle' if subtype == 'task_notification' else 'awaiting_model')
                return
            if subtype == 'background_tasks_changed':
                live = set()
                for task in event.get('tasks', []):
                    if task.get('task_type') in ('local_agent', 'remote_agent') and task.get('task_id'):
                        sid = task['task_id']
                        live.add(sid)
                        self.child(sid, task)
                for sid in self.background - live:
                    self.phase(sid, 'idle')
                self.background = live
                return
        sid = event.get('agent_id') or self.tools.get(event.get('parent_tool_use_id'))
        if sid:
            self.child(sid, event)
            record_managed('anthropic', sid, event)
        elif not event.get('parent_tool_use_id'):
            record_managed('anthropic', self.root, event)
        # An older CLI can omit agent_id and task_started. In that case the
        # transcript adapter supplies the child; never credit its parent.

    def close(self):
        forget_managed('anthropic', self.root)
        with _lock:
            for sid in self.children:
                _children.pop(sid, None)
                _managed.pop(('anthropic', sid), None)


# Incremental native-transcript evidence. Completed message content is discarded
# immediately after its lifecycle fields have been reduced. First observation is
# bounded; an incomplete/oversized record produces a coverage gap, not activity.
_file_cache = {}
FILE_READ_BYTES = 4 * 1024 * 1024


def file_observation(path, provider):
    import json
    from pathlib import Path
    path = Path(path)
    key = (provider, str(path))
    try:
        stat = path.stat()
        with _lock:
            saved = _file_cache.get(key)
            if saved and saved['inode'] == stat.st_ino and saved['offset'] <= stat.st_size:
                offset, previous = saved['offset'], saved['evidence']
            else:
                offset, previous = max(0, stat.st_size - FILE_READ_BYTES), None
            if saved and offset == stat.st_size:
                return dict(previous)
            skipped = stat.st_size - offset > FILE_READ_BYTES
            if skipped:
                offset, previous = stat.st_size - FILE_READ_BYTES, None
            with path.open('rb') as source:
                source.seek(offset)
                if previous is None and offset:
                    source.readline(FILE_READ_BYTES)  # Initial seek may begin inside a record.
                    offset = source.tell()
                raw = source.read(FILE_READ_BYTES)
            end = raw.rfind(b'\n')
            if end < 0:
                evidence = dict(previous or {'state': 'unknown', 'source': 'incomplete_transcript'})
                if len(raw) == FILE_READ_BYTES:
                    evidence = {'state': 'unknown', 'source': 'oversized_transcript_record',
                                'coverage_gap': 'A transcript record exceeded the observation limit.'}
                    offset = stat.st_size
            else:
                objects = []
                for line in raw[:end].splitlines():
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict): objects.append(obj)
                    except (ValueError, UnicodeDecodeError):
                        continue
                evidence = observation(objects, provider, previous)
                offset += end + 1
            if skipped:
                evidence['coverage_gap'] = 'Transcript ingestion skipped a burst larger than its observation limit.'
            _file_cache[key] = {'inode': stat.st_ino, 'offset': offset, 'evidence': evidence}
            while len(_file_cache) > 4096:
                _file_cache.pop(next(iter(_file_cache)))
            return dict(evidence)
    except OSError:
        return {'state': 'unknown', 'source': 'transcript_unavailable'}
