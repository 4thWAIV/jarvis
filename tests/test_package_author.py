"""Fresh same-engine authorship is mandatory before factory delivery."""
import base64
import json
from pathlib import Path
import sys

import pytest
import yaml

from jarvis import package_author as pa, make_package as mp, decision_package as dp
from tests.test_make_package import presentation
from tests.deck_fixtures import presentation_of
from tests.test_decision_package import _Delivery
from tests.settings_fixtures import default_setting
from tests.test_package_jobs import ANSWERS, handoff_file

FIXTURES = Path(__file__).parent / "fixtures"


def handoff(tmp_path):
    return handoff_file(tmp_path / 'handoff.json', 'Approve the result?', work='origin-work')


def presentation_yaml(folder: Path) -> str:
    """The test presentation as the author writes it, presentation.yaml."""
    return yaml.safe_dump(presentation_of(presentation(), folder), sort_keys=False,
                          allow_unicode=True)


@pytest.fixture(params=['claude', 'codex'])
def only_engine(request, tmp_path, monkeypatch):
    """Execute the real adapter with only its engine available on PATH."""
    engine = request.param
    folder = tmp_path / 'bin'
    folder.mkdir()
    original = (FIXTURES / ('fake_brain.py' if engine == 'claude' else 'fake_codex.py')).read_text()
    start = 'def reply(text):' if engine == 'claude' else 'def turn(rid, text):'
    session = 'SESSION_ID' if engine == 'claude' else '"thread-1"'
    resumed = "'--resume' in sys.argv" if engine == 'claude' else 'RESUMED'
    # The author writes its source in its first turn: an agent package
    # or a working tool's page (package/), or, to be refused, a deck.
    action = f'''
    with open('received-prompt.txt', 'a') as output:
        output.write(text + '\\n---turn---\\n')
    mode = os.environ.get('AUTHOR_TEST_MODE', 'source')
    # ask:N asks its sender questions on its first N turns, then writes its
    # source; every turn is logged with whether its session was resumed.
    _asked = int(open('asked-count').read()) if os.path.exists('asked-count') else 0
    with open('turns.log', 'a') as output:
        output.write(json.dumps({{'text': text, 'resumed': {resumed}}}) + '\\n')
    _asking = mode.startswith('ask:')
    if _asking and _asked < int(mode[4:]):
        with open('asked-count', 'w') as output:
            output.write(str(_asked + 1))
        with open('author-blocked.json', 'w') as output:
            json.dump({{'questions': [
                {{'id': 'release-date', 'question': 'When did the release go out?',
                  'why': 'The conclusion depends on it.'}},
                {{'id': 'owner', 'question': 'Who owns the fix?', 'why': ''}}]}}, output)
    elif mode in ('source', 'page') or _asking:
        os.makedirs('package', exist_ok=True)
        with open('package/package.json', 'w') as output:
            output.write(os.environ['AUTHOR_TEST_CONTENT'])
        if mode == 'page':
            with open('package/index.html', 'w') as output:
                output.write('<p>Approve the result?</p>')
            with open('package/explanation.md', 'w') as output:
                output.write('# The tool\\nA page that shows the result.\\n')
        else:
            with open('package/BRIEF.md', 'w') as output:
                output.write('Count the words.\\n')
    elif mode == 'deck':
        with open('presentation.yaml', 'w') as output:
            output.write('title: A deck\\n')
    elif mode == 'blocked':
        with open('author-blocked.md', 'w') as output:
            output.write('Missing release receipt.')
'''
    fake = folder / engine
    fake.write_text(original.replace('#!/usr/bin/env python3', '#!' + sys.executable).replace(start, start + action))
    fake.chmod(0o755)
    monkeypatch.setenv('PATH', str(folder))
    default_setting("claude_path")
    default_setting("codex_path")
    monkeypatch.setenv('AUTHOR_TEST_CONTENT', json.dumps({'purpose': 'Count the words.'}))
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
    source = handoff(tmp_path)
    out = tmp_path / 'out'
    assert mp.main(['--handoff', str(source), '--engine', only_engine,
                    '--session', 'origin-session', '--work', 'origin-work', '--workflow', 'origin-workflow',
                    '--out', str(out), '--deliver-to', 'http://127.0.0.1:19999']) == 0
    [(url, signer, _)] = delivery.calls
    assert 'work=origin-work' in url and 'workflow=origin-workflow' in url
    assert signer == origin_key, "the session is the signature's, never a query (round 3)"
    [receipt_path] = out.glob('author/*/author-receipt.json')
    receipt = json.loads(receipt_path.read_text())
    assert receipt['status'] == 'authored' and receipt['engine'] == only_engine
    assert receipt['author_session'] != receipt['origin_session'] == 'origin-session'
    assert receipt['built']['status'] == 'built', 'built once, with no screenshot review'
    prompt = (receipt_path.parent / 'received-prompt.txt').read_text()
    assert str(pa.SKILL) in prompt and str(source) in prompt
    assert pa.session_skill._module("make_package", cert=False) in prompt
    assert 'do not launch another author' in prompt
    # The author has the agent MCP server alone, signed as the sending agent.
    config = json.loads((receipt_path.parent / 'author-mcp.json').read_text())
    [(name, server)] = config['mcpServers'].items()
    assert name == pa.AGENT_MCP and server['args'][1:3] == ['-m', 'jarvis.agent_mcp']
    assert server['env']['JARVIS_AGENT_SESSION'] == 'origin-session'
    first = prompt.split('---turn---')[0]
    assert 'a folder package under package/' in first and pa.BRIEFING_BY_REQUEST in first, \
        "the first turn writes the package itself; a deck is no business of it"
    if only_engine == 'codex':
        log = [json.loads(line) for line in (tmp_path / 'codex.log').read_text().splitlines()]
        start = next(row for row in log if 'argv' in row)
        assert start['codex_home'] == str(tmp_path / 'caller-codex-home')
        assert start['argv'][0] == 'app-server'
        overrides = start['argv'][2::2]
        assert f'{pa.AGENT_MCP}.env.JARVIS_AGENT_SESSION="origin-session"' in \
            ' '.join(overrides)
        assert all(flag == '-c' for flag in start['argv'][1::2])
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
    source = tmp_path / 'presentation.yaml'
    source.write_text(presentation_yaml(tmp_path))
    monkeypatch.setattr(dp, 'post', lambda *a, **kw: pytest.fail('bypassed author'))
    assert mp.main([str(source), '--session', 'origin']) == 1
    assert 'requires --handoff' in capsys.readouterr().out
    assert mp.main([str(source), '--check-only']) == 0


def test_a_deck_the_author_writes_is_refused_as_a_briefing(only_engine, origin_key, tmp_path,
                                                          monkeypatch, capsys):
    """A deck for Tim is a briefing: only the package workflow makes one
    (make_package --start); nothing is delivered."""
    monkeypatch.setenv('AUTHOR_TEST_MODE', 'deck')
    monkeypatch.setattr(dp, 'post', lambda *a, **kw: pytest.fail('a briefing delivered'))
    assert mp.main(['--handoff', str(handoff(tmp_path)), '--engine', only_engine,
                    '--session', 'origin-session', '--out', str(tmp_path / 'out')]) == 1
    assert pa.BRIEFING_BY_REQUEST in capsys.readouterr().out


def test_a_working_tool_s_page_the_author_writes_is_built(only_engine, origin_key, tmp_path,
                                                          monkeypatch, capsys):
    """Hand-made pages stay, for tools: a page the author writes is built
    and delivered, never refused as a briefing."""
    monkeypatch.setenv('AUTHOR_TEST_MODE', 'page')
    monkeypatch.setenv('AUTHOR_TEST_CONTENT', json.dumps(
        {'purpose': 'Show the result.', 'understanding': {'index.html': 'explanation.md'}}))
    delivery = _Delivery()
    monkeypatch.setattr(dp, 'post', delivery)
    assert mp.main(['--handoff', str(handoff(tmp_path)), '--engine', only_engine,
                    '--session', 'origin-session', '--out', str(tmp_path / 'out'),
                    '--deliver-to', 'http://127.0.0.1:19999']) == 0
    out = capsys.readouterr().out
    assert 'Built a page package' in out and pa.BRIEFING_BY_REQUEST not in out
    assert len(delivery.calls) == 1


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
    default_setting("codex_path")
    default_setting("claude_path")
    with pytest.raises(dp.Refused, match='Fresh author'):
        pa.prepare(handoff(tmp_path), engine, tmp_path / 'out', session='origin')
    [receipt] = (tmp_path / 'out').glob('author/*/author-receipt.json')
    value = json.loads(receipt.read_text())
    assert value['engine'] == engine and value['status'] == 'failed'
    assert 'No such file' in value['error']


def test_the_author_goes_on_his_package_author_setting(tmp_path, monkeypatch):
    """Set to Codex, a Claude caller's author is launched on Codex (here
    missing, so it fails saying so) and never on the caller's Claude."""
    from jarvis import brains_config, engine_choice
    brains_config.add("openai")
    engine_choice.set_kind("package_author", "fixed", "codex", "gpt-test")
    folder = tmp_path / 'bin'
    folder.mkdir()
    caller = folder / 'claude'
    caller.write_text('#!' + sys.executable + '\nraise RuntimeError("wrong engine launched")\n')
    caller.chmod(0o755)
    monkeypatch.setenv('PATH', str(folder))
    default_setting("codex_path")
    default_setting("claude_path")
    with pytest.raises(dp.Refused, match='Fresh author'):
        pa.prepare(handoff(tmp_path), 'claude', tmp_path / 'out', session='origin')
    [receipt] = (tmp_path / 'out').glob('author/*/author-receipt.json')
    value = json.loads(receipt.read_text())
    assert (value['engine'], value['requester_engine'], value['chosen_by']) == \
        ('codex', 'claude', 'fixed')
    assert 'No such file' in value['error']


def test_invalid_generated_content_is_refused_before_delivery(only_engine, tmp_path, monkeypatch):
    monkeypatch.setenv('AUTHOR_TEST_CONTENT', '{}')
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


@pytest.mark.asyncio
async def test_a_second_turn_cannot_switch_author_sessions(tmp_path, monkeypatch):
    from jarvis.brain_provider import SessionInfo, TurnEnded
    class Session:
        turns = 0
        async def open(self, instructions):
            pass
        async def send(self, text):
            self.turns += 1
            if self.turns == 1:
                (self.config.home / 'package').mkdir()
                (self.config.home / 'package' / 'BRIEF.md').write_text('Count.\n')
            self.event(self, SessionInfo('fresh-author' if self.turns == 1 else 'replacement', None))
            self.event(self, TurnEnded(0, 0))
        async def close(self, timeout):
            self.closed = True
    child = Session()
    class Provider:
        name = 'openai'
        def session(self, config, event, exited):
            child.config, child.event = config, event
            return child
    monkeypatch.setattr(pa.brain_provider, 'provider', lambda name: Provider())
    async def finish(source, turn):
        await turn('What did the reader miss?')
    with pytest.raises(dp.Refused, match='author session changed'):
        await pa.author(handoff(tmp_path), 'codex', tmp_path / 'out', session='origin', finish=finish)
    assert child.closed


@pytest.mark.parametrize('extra, reason', [
    ({'question': 'Merge?'}, 'no longer takes question'),
    ({'decisions': []}, 'questions and decisions are one list'),
    ({'conclusion': 'Approve it'}, 'may not give conclusion'),
    ({'audience': 'Tim'}, 'the audience is always Tim'),
    ({'shape': 'Slides 1-3 background'}, "never a sender's structure"),
    ({'sender_answers': None}, 'needs sender_answers'),
    ({'evidence': ['PR 171']}, 'evidence entry 1 is an object'),
    ({'notes': 'x'}, 'may not give notes'),
])
def test_the_handoff_is_the_one_form_a_request_takes(tmp_path, monkeypatch, extra, reason):
    """The command reads the same handoff JARVIS takes a request in
    (package_request): one form, refused for the same reasons."""
    path = handoff(tmp_path)
    path.write_text(json.dumps({**json.loads(path.read_text()), **extra}))
    monkeypatch.setattr(pa.brain_provider, 'provider', lambda *a: pytest.fail('engine launched'))
    with pytest.raises(dp.Refused) as refused:
        pa.prepare(path, 'claude', tmp_path / 'out', session='origin')
    assert any(reason in p for p in refused.value.problems), refused.value.problems


def test_a_handoff_reads_as_jarvis_keeps_it(tmp_path):
    path = handoff_file(tmp_path / 'handoff.json', 'Merge PR 171?', work=['w1', 'w2'],
                        purpose='I need his yes to merge.')
    handed = pa.read_handoff(path)
    assert [q['text'] for q in handed['questions']] == ['Merge PR 171?']
    assert handed['work'] == ['w1', 'w2']
    assert handed['from_the_sender']['answers']['plainly']['answer'] == ANSWERS['plainly']
    assert handed['from_the_sender']['purpose'] == 'I need his yes to merge.'
