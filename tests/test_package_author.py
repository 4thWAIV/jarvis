"""Fresh same-engine authorship is mandatory before factory delivery."""
import json
from pathlib import Path
import sys

import pytest

from jarvis import package_author as pa, make_package as mp, decision_package as dp
from tests.test_make_package import passing_check, presentation
from tests.test_decision_package import _Delivery

FIXTURES = Path(__file__).parent / "fixtures"


def handoff(tmp_path):
    path = tmp_path / 'handoff.json'
    path.write_text(json.dumps({'purpose': 'Explain the result', 'audience': 'A busy reader',
                               'questions': ['What changed?'], 'evidence': ['receipt.json']}))
    return path


@pytest.fixture(params=['claude', 'codex'])
def only_engine(request, tmp_path, monkeypatch):
    """Execute the real adapter with only its engine available on PATH."""
    engine = request.param
    folder = tmp_path / 'bin'
    folder.mkdir()
    original = (FIXTURES / ('fake_brain.py' if engine == 'claude' else 'fake_codex.py')).read_text()
    start = 'def reply(text):' if engine == 'claude' else 'def turn(rid, text):'
    action = '''
    with open('received-prompt.txt', 'w') as output:
        output.write(text)
    mode = os.environ.get('AUTHOR_TEST_MODE', 'source')
    if mode == 'source':
        with open('content.json', 'w') as output:
            output.write(os.environ['AUTHOR_TEST_CONTENT'])
    elif mode == 'blocked':
        with open('author-blocked.md', 'w') as output:
            output.write('Missing release receipt.')
'''
    fake = folder / engine
    fake.write_text(original.replace('#!/usr/bin/env python3', '#!' + sys.executable).replace(start, start + action))
    fake.chmod(0o755)
    monkeypatch.setenv('PATH', str(folder))
    monkeypatch.delenv('JARVIS_CLAUDE_PATH', raising=False)
    monkeypatch.delenv('JARVIS_CODEX_PATH', raising=False)
    monkeypatch.setenv('AUTHOR_TEST_CONTENT', json.dumps(presentation()))
    monkeypatch.setenv('FAKE_CODEX_LOG', str(tmp_path / 'codex.log'))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'caller-codex-home'))
    return engine


@pytest.fixture
def origin_key(only_engine):
    """The originating session's own key, which its delivery is signed with
    (agent keys: nothing is delivered unsigned), made once the engine's
    homes are set."""
    from jarvis import agent_keys
    return agent_keys.make_pair(agent_keys.key_folder('origin-session'),
                                'jarvis-agent origin-session')


def test_factory_launches_only_callers_engine_and_preserves_origin(only_engine, origin_key, tmp_path,
                                                                   monkeypatch):
    delivery = _Delivery()
    monkeypatch.setattr(dp, 'post', delivery)
    passing_check(monkeypatch)
    source = handoff(tmp_path)
    out = tmp_path / 'out'
    assert mp.main(['--handoff', str(source), '--engine', only_engine,
                    '--session', 'origin-session', '--work', 'origin-work', '--workflow', 'origin-workflow',
                    '--out', str(out), '--deliver-to', 'http://127.0.0.1:19999']) == 0
    [(url, _, _)] = delivery.calls
    assert 'session=origin-session' in url and 'work=origin-work' in url and 'workflow=origin-workflow' in url
    [receipt_path] = out.glob('author/*/author-receipt.json')
    receipt = json.loads(receipt_path.read_text())
    assert receipt['status'] == 'authored' and receipt['engine'] == only_engine
    assert receipt['author_session'] != receipt['origin_session'] == 'origin-session'
    prompt = (receipt_path.parent / 'received-prompt.txt').read_text()
    assert str(pa.SKILL) in prompt and str(source) in prompt
    assert pa.session_skill._make_package() in prompt
    assert 'do not launch another author' in prompt
    if only_engine == 'codex':
        log = [json.loads(line) for line in (tmp_path / 'codex.log').read_text().splitlines()]
        start = next(row for row in log if 'argv' in row)
        assert start['codex_home'] == str(tmp_path / 'caller-codex-home')
        assert start['argv'] == ['app-server']
        thread = next(row['thread'] for row in log if 'thread' in row)
        assert thread['sandbox'] == 'workspace-write'
        assert 'resume' not in thread


@pytest.mark.parametrize('mode', ['missing', 'blocked'])
def test_failed_author_never_delivers(only_engine, mode, tmp_path, monkeypatch):
    monkeypatch.setenv('AUTHOR_TEST_MODE', mode)
    monkeypatch.setattr(dp, 'post', lambda *args, **kwargs: pytest.fail('failed author delivered'))
    assert mp.main(['--handoff', str(handoff(tmp_path)), '--engine', only_engine,
                    '--session', 'origin', '--out', str(tmp_path / 'out')]) == 1
    [receipt] = (tmp_path / 'out').glob('author/*/author-receipt.json')
    assert json.loads(receipt.read_text())['status'] == 'failed'


def test_creation_cannot_bypass_author_with_source(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'content.json'
    source.write_text(json.dumps(presentation()))
    monkeypatch.setattr(dp, 'post', lambda *a, **kw: pytest.fail('bypassed author'))
    assert mp.main([str(source), '--session', 'origin']) == 1
    assert 'requires --handoff' in capsys.readouterr().out
    assert mp.main([str(source), '--check-only']) == 0


def test_invalid_handoff_never_starts_engine(tmp_path, monkeypatch):
    source = handoff(tmp_path)
    source.write_text('{}')
    monkeypatch.setattr(pa.brain_provider, 'provider', lambda *a: pytest.fail('invalid handoff launched'))
    with pytest.raises(dp.Refused):
        pa.prepare(source, 'codex', tmp_path / 'out', session='origin')


def test_claude_author_uses_coding_tools_without_impersonating_jarvis(tmp_path):
    from jarvis.brain import BrainConfig
    from jarvis.claude_provider import ClaudeSession
    cfg = BrainConfig(home=tmp_path, authoring=True, model=None, effort=None)
    cmd = ClaudeSession(cfg, None, None).command('fresh instructions')
    assert cmd[cmd.index('--name') + 1] == 'package-author'
    assert cmd[cmd.index('--tools') + 1] == 'default'
    assert '--resume' not in cmd and '--continue' not in cmd
    assert '--model' not in cmd


@pytest.mark.parametrize("engine", ["codex", "claude"])
def test_missing_selected_engine_does_not_try_other_engine(tmp_path, monkeypatch, engine):
    folder = tmp_path / 'bin'
    folder.mkdir()
    opposite = folder / ('claude' if engine == 'codex' else 'codex')
    opposite.write_text('#!' + sys.executable + '\nraise RuntimeError("wrong engine launched")\n')
    opposite.chmod(0o755)
    monkeypatch.setenv('PATH', str(folder))
    monkeypatch.delenv('JARVIS_CODEX_PATH', raising=False)
    monkeypatch.delenv('JARVIS_CLAUDE_PATH', raising=False)
    with pytest.raises(dp.Refused, match='Fresh author'):
        pa.prepare(handoff(tmp_path), engine, tmp_path / 'out', session='origin')
    [receipt] = (tmp_path / 'out').glob('author/*/author-receipt.json')
    value = json.loads(receipt.read_text())
    assert value['engine'] == engine and value['status'] == 'failed'
    assert 'No such file' in value['error']


def test_invalid_generated_content_is_refused_before_delivery(only_engine, tmp_path, monkeypatch):
    monkeypatch.setenv('AUTHOR_TEST_CONTENT', '{"title":"Missing explanation"}')
    monkeypatch.setattr(dp, 'post', lambda *args, **kwargs: pytest.fail('invalid content delivered'))
    assert mp.main(['--handoff', str(handoff(tmp_path)), '--engine', only_engine,
                    '--session', 'origin', '--out', str(tmp_path / 'out')]) == 1


@pytest.mark.asyncio
async def test_author_timeout_closes_session_and_preserves_failure(tmp_path, monkeypatch):
    class Session:
        closed = False
        async def open(self, instructions): pass
        async def send(self, text): pass
        async def close(self, timeout): self.closed = True
    child = Session()
    class Provider:
        name = 'openai'
        def session(self, *args): return child
    monkeypatch.setattr(pa.brain_provider, 'provider', lambda name: Provider())
    with pytest.raises(dp.Refused, match='timed out'):
        await pa.author(handoff(tmp_path), 'codex', tmp_path / 'out', session='origin', timeout=.01)
    assert child.closed
    [receipt] = (tmp_path / 'out').glob('author/*/author-receipt.json')
    assert json.loads(receipt.read_text())['status'] == 'failed'
