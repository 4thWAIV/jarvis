import json
import time
from jarvis import worker_hooks as hooks
from jarvis import worker_activity as activity
from jarvis.worker_inventory import build
from jarvis.session_watch import Snapshot, SessionState


def test_hook_observer_preserves_parallel_tools_and_never_records_content(tmp_path):
    def event(kind, tool=None):
        hooks.record(tmp_path, {'hook_event_name':kind, 'session_id':'root', 'agent_id':'child',
                               'tool_use_id':tool, 'tool_input':{'secret':'private'}, 'prompt':'private'})
    event('PreToolUse','a'); event('PreToolUse','b'); event('PostToolUse','a')
    rows, truncated = hooks.observations(tmp_path,time.time())
    assert rows[0]['state'] == 'tool_wait'
    assert rows[0]['tools'] == {'b'}
    assert not truncated
    assert all('private' not in p.read_text() for p in (tmp_path/'worker-observations').glob('*.json'))
    event('PostToolBatch')
    assert hooks.observations(tmp_path,time.time())[0][0]['state'] == 'awaiting_model'


def test_stop_hook_does_not_claim_completed_worker(tmp_path):
    hooks.record(tmp_path, {'hook_event_name':'SubagentStop','session_id':'root','agent_id':'child'})
    assert hooks.observations(tmp_path,time.time())[0][0]['state']=='finishing'


def test_installer_preserves_existing_hooks_and_is_idempotent(tmp_path):
    config=tmp_path/'settings.json'
    config.write_text(json.dumps({'hooks':{'UserPromptSubmit':[{'hooks':[{'type':'command','command':'original'}]}]},'other':1}))
    hooks.install(config,tmp_path/'data'); hooks.install(config,tmp_path/'data')
    body=json.loads(config.read_text())
    assert body['other']==1
    assert len(body['hooks']['UserPromptSubmit'])==2
    assert body['hooks']['UserPromptSubmit'][0]['hooks'][0]['command']=='original'
    assert all(len(body['hooks'][kind])==1 for kind in hooks.EVENTS if kind!='UserPromptSubmit')


def test_new_hook_child_is_visible_but_not_called_model_work(tmp_path):
    now=time.time()
    hooks.record(tmp_path,{'hook_event_name':'SubagentStart','session_id':'root','agent_id':'child'},now)
    rows,_=hooks.observations(tmp_path,now+.1)
    parent=SessionState(session_id='root',provider='anthropic',cwd='/project',project='project',state='idle')
    result=build(Snapshot([parent],now),now=now+.1,native_observations=rows)
    assert result['total']['workers']==2
    assert result['total']['model_work']==0
    assert result['total']['unknown']==1
    assert result['workers'][-1]['parent_id']=='anthropic:root'


def test_model_request_matches_multiple_blocks_without_finishing_thinking():
    def obj(kind,second,**fields):return {'type':kind,'timestamp':f'2026-01-01T00:00:{second:02d}Z',**fields}
    evidence=activity.observation([
        obj('api-request',0,id='request'),
        obj('assistant',5,requestRef='request',message={'stop_reason':'end_turn','content':[{'type':'thinking'}]}),
    ],'anthropic')
    current=activity.current(evidence,evidence['observed_at']+1)
    assert current['model_work'] and not current['generating']
    assert evidence['request_id']=='request'
    assert not activity.current(evidence,evidence['observed_at']+121)['model_work']


def test_tool_batch_results_do_not_resume_model_work_early():
    evidence=activity.observation([
        {'type':'assistant','message':{'content':[{'type':'tool_use','id':'a'},{'type':'tool_use','id':'b'}]}},
        {'type':'user','message':{'content':[{'type':'tool_result','tool_use_id':'a'}]}},
    ],'anthropic')
    assert evidence['state']=='tool_wait'
    assert evidence['outstanding_tools']==['b']


def test_finished_sidecar_invalidates_cache_and_thinking_is_not_finished(tmp_path):
    import os
    from jarvis import session_watch as sw
    path=tmp_path/'agent-child.jsonl'
    path.write_text(json.dumps({'type':'assistant','timestamp':'2026-01-01T00:00:05Z',
                                'message':{'stop_reason':'end_turn','content':[{'type':'thinking'}]}})+'\n')
    side=tmp_path/'agent-child.meta.json'
    side.write_text(json.dumps({'finished':False,'parentAgentId':'parent'}))
    assert not sw._agent_facts(path,path.stat())['finished']
    assert not sw._finished(path)
    side.write_text(json.dumps({'finished':True,'parentAgentId':'parent'}))
    assert sw._agent_facts(path,path.stat())['finished']
    assert sw._agent_facts(path,path.stat())['parent_agent_id']=='parent'
    os.utime(side,(100,100))
    with path.open('a') as out:
        out.write(json.dumps({'type':'api-request','id':'resume','timestamp':'2026-01-01T00:00:06Z'})+'\n')
    assert not sw._agent_facts(path,path.stat())['finished']


def test_unobserved_hook_session_is_not_reported_as_hook_covered():
    root=SessionState(session_id='root',provider='anthropic',cwd='/p',project='p',state='idle')
    result=build(Snapshot([root],100),now=100)
    assert result['workers'][0]['native_hook_coverage']=='not_observed'


def test_brain_request_and_parallel_tools_have_distinct_counts():
    events=[{'kind':'turn','stage':'started','provider':'openai','at':100}]
    assert build(Snapshot([],100),now=100,brain_events={'events':events})['total']['model_work']==1
    events.extend([{'kind':'tool','stage':'started','provider':'openai','call_id':'a','at':100},
                   {'kind':'tool','stage':'started','provider':'openai','call_id':'b','at':100},
                   {'kind':'tool','stage':'completed','provider':'openai','call_id':'a','at':100}])
    result=build(Snapshot([],100),now=100,brain_events={'events':events})
    assert result['total']['model_work']==0 and result['total']['tool_wait']==1
    events.append({'kind':'tool','stage':'completed','provider':'openai','call_id':'b','at':100})
    assert build(Snapshot([],100),now=100,brain_events={'events':events})['total']['model_work']==1
