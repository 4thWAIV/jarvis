"""One inventory for UI and brain tools; workers count once by provider/session.

Containers (projects, workflows and capacity) are not token consumers. A
worker may belong to several containers without being counted several times.
"""
from __future__ import annotations

from jarvis import worker_activity


def identity(provider, session):
    return f'{provider}:{session}'


def build(snapshot, names=None, runs=(), brain_events=None, now=0, workflows=(), capacity=None, native_observations=(), native_truncated=False):
    names = names or {}
    rows = {}
    gaps = list(snapshot.provider_errors)
    if native_truncated:
        gaps.append('Native hook history exceeded its observation window; phase reconstruction may be incomplete.')

    def add(provider, sid, name, project, evidence, state, kind='agent', parent=None, workflow=None, runtime=None):
        key = identity(provider, sid)
        streamed = worker_activity.managed(provider, sid)
        latest = max((streamed, evidence or {}), key=lambda e: e.get('observed_at') or 0)
        live = worker_activity.current(latest, now)
        if latest.get('coverage_gap'):
            gaps.append(latest['coverage_gap'])
        # Session completion/liveness beats any previously observed output.
        if not worker_activity.managed(provider, sid) and state in ('gone', 'idle', 'finished', 'needs_you'):
            live.update(state='idle' if state != 'gone' else 'offline', generating=False, model_work=False)
        row = {'id': key, 'session_id': sid, 'provider': provider, 'name': name,
               'project': project, 'parent_id': identity(provider, parent) if parent else None,
               'kind': kind, 'workflow_id': workflow, 'allocation': 'unknown' if kind == 'subagent' else 'observed',
               'activity': live, 'runtime': runtime or {}}
        if runtime and runtime.get('status') in ('active', 'idle'):
            row['allocation'] = 'loaded'
        if runtime and runtime.get('error') and runtime['error'] not in gaps:
            gaps.append(runtime['error'])
        # Prefer a structured session over its duplicate workflow/subagent record.
        if key in rows:
            existing = rows[key]
            if parent and not existing['parent_id']: existing['parent_id'] = row['parent_id']
            if workflow: existing['workflow_id'] = workflow
        else:
            rows[key] = row
        return rows[key]

    stale = not snapshot.taken_at or now - snapshot.taken_at > 15
    if stale:
        gaps.append('Session discovery is stale or unavailable.')
    for s in snapshot.sessions:
        if s.state == 'gone':
            continue
        add(s.provider, s.session_id, names.get(s.session_id) or s.given_name or s.voice_name or s.project,
            s.project, s.worker_activity, s.state, 'subagent' if s.parent_session_id else 'agent', s.parent_session_id, runtime=s.runtime_status)
        if s.agents_capped:
            gaps.append(f'Subagent discovery was capped for {s.session_id}.')
        for a in s.subagents:
            add(s.provider, a.agent_id, a.description or a.agent_type or a.agent_id, s.project,
                a.worker_activity, a.state, 'subagent', a.parent_agent_id or s.session_id, a.workflow_run, runtime=a.runtime_status)
    for run in runs:
        key = identity('anthropic', run['id'])
        row = rows.get(key) or add('anthropic', run['id'], run.get('title') or run.get('task') or 'Run',
                                  run.get('project_name') or '', {}, 'unknown', 'run')
        row['run_id'] = run['id']
        if run.get('status') == 'queued':
            row['allocation'] = 'queued'
            row['activity'] = {'state': 'queued', 'generating': False, 'source': 'run_queue'}
    # Children from managed execution appear before their transcript is flushed.
    # The executor owns their lifetime; a restart/exit removes the registry.
    for child in worker_activity.managed_children():
        owner = rows.get(identity('anthropic', child['owner']))
        if owner is None:
            continue
        row = add('anthropic', child['session_id'], child['name'], owner['project'], {},
                  'unknown', 'subagent', child['parent_id'])
        row['runtime'] = {'status': child['state'], 'observed_at': now, 'source': 'managed_process'}
        row['allocation'] = 'loaded'
    for hook in native_observations:
        sid = hook['session_id']
        key = identity('anthropic', sid)
        row = rows.get(key)
        parent = rows.get(identity('anthropic', hook.get('parent_id')))
        if row is None and parent is not None:
            row = add('anthropic', sid, 'Claude native child', parent['project'], {}, 'unknown', 'subagent', hook['parent_id'])
        if row is None:
            continue  # Old hooks do not resurrect an exited session.
        row['hook_observed_at'] = hook.get('observed_at')
        # Async prompt/start/post-tool hooks report receipt time, not a model
        # boundary. They cannot erase a current, directly observed request.
        if hook.get('state') == 'awaiting_model' and row['activity'].get('model_work'):
            continue
        if (hook.get('observed_at') or 0) > (row['activity'].get('observed_at') or 0):
            evidence = {k: v for k, v in hook.items() if k not in ('tools', 'finished_tools', 'parent_id', 'session_id')}
            if now - evidence['observed_at'] > worker_activity.MODEL_REQUEST_LEASE_SECONDS:
                evidence.update(last_phase=evidence['state'], state='unknown')
            row['activity'] = worker_activity.current(evidence, now)
    # Brain stream is already recorded at the producer; don't derive activity
    # from conversation text or start another pulse/timer mechanism.
    brain_tools = {}
    for event in (brain_events or {}).get('events', []):
        if event.get('kind') not in ('turn', 'tool'):
            continue
        provider = event.get('provider') or 'unknown'
        stage = event.get('stage')
        if event['kind'] == 'turn':
            state = ('idle' if stage in ('completed', 'failed', 'cancelled') else
                     'generating' if stage in ('first_output', 'streaming') else 'model_work')
            if state == 'idle': brain_tools.pop(provider, None)
        else:
            active_tools = brain_tools.setdefault(provider, set())
            call = event.get('call_id') or event.get('id') or event.get('tool') or 'unknown'
            if stage == 'started': active_tools.add(call)
            else: active_tools.discard(call)
            state = 'tool_wait' if active_tools else 'model_work'
        row = add(provider, 'brain', 'JARVIS brain', '', {}, 'unknown', 'brain')
        row['activity'] = worker_activity.current({'state': state, 'observed_at': event.get('at'),
                                                  'source': 'brain_stream', 'coverage': 'stream'}, now)
    if stale:
        for row in rows.values():
            if row['kind'] != 'brain' and not worker_activity.managed(row['provider'], row['session_id']):
                row['activity'] = {**row['activity'], 'state': 'unknown', 'generating': False, 'model_work': False}
    for workflow in workflows:
        for sid in workflow.agents:
            found = [row for row in rows.values() if row['session_id'] == sid]
            if not found:
                gaps.append(f'Workflow {workflow.name} references an undiscovered worker {sid}.')
            for row in found:
                row.setdefault('workflows', []).append({'id': workflow.id, 'name': workflow.name})
    workers = list(rows.values())
    for row in workers:
        if row['provider'] == 'anthropic' and row['kind'] in ('agent', 'subagent'):
            row['native_hook_coverage'] = 'observed' if row.get('hook_observed_at') else 'not_observed'
    for row in workers:
        seen = {row['id']}
        parent = row['parent_id']
        while parent in rows:
            if parent in seen:
                gaps.append(f"Parent cycle reported for {row['name']}.")
                row['parent_id'] = None
                break
            seen.add(parent)
            parent = rows[parent]['parent_id']
    for row in workers:
        if row['parent_id'] and row['parent_id'] not in rows:
            gaps.append(f"Parent not discovered for {row['name']}.")
    def counts(items):
        return {'workers': len(items), 'model_work': sum(r['activity'].get('model_work', False) for r in items), 'generating': sum(r['activity']['generating'] for r in items),
                'unknown': sum(r['activity']['state'] in ('unknown', 'awaiting_model', 'recent_output', 'finishing') for r in items),
                'tool_wait': sum(r['activity']['state'] == 'tool_wait' for r in items),
                'runtime_active': sum(r['runtime'].get('status') == 'active' and 0 <= now - (r['runtime'].get('observed_at') or 0) <= 10 for r in items),
                'queued': sum(r['activity']['state'] == 'queued' for r in items)}
    def descendants(key):
        found = {key}
        while True:
            extra = {r['id'] for r in workers if r['parent_id'] in found} - found
            if not extra: break
            found.update(extra)
        return [r for r in workers if r['id'] in found]
    for row in workers:
        row['counts'] = counts(descendants(row['id']))
    projects = {p: counts([r for r in workers if r['project'] == p]) for p in {r['project'] for r in workers} if p}
    providers = {p: counts([r for r in workers if r['provider'] == p]) for p in {r['provider'] for r in workers}}
    return {'workers': workers, 'total': counts(workers), 'projects': projects, 'providers': providers,
            'capacity': capacity, 'taken_at': now, 'evidence_window_seconds': worker_activity.EVIDENCE_SECONDS,
            'generation': (brain_events or {}).get('generation'), 'gaps': gaps,
            'coverage': 'Model work means an observed model-request phase, including possible provider wait; it is not continuous backend token generation. Tools and unconfirmed observations are counted separately. Native Claude delegation is unchanged.'}
