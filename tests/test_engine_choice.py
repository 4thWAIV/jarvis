"""Which engine and model each kind of agent JARVIS starts goes on
(engine_choice.py): his setting per kind, "same as requester", "least token
stress", and the override for a brain in token stress, which is the brain
board's plus/minus (usage_store.week's `ahead`)."""
import time

import pytest

from jarvis import brains_config, engine_choice, usage_store


def _week(service: str, used: float) -> None:
    """A seven-day reading half way through its week: expected 50%, so
    `ahead` is 50 - used."""
    usage_store.record({"status": "allowed", "unifiedWindows": {"seven_day": {
        "utilization": used / 100.0, "resetsAt": time.time() + 3.5 * 24 * 3600}}},
        provider={"service": service, "subscription": "max"})


@pytest.fixture
def both():
    brains_config.add("openai")


def test_with_nothing_set_it_is_what_jarvis_did_before():
    assert engine_choice.choose("run") == engine_choice.Choice("claude", None, "fixed")
    assert engine_choice.choose("package_author", requester="codex") == \
        engine_choice.Choice("codex", None, "requester")
    assert engine_choice.choose("package_author").engine == "claude"


def test_a_fixed_brain_and_model(both):
    engine_choice.set_kind("build", "fixed", "codex", "gpt-test", models=["gpt-test"])
    assert engine_choice.choose("build") == engine_choice.Choice("codex", "gpt-test", "fixed")
    # Each kind is its own setting.
    assert engine_choice.choose("run").engine == "claude"


def test_what_he_names_for_one_agent_is_kept(both):
    engine_choice.set_kind("run", "fixed", "codex", None)
    engine_choice.set_override(True)
    _week("anthropic", 90)
    assert engine_choice.choose("run", engine="claude", model="opus") == \
        engine_choice.Choice("claude", "opus", "asked")


def test_token_stress_is_the_board_s_plus_minus(both):
    _week("anthropic", 70)                 # 20 points behind its pace
    _week("openai", 30)                    # 20 ahead
    assert engine_choice.stress("claude") == -20.0
    assert engine_choice.stressed("claude") and not engine_choice.stressed("codex")
    assert engine_choice.least_stressed() == "codex"


def test_a_brain_not_measured_is_neither_stressed_nor_least():
    assert engine_choice.stress("claude") is None
    assert not engine_choice.stressed("claude")
    assert engine_choice.least_stressed() is None


def test_least_token_stress(both):
    engine_choice.set_kind("agent_package", "least_stress")
    _week("anthropic", 30)
    _week("openai", 45)
    assert engine_choice.choose("agent_package") == \
        engine_choice.Choice("claude", None, "least_stress")


def test_the_override_moves_a_stressed_choice_to_the_least_stressed(both):
    _week("anthropic", 70)
    _week("openai", 30)
    assert engine_choice.choose("run").engine == "claude"          # off: kept
    engine_choice.set_override(True)
    assert engine_choice.choose("run") == engine_choice.Choice("codex", None, "override")
    assert engine_choice.choose("package_author", requester="claude").engine == "codex"
    # Not stressed: nothing moves.
    _week("anthropic", 40)
    assert engine_choice.choose("run").engine == "claude"


def test_refusals_say_what_was_wrong():
    with pytest.raises(engine_choice.Refused) as e:
        engine_choice.set_kind("run", "fixed", "codex")       # Codex not added
    assert e.value.code == "not-added"
    with pytest.raises(engine_choice.Refused) as e:
        engine_choice.set_kind("lunch", "requester")
    assert e.value.code == "unknown-kind"
    with pytest.raises(engine_choice.Refused) as e:
        engine_choice.set_kind("run", "fixed", "claude", "gpt-9", models=["sonnet"])
    assert e.value.code == "no-such-model"


def test_an_unreadable_file_is_the_defaults():
    engine_choice._path().write_text("{not json")
    assert engine_choice.load()["kinds"] == engine_choice.DEFAULTS
