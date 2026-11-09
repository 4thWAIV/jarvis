"""Direct-utterance classification never receives package material or tools."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from jarvis.brain import BrainConfig
from jarvis.brain_provider import TextDelta, TurnEnded
from jarvis.package_authority import PackageAuthority
from jarvis.package_content_contract import InspectionError, PackageRef

@pytest.mark.asyncio
async def test_one_fresh_provider_session_sees_only_the_direct_utterance_and_host_ids(tmp_path):
    seen = []
    response = {'operation': 'prepare_followup', 'target': None, 'recipient': 'sender',
                'decision': None, 'answer': None, 'ambiguous': False}
    class Session:
        def __init__(self, config, event):
            self.config, self.event = config, event
        async def open(self, instructions):
            assert self.config.classification_only and self.config.mcp_config is None
            assert not self.config.connections
            assert not (self.config.home / 'CLAUDE.md').exists()
        async def send(self, text):
            seen.append(json.loads(text))
            self.event(self, TextDelta(json.dumps(response)))
            self.event(self, TurnEnded(0, 0))
        def kill(self): pass
        async def close(self, timeout): pass
    provider = SimpleNamespace(session=lambda config, event, exited: Session(config, event))
    authority = PackageAuthority(config=lambda: BrainConfig(home=tmp_path), provider=lambda name: provider)
    await authority._prepare()
    intent = await authority.classify('Ask the sender about the cost.', {'package_id': 'known-package', 'client_id': 'known-client'})
    assert intent.operation == 'prepare_followup'
    assert seen == [{'utterance': 'Ask the sender about the cost.', 'host': {'package_id': 'known-package', 'client_id': 'known-client'}}]
    await authority.close()

@pytest.mark.asyncio
async def test_classification_cannot_introduce_answer_or_recipient_words(tmp_path):
    from jarvis.package_authority import Intent
    future = asyncio.get_running_loop().create_future()
    future.set_result(json.dumps({'operation': 'answer_decision', 'target': None,
        'recipient': None, 'decision': 'malicious-decision', 'answer': 'approve', 'ambiguous': False}))
    class Session:
        async def send(self, text): pass
        def kill(self): pass
        async def close(self, timeout): pass
    authority = PackageAuthority(enabled=lambda: False)
    folder = tmp_path / 'private'
    folder.mkdir()
    authority._ready = (Session(), future, folder)
    with pytest.raises(InspectionError) as error:
        await authority.classify('Is this proposal sensible?', {'package_id': 'known'})
    assert error.value.failure.code == 'ambiguous_intent'
    assert not authority._scopes


def test_end_of_turn_revokes_the_exact_package_operation_and_recipient():
    authority = PackageAuthority(enabled=lambda: False)
    ref = PackageRef(package_id='package', inbox_id='pkg-exact')
    scope = authority.mint(user_turn_id='direct-turn', package=ref, operation='prepare_followup', recipient='recipient')
    authority.require(scope, package=ref, operation='prepare_followup', recipient='recipient')
    for package, operation, recipient in [(PackageRef(package_id='other'), 'prepare_followup', 'recipient'),
        (ref, 'execute_package', 'recipient'), (ref, 'prepare_followup', 'different-recipient')]:
        with pytest.raises(InspectionError):
            authority.require(scope, package=package, operation=operation, recipient=recipient)
    authority.end_turn()
    with pytest.raises(InspectionError):
        authority.require(scope, package=ref, operation='prepare_followup', recipient='recipient')
