"""Observe the already-running Codex daemon over its documented Unix WebSocket.

Only loaded threads are considered. Rejoin active threads without configuration
overrides to receive their events; never start a thread/turn or answer approval
requests. No transcript contents, tool arguments or output text are retained.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from websockets.sync.client import unix_connect
from jarvis import worker_activity

MODEL_DELTAS = frozenset({'item/agentMessage/delta', 'item/reasoning/textDelta',
                         'item/reasoning/summaryTextDelta', 'item/plan/delta'})
TOOLS = frozenset({'commandExecution', 'fileChange', 'mcpToolCall', 'dynamicToolCall',
                   'collabAgentToolCall', 'webSearch'})


def event_evidence(event: dict, now: float) -> tuple[str, dict] | None:
    method = event.get('method')
    p = event.get('params') or {}
    sid = p.get('threadId')
    if not sid:
        return None
    state = None
    if method in MODEL_DELTAS:
        state = 'generating'
    elif method in ('turn/completed', 'thread/closed'):
        state = 'idle'
    elif method == 'turn/started':
        state = 'awaiting_model'
    elif method in ('item/started', 'item/completed'):
        item = p.get('item') or {}
        if method == 'item/started':
            if item.get('type') in TOOLS: state = 'tool_wait'
            elif item.get('type') in ('reasoning', 'agentMessage', 'plan'): state = 'model_active'
        elif item.get('type') in TOOLS or item.get('type') in ('reasoning', 'agentMessage', 'plan'):
            state = 'awaiting_model'
    elif method == 'thread/status/changed':
        status = p.get('status') or {}
        if status.get('type') in ('idle', 'notLoaded'): state = 'idle'
        elif status.get('type') == 'active' and status.get('activeFlags'): state = 'human_wait'
    if state is None:
        return None
    return sid, {'state': state, 'observed_at': now, 'source': f'codex_runtime:{method}',
                 'coverage': 'runtime_stream'}


class RuntimeObserver:
    def __init__(self, home: Path):
        self.path = home / 'app-server-control' / 'app-server-control.sock'
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._rows = {}
        self._read_at = 0.0
        self._error = 'Runtime observer has not connected.'
        self._sequence = 0

    def start(self):
        if (self._thread is None or not self._thread.is_alive()) and self.path.exists():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name='codex-worker-observer', daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=4)
        with self._lock:
            for sid in self._rows:
                worker_activity.forget_managed('openai', sid)

    def snapshot(self):
        with self._lock:
            return {sid: dict(row) for sid, row in self._rows.items()}, self._read_at, self._error

    def _event(self, event):
        found = event_evidence(event, time.time())
        if found:
            sid, evidence = found
            worker_activity.record_runtime('openai', sid, evidence)
            with self._lock:
                self._rows.setdefault(sid, {})['activity'] = evidence

    def _request(self, ws, method, params):
        self._sequence += 1
        request_id = self._sequence
        ws.send(json.dumps({'id': request_id, 'method': method, 'params': params}))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            event = json.loads(ws.recv(timeout=max(.01, deadline - time.monotonic())))
            # Server requests have their own ID namespace. They are not replies
            # to us, even when their numeric ID matches an outstanding request.
            if (event.get('id') == request_id and 'method' not in event
                    and ('result' in event or 'error' in event)):
                if 'error' in event:
                    raise RuntimeError(f'{method} refused')
                return event.get('result') or {}
            self._event(event)
        raise TimeoutError(method)

    def _refresh(self, ws, attached):
        cursor = None
        loaded = []
        while True:
            page = self._request(ws, 'thread/loaded/list', {'cursor': cursor, 'limit': 100})
            loaded.extend(page.get('data') or [])
            cursor = page.get('nextCursor')
            if not cursor: break
        for sid in loaded:
            thread = self._request(ws, 'thread/read', {'threadId': sid}).get('thread') or {}
            status = thread.get('status') or {}
            with self._lock:
                row = self._rows.setdefault(sid, {})
                previous_status = row.get('status')
                row.update(status=status.get('type', 'unknown'), observed_at=time.time())
                if status.get('type') != 'active':
                    row['activity'] = {'state': 'idle', 'observed_at': time.time(),
                                       'source': 'codex_runtime:thread/read', 'coverage': 'runtime_status'}
                elif previous_status != 'active':
                    row['activity'] = {'state': 'awaiting_model', 'observed_at': time.time(),
                                       'source': 'codex_runtime:thread/read', 'coverage': 'runtime_status'}
                if status.get('type') == 'active' and status.get('activeFlags'):
                    row['activity'] = {'state': 'human_wait', 'observed_at': time.time(),
                                       'source': 'codex_runtime:thread/read', 'coverage': 'runtime_status'}
            if status.get('type') != 'active' or status.get('activeFlags') or previous_status != 'active':
                worker_activity.record_runtime('openai', sid, row['activity'])
            if status.get('type') == 'active' and sid not in attached:
                self._request(ws, 'thread/resume', {'threadId': sid, 'excludeTurns': True})
                attached.add(sid)
            elif status.get('type') != 'active' and sid in attached:
                self._request(ws, 'thread/unsubscribe', {'threadId': sid})
                attached.discard(sid)
        with self._lock:
            for sid in self._rows.keys() - set(loaded):
                worker_activity.forget_managed('openai', sid)
            self._rows = {sid: row for sid, row in self._rows.items() if sid in loaded}
            self._read_at, self._error = time.time(), None
        attached.intersection_update(loaded)

    def _loop(self):
        while not self._stop.is_set():
            try:
                with unix_connect(str(self.path), uri='ws://localhost', open_timeout=3,
                                  close_timeout=1, max_size=8 * 1024 * 1024) as ws:
                    self._request(ws, 'initialize', {'clientInfo': {'name': 'jarvis_worker_observer',
                                  'title': 'JARVIS worker observer', 'version': '1'},
                                  'capabilities': {'experimentalApi': True, 'requestAttestation': False}})
                    ws.send(json.dumps({'method': 'initialized'}))
                    attached = set()
                    refresh_at = 0
                    while not self._stop.is_set():
                        if time.monotonic() >= refresh_at:
                            self._refresh(ws, attached)
                            refresh_at = time.monotonic() + 2
                        try:
                            self._event(json.loads(ws.recv(timeout=.25)))
                        except TimeoutError:
                            pass
            except Exception as exc:
                with self._lock:
                    for sid in self._rows:
                        worker_activity.forget_managed('openai', sid)
                    self._rows = {}
                    self._read_at = 0
                    self._error = f'Codex runtime observer unavailable: {type(exc).__name__}'
                self._stop.wait(3)
