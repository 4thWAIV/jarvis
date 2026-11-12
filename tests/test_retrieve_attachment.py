from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from jarvis import agent_rights, retrieve_attachment

MESSAGE = '117be02f-bb68-4fd0-a786-bc670ff07d15'
ATTACHMENT = '786c166e-2eae-4545-8463-62bee0fa9f6d'
BASE = f'/api/team/messages/{MESSAGE}'
ARGS = ['--message', MESSAGE, '--attachment', ATTACHMENT, '--as', 'origin']


@pytest.mark.parametrize('scope,allowed', [('*', True), ('work:owned', False)])
def test_retrieval_right_and_scope(monkeypatch, scope, allowed):
    from jarvis import agent_key_store
    monkeypatch.setattr(agent_key_store, 'grants', lambda _: [SimpleNamespace(right='packages', scope=scope)])
    for path in (f'{BASE}/files/{ATTACHMENT}/location',
                 f'{BASE}/files/{ATTACHMENT}/download',
                 f'{BASE}/attachments/{ATTACHMENT}/bundle'):
        assert agent_rights.allows('agent:origin', 'POST', path, 'work=owned') is allowed
    for path in (f'{BASE}/files/{ATTACHMENT}/reveal',
                 f'{BASE}/attachments/{ATTACHMENT}/repair',
                 f'{BASE}/attachments/{ATTACHMENT}/bundle/members/a/show',
                 '/api/team/messages/drafts', '/internal/tool', '/api/restart'):
        assert not agent_rights.allows('agent:origin', 'POST', path)


@pytest.mark.parametrize('engine', ['claude', 'codex'])
def test_same_signed_retrieval_for_either_engine(monkeypatch, capsys, engine):
    calls = []
    def post(path, body):
        calls.append((path, body))
        data = ({'state': 'verified', 'local_path': '/downloads/received.zip', 'sha256': 'abc'}
                if path.endswith('/location') else {'kind': 'archive'})
        return SimpleNamespace(status_code=200, json=lambda: data)
    def client(cert, session, right):
        assert session == engine and right == 'packages'
        return nullcontext(SimpleNamespace(post=post))
    monkeypatch.setattr(retrieve_attachment, 'signed_client', client)
    assert retrieve_attachment.main(ARGS[:-1] + [engine, '--inspect']) == 0
    assert calls == [(f'{BASE}/files/{ATTACHMENT}/location', {}),
                     (f'{BASE}/attachments/{ATTACHMENT}/bundle', {})]
    assert '/downloads/received.zip' in capsys.readouterr().out


@pytest.mark.parametrize('status,state', [(403, ''), (200, 'downloading'), (200, 'failed')])
def test_no_inspection_after_failed_or_incomplete_retrieval(monkeypatch, status, state):
    calls = []
    def post(path, body):
        calls.append(path)
        return SimpleNamespace(status_code=status, text='refused', json=lambda: {'state': state})
    monkeypatch.setattr(retrieve_attachment, 'signed_client', lambda *a: nullcontext(SimpleNamespace(post=post)))
    assert retrieve_attachment.main(ARGS + ['--inspect']) == 1
    assert calls == [f'{BASE}/files/{ATTACHMENT}/location']
