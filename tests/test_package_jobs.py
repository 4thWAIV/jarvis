"""Packages as jobs (package_jobs.py): started at once, built in a process of
their own, the author's questions asked of its sender and answered, and the
same author session resumed, its proof still passing."""
import base64
import json
import sys
import time

import pytest

from jarvis import author_proof, make_package as mp, package_author as pa, package_jobs as pj
from jarvis import agent_mcp, decision_package as dp, package_signature, records
from tests.test_decision_package import _Delivery
from tests.test_package_author import handoff, only_engine, origin_key  # noqa: F401 (fixtures)


@pytest.fixture
def spawned(monkeypatch):
    """The job ids that would have started a detached process."""
    started = []
    monkeypatch.setattr(pj, 'SPAWN', lambda job_id, doc: started.append(job_id))
    return started


@pytest.fixture
def delivery(monkeypatch):
    sent = _Delivery()
    monkeypatch.setattr(dp, 'post', sent)
    return sent


def start(tmp_path, engine, **kw):
    return pj.package_start(handoff(tmp_path), kw.pop('work', 'origin-work'), session='origin-session',
                            engine=engine, out=tmp_path / 'out',
                            deliver_to='http://127.0.0.1:19999', **kw)


def states(job_id):
    return [v.data['state'] for v in records.history(pj.path_of(job_id), fields=['state'])]


def turns(job_id):
    folder = pj.package_status(job_id)['source_folder']
    return [json.loads(line) for line in open(folder + '/turns.log')]


def test_start_records_the_job_and_returns_at_once(tmp_path, spawned, monkeypatch):
    monkeypatch.setenv('JARVIS_AGENT_SESSION', 'origin-session')
    answer = pj.package_start(handoff(tmp_path), 'origin-work', engine='claude',
                              out=tmp_path / 'out')
    assert answer == {'job': spawned[0], 'state': pj.QUEUED}
    job = pj.package_status(answer['job'])
    assert job['session'] == 'origin-session' and job['work'] == 'origin-work'
    argv = job['argv']
    assert argv[argv.index('--handoff') + 1] == str((tmp_path / 'handoff.json').resolve())
    assert argv[argv.index('--session') + 1] == 'origin-session'
    assert argv[argv.index('--work') + 1] == 'origin-work'
    assert argv[argv.index('--out') + 1] == str((tmp_path / 'out').resolve())
    assert job['rounds'] == [] and job['author_session'] is None


def test_start_refuses_what_cannot_be_a_job(tmp_path, spawned):
    with pytest.raises(pj.JobError, match='not both'):
        pj.package_start(handoff(tmp_path), 'w', True, session='s', engine='claude')
    with pytest.raises(pj.JobError, match='no handoff'):
        pj.package_start(tmp_path / 'missing.json', session='s', engine='claude')
    with pytest.raises(pj.JobError, match='sending session'):
        pj.package_start(handoff(tmp_path), engine='claude')
    with pytest.raises(pj.JobError, match='which engine'):
        pj.package_start(handoff(tmp_path), session='unknown-session')
    assert spawned == []


def test_the_job_process_is_detached(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pj.subprocess, 'Popen', lambda argv, **kw: calls.append((argv, kw)))
    job_id = start(tmp_path, 'claude')['job']
    [(argv, kw)] = calls
    assert argv == [sys.executable, '-P', '-m', 'jarvis.package_jobs', 'run', job_id]
    assert kw['start_new_session'] is True and kw['stdin'] is pj.subprocess.DEVNULL
    assert kw['stdout'].name == str(tmp_path / 'out' / pj.LOG)


def test_a_job_delivers(only_engine, origin_key, tmp_path, spawned, delivery):
    job_id = start(tmp_path, only_engine)['job']
    assert pj.run(job_id) == 0
    job = pj.package_status(job_id)
    assert job['state'] == pj.DELIVERED and job['rounds'] == []
    assert job['result']['inbox'] and job['result']['work'] == 'origin-work'
    assert states(job_id) == [pj.QUEUED, pj.QUEUED, pj.RESEARCHING, pj.WRITING, pj.CHECKING,
                              pj.DELIVERED]
    assert job['author_engine'] == only_engine


def test_questions_wait_on_the_sender_and_the_same_author_resumes(
        only_engine, origin_key, tmp_path, spawned, delivery, monkeypatch):
    monkeypatch.setenv('AUTHOR_TEST_MODE', 'ask:1')
    job_id = start(tmp_path, only_engine)['job']
    assert pj.run(job_id) == 0
    job = pj.package_status(job_id)
    assert job['state'] == pj.WAITING and delivery.calls == []
    [asked] = job['rounds']
    assert [q['id'] for q in asked['questions']] == ['release-date', 'owner']
    assert asked['questions'][0]['why'] == 'The conclusion depends on it.'
    author = job['author_session']
    assert author and author != 'origin-session'

    job = pj.package_answer(job_id, {'release-date': 'Monday 10:00'}, session='origin-session')
    assert job['state'] == pj.QUEUED and spawned == [job_id, job_id]
    assert pj.run(job_id) == 0
    job = pj.package_status(job_id)
    assert job['state'] == pj.DELIVERED and job['author_session'] == author
    # Its first turn asked; the second resumed it with the answers; the
    # briefing and the deck followed in the same session.
    first, second, *rest = turns(job_id)
    assert all(t['resumed'] for t in rest)
    assert not first['resumed'] and first['text'].startswith(author_proof.PROMPT_HEAD)
    assert second['resumed'], 'the same session, resumed'
    assert 'Monday 10:00' in second['text'] and 'UNANSWERED' in second['text']
    assert 'owner (Who owns the fix?)' in second['text']
    if only_engine == 'codex':
        log = [json.loads(line) for line in (tmp_path / 'codex.log').read_text().splitlines()]
        [resumed] = [row['resume'] for row in log if 'resume' in row]
        assert resumed['threadId'] == author and resumed['sandbox'] == 'workspace-write'

    # The receiver's proof passes for the resumed author.
    [(_, _, body)] = delivery.calls
    sent = tmp_path / 'sent.zip'
    sent.write_bytes(base64.b64decode(body['package']))
    signed = package_signature.read(sent)
    assert signed.author['session'] == author
    assert author_proof.problem(signed.author, 'origin-session', sent, time.time()) is None


def test_after_two_rounds_the_rest_goes_to_tim_unanswered(
        only_engine, origin_key, tmp_path, spawned, delivery, monkeypatch):
    monkeypatch.setenv('AUTHOR_TEST_MODE', 'ask:3')
    job_id = start(tmp_path, only_engine)['job']
    for answer in ('Monday', 'Tuesday'):
        assert pj.run(job_id) == 0
        assert pj.package_status(job_id)['state'] == pj.WAITING
        pj.package_answer(job_id, {'release-date': answer, 'owner': 'Ops'})
    assert pj.run(job_id) == 0
    job = pj.package_status(job_id)
    assert job['state'] == pj.DELIVERED and len(job['rounds']) == pj.MAX_ROUNDS
    assert [q['id'] for q in job['unanswered']] == ['release-date', 'owner']
    told = [t['text'] for t in turns(job_id) if 'no more question rounds' in t['text']]
    assert len(told) == 1 and 'UNANSWERED' in told[0]
    assert turns(job_id)[-1]['text'].startswith('The script passed'), \
        "the deck is still written from the passed script"


def test_an_author_that_never_stops_asking_is_refused(
        only_engine, origin_key, tmp_path, spawned, delivery, monkeypatch):
    monkeypatch.setenv('AUTHOR_TEST_MODE', 'ask:9')
    job_id = start(tmp_path, only_engine)['job']
    for _ in range(pj.MAX_ROUNDS):
        pj.run(job_id)
        pj.package_answer(job_id, {'owner': 'Ops'})
    assert pj.run(job_id) == 1
    job = pj.package_status(job_id)
    assert job['state'] == pj.REFUSED and delivery.calls == []
    assert 'still needs evidence after every question round' in job['problems'][0]


def _waiting(job_id, author='author-1'):
    pj.Progress(job_id).ask([{'id': 'q1', 'question': 'Which?', 'why': ''}],
                            author_session=author)


def test_answers_are_only_the_sender_s_to_its_open_questions(tmp_path, spawned):
    job_id = start(tmp_path, 'claude')['job']
    with pytest.raises(pj.JobError, match='not waiting'):
        pj.package_answer(job_id, {'q1': 'yes'})
    _waiting(job_id)
    with pytest.raises(pj.JobError, match="session origin-session's"):
        pj.package_answer(job_id, {'q1': 'yes'}, session='someone-else')
    with pytest.raises(pj.JobError, match='no question q2'):
        pj.package_answer(job_id, {'q2': 'yes'})
    with pytest.raises(pj.JobError, match='is words'):
        pj.package_answer(job_id, {'q1': '  '})
    with pytest.raises(pj.JobError, match='no package job'):
        pj.package_answer('job-missing', {'q1': 'yes'})
    assert pj.package_answer(job_id, {'q1': 'yes'})['state'] == pj.QUEUED


def test_a_job_whose_process_died_is_refused_on_reading(tmp_path, spawned):
    job_id = start(tmp_path, 'claude')['job']
    pj.Progress(job_id)._set('running', pid=2 ** 22 + 12345, state=pj.RESEARCHING)
    job = pj.package_status(job_id)
    assert job['state'] == pj.REFUSED and 'ended without finishing' in job['problems'][0]


def test_a_job_that_never_ran_is_not_run_again(tmp_path, spawned, capsys):
    job_id = start(tmp_path, 'claude')['job']
    pj.Progress(job_id).delivered({'inbox': 'pkg-1'})
    assert pj.run(job_id) == 1
    assert 'delivered; nothing to run' in capsys.readouterr().out


def test_the_command_starts_follows_and_answers(tmp_path, spawned, capsys):
    assert mp.main(['--start', '--handoff', str(handoff(tmp_path)), '--engine', 'claude',
                    '--session', 'origin-session', '--work', 'w1',
                    '--out', str(tmp_path / 'out')]) == 0
    started = json.loads(capsys.readouterr().out)
    assert started['state'] == pj.QUEUED
    _waiting(started['job'])
    assert mp.main(['--status', started['job']]) == 0
    assert json.loads(capsys.readouterr().out)['state'] == pj.WAITING
    assert mp.main(['--answer', started['job'], '--reply', 'q1=The second one',
                    '--session', 'origin-session']) == 0
    answered = json.loads(capsys.readouterr().out)
    assert answered['rounds'][0]['answers'] == {'q1': 'The second one'}
    assert mp.main(['--status', 'job-missing']) == 1
    assert 'no package job' in capsys.readouterr().out


def test_without_a_job_the_author_s_questions_refuse_it(only_engine, origin_key, tmp_path,
                                                        delivery, monkeypatch, capsys):
    monkeypatch.setenv('AUTHOR_TEST_MODE', 'ask:1')
    assert mp.main(['--handoff', str(handoff(tmp_path)), '--engine', only_engine,
                    '--session', 'origin-session', '--out', str(tmp_path / 'out')]) == 1
    out = capsys.readouterr().out
    assert 'Author needs evidence: When did the release go out?; Who owns the fix?' in out


def test_the_author_s_questions_are_structured(tmp_path):
    (tmp_path / pa.QUESTIONS).write_text(json.dumps({'questions': [
        {'id': 'a', 'question': 'One?'}, {'id': 'a', 'question': 'Two?'}]}))
    with pytest.raises(OSError, match='its own short id'):
        pa.read_questions(tmp_path)
    (tmp_path / pa.QUESTIONS).unlink()
    (tmp_path / pa.BLOCKED).write_text('The release receipt is missing.')
    assert pa.read_questions(tmp_path) == [{'id': 'q1', 'why': '',
                                            'question': 'The release receipt is missing.'}]


def test_engines_resume_the_author_s_own_session(tmp_path):
    from jarvis.brain import BrainConfig
    from jarvis.claude_provider import ClaudeSession
    from jarvis import codex_provider
    mcp = pa.agent_mcp_config(tmp_path, 'origin-session')
    cfg = BrainConfig(home=tmp_path, authoring=True, model=None, effort=None,
                      resume='author-1', mcp_config=mcp)
    cmd = ClaudeSession(cfg, None, None).command('fresh instructions')
    assert cmd[cmd.index('--resume') + 1] == 'author-1'
    assert cmd[cmd.index('--mcp-config') + 1] == str(mcp) and '--strict-mcp-config' in cmd
    overrides = codex_provider.config_overrides(codex_provider._servers_of(mcp))
    assert f'mcp_servers.{pa.AGENT_MCP}.command="{sys.executable}"' in overrides
    args = json.dumps(agent_mcp.entry('origin-session')['args'])
    assert f'mcp_servers.{pa.AGENT_MCP}.args={args}' in overrides, "agent_mcp.entry, the one description"
