"""Neutral Claude lifecycle observer. Never returns a hook decision or content.

Install explicitly during deployment. Only metadata is retained, in short-lived
atomic files; the runtime remains Claude's. Configuration does not prove a
running session loaded the hook: only received events establish coverage.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import time
import threading
import uuid

EVENTS = ('SessionStart', 'UserPromptSubmit', 'SubagentStart', 'SubagentStop',
          'PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'PostToolBatch',
          'PermissionRequest', 'Stop', 'StopFailure', 'SessionEnd')
RETENTION_SECONDS = 600
_cache = {}
_cache_lock = threading.Lock()
OBSERVER_ID = "jarvis-native-worker-v1"


def record(root: Path, obj: dict, now=None):
    now = time.time() if now is None else now
    kind, sid = obj.get('hook_event_name'), obj.get('session_id')
    if kind not in EVENTS or not isinstance(sid, str) or not sid:
        return
    agent = obj.get('agent_id')
    event = {'kind': kind, 'session_id': sid[:128], 'agent_id': str(agent)[:128] if agent else None,
             'tool_use_id': str(obj.get('tool_use_id') or '')[:128],
             'tool_name': str(obj.get('tool_name') or '')[:80], 'at': now,
             'completed_tools': [str(call.get('tool_use_id'))[:128] for call in obj.get('tool_calls', [])[:1000]
                                 if isinstance(call, dict) and call.get('tool_use_id')]}
    directory = root / 'worker-observations'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f'{time.time_ns()}-{uuid.uuid4().hex}.json'
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(event, out)
    os.replace(temporary, path)


def observations(root: Path, now: float):
    directory = root / 'worker-observations'
    groups = {}
    files = sorted(directory.glob('*.json')) if directory.exists() else []
    truncated = len(files) > 5000
    selected = set(files[-5000:])
    for path in files:
        try:
            if now - path.stat().st_mtime > RETENTION_SECONDS:
                path.unlink(missing_ok=True)
                continue
            if truncated and path not in selected:
                continue
            with _cache_lock:
                event = _cache.get(str(path))
            if event is None:
                event = json.loads(path.read_text())
                with _cache_lock:
                    _cache[str(path)] = event
                    while len(_cache) > 5000:
                        _cache.pop(next(iter(_cache)))
            if not 0 <= now - event['at'] <= RETENTION_SECONDS:
                continue
            sid = event.get('agent_id') or event['session_id']
            row = groups.setdefault(sid, {'session_id': sid, 'parent_id': event['session_id'] if event.get('agent_id') else None,
                                         'tools': set(), 'finished_tools': set(), 'coverage': 'native_hooks'})
            kind, tool = event['kind'], event.get('tool_use_id')
            phase = None
            if kind == 'PreToolUse':
                if tool and tool in row['finished_tools']:
                    continue  # An asynchronous start arrived after its completion.
                if tool: row['tools'].add(tool)
                phase = 'tool_wait'
            elif kind in ('PostToolUse', 'PostToolUseFailure'):
                row['tools'].discard(tool)
                if tool: row['finished_tools'].add(tool)
                phase = 'tool_wait' if row['tools'] else 'awaiting_model'
            elif kind == 'PostToolBatch':
                row['finished_tools'].update(event.get('completed_tools') or [])
                row['tools'].clear()
                phase = 'awaiting_model'
            elif kind == 'PermissionRequest': phase = 'human_wait'
            elif kind == 'SessionEnd': phase = 'offline'
            elif kind in ('Stop', 'SubagentStop', 'StopFailure'): phase = 'finishing'
            else: phase = 'awaiting_model'
            row.update(state=phase, observed_at=event['at'], source=f'claude_hook:{kind}',
                       turn_open=False if kind in ('SessionEnd', 'Stop', 'SubagentStop', 'StopFailure')
                       else None if kind == 'SessionStart' else True)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return list(groups.values()), truncated


def install(config: Path, data: Path):
    if config.is_symlink():
        raise ValueError('Install into the settings file target, not a symlink.')
    body = json.loads(config.read_text()) if config.exists() else {}
    hooks = body.setdefault('hooks', {})
    command = ' '.join(shlex.quote(v) for v in (sys.executable, str(Path(__file__).resolve()), '--data-dir', str(data.resolve()), '--observer-id', OBSERVER_ID))
    for kind in EVENTS:
        rules = hooks.setdefault(kind, [])
        # Replace only our previous recorder, even after an install moves.
        for rule in list(rules):
            rule['hooks'] = [h for h in rule.get('hooks', []) if OBSERVER_ID not in h.get('command', '')]
            if not rule['hooks']:
                rules.remove(rule)
        if not any(h.get('command') == command for rule in rules for h in rule.get('hooks', [])):
            rules.append({'hooks': [{'type': 'command', 'command': command, 'timeout': 2, 'async': True}]})
    config.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.with_suffix('.worker-observer.tmp')
    temporary.write_text(json.dumps(body, indent=2) + '\n')
    os.chmod(temporary, 0o600)
    os.replace(temporary, config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--install', type=Path)
    parser.add_argument('--observer-id', default=OBSERVER_ID)
    args = parser.parse_args()
    if args.install:
        install(args.install, args.data_dir)
    else:
        try:
            record(args.data_dir, json.load(sys.stdin))
        except Exception:
            # Observation failure must never block, approve or steer work.
            pass

if __name__ == '__main__':
    main()
