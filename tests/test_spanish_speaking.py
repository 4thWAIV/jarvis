"""Spanish build step 4, spanish-speaking: the Fish language field, Spanish
abbreviations and banned phrases, and the brain's language rule. English
behaviour stays as it was; the existing tests pin that, and the English cases
here pin the new code's English path."""

import asyncio
import importlib
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis import spoken_words
from jarvis.speech import SentenceSplitter
from tests.test_system_prompt_header import assert_prompt_is_jarviss_own

REPO = Path(__file__).parent.parent


def _spanish(monkeypatch):
    monkeypatch.setattr(spoken_words, "active_language", lambda: spoken_words.SPANISH)


# --- the one function ------------------------------------------------------

def test_english_is_in_force_until_the_setting_exists():
    assert spoken_words.active_language() == spoken_words.ENGLISH


# --- Fish ------------------------------------------------------------------

def test_english_sends_no_language_field():
    from jarvis import tts
    assert "language" not in tts.request_body("Good evening, sir.", "v")


def test_spanish_sends_latin_american_spanish():
    from jarvis import tts
    assert tts.request_body("Buenas noches, señor.", "v",
                            language="es")["language"] == "es-MX"


def test_every_language_has_a_fish_entry():
    from jarvis import tts
    assert set(tts.FISH_LANGUAGES) == set((spoken_words.ENGLISH, spoken_words.SPANISH))


@pytest.mark.asyncio
async def test_synthesis_carries_the_language_to_fish():
    from jarvis import tts
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await tts.synthesize_chunk("Hola, señor.", api_key="k", voice_id="v",
                                   client=c, language="es")
    assert seen["body"]["language"] == "es-MX"


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import data_paths
    importlib.reload(data_paths)
    from jarvis import run_store
    importlib.reload(run_store)
    from jarvis import server as server_module
    importlib.reload(server_module)
    run_store.init_db()
    return server_module


def _capture_synth(server, monkeypatch):
    calls = []

    async def synth(text, **kw):
        calls.append(kw)
        return None

    monkeypatch.setattr(server.tts, "synthesize_chunk", synth)
    monkeypatch.setattr(server, "FISH_API_KEY", "key")
    return calls


@pytest.mark.parametrize("language", (spoken_words.ENGLISH, spoken_words.SPANISH))
def test_speech_is_synthesized_in_the_active_language(server, monkeypatch, language):
    calls = _capture_synth(server, monkeypatch)
    monkeypatch.setattr(spoken_words, "active_language", lambda: language)
    asyncio.run(server._synth_for_speech("Good evening."))
    assert calls[0]["language"] == language


def test_the_audio_test_goes_through_the_same_synthesis(server, monkeypatch):
    calls = _capture_synth(server, monkeypatch)
    _spanish(monkeypatch)
    asyncio.run(server.synthesize_speech("Testing audio, sir."))
    assert calls and calls[0]["language"] == "es"


def test_no_fish_request_is_built_anywhere_but_tts():
    for path in (REPO / "jarvis").glob("*.py"):
        if path.name != "tts.py":
            assert "api.fish.audio" not in path.read_text(), path.name


# --- abbreviations ---------------------------------------------------------

def _split(text: str) -> list[str]:
    s = SentenceSplitter()
    out = s.feed(text)
    tail = s.flush()
    return out + ([tail] if tail else [])


@pytest.mark.parametrize("abbr", ["Sra.", "Srta.", "Dra.", "Ud.", "Uds.",
                                  "pág.", "núm.", "aprox."])
def test_a_spanish_abbreviation_does_not_end_the_sentence(abbr):
    assert _split(f"Mire {abbr} cinco, señor. Listo.") == [
        f"Mire {abbr} cinco, señor.", "Listo."]


def test_the_spaced_time_of_day_is_one_piece():
    assert _split("Son las 3 p. m. y llueve, señor. Nada más.") == [
        "Son las 3 p. m. y llueve, señor.", "Nada más."]
    assert _split("Desde las 9 a. m. todo va bien. Bien.") == [
        "Desde las 9 a. m. todo va bien.", "Bien."]


def test_the_spaced_time_of_day_is_held_while_it_streams_in():
    s = SentenceSplitter()
    got = []
    for delta in ["Son las 3 p", ". ", "m", ". y llueve, señor. ", "Ya."]:
        got += s.feed(delta)
    got.append(s.flush())
    assert got == ["Son las 3 p. m. y llueve, señor.", "Ya."]


def test_an_english_sentence_ending_in_a_letter_still_ends():
    assert _split("We go with plan a. Then we wait.") == [
        "We go with plan a.", "Then we wait."]
    assert _split("The 3 p.m. call moved. Fine.") == [
        "The 3 p.m. call moved.", "Fine."]


# --- banned phrases --------------------------------------------------------

@pytest.mark.parametrize("said, heard", [
    ("Por supuesto, señor, ya está.", "señor, ya está."),
    ("¡Claro que sí! Hecho, señor.", "Hecho, señor."),
    ("Con mucho gusto, lo envío.", "lo envío."),
])
def test_spanish_banned_phrases_are_stripped(server, said, heard):
    assert server.strip_markdown_for_tts(said) == heard


def test_english_banned_phrases_are_stripped_as_before(server):
    assert server.strip_markdown_for_tts("Of course, sir, it is done.") == \
        "sir, it is done."


# --- the brain -------------------------------------------------------------

def _brain(tmp_path):
    from jarvis import brain
    return brain.Brain(brain.BrainConfig(home=tmp_path))


def test_the_english_launch_prompt_has_no_language_line(tmp_path):
    prompt = _brain(tmp_path).launch_prompt()
    assert "Spanish" not in prompt and "señor" not in prompt


def test_the_spanish_launch_prompt_carries_the_rule(tmp_path, monkeypatch):
    _spanish(monkeypatch)
    prompt = _brain(tmp_path).launch_prompt()
    assert "answer in Latin American Spanish" in prompt
    assert "señor" in prompt
    assert "Tool results reach you in English" in prompt
    assert "search them in both Spanish and English" in prompt
    assert_prompt_is_jarviss_own(prompt)


def test_the_persona_says_the_same():
    persona = (REPO / "jarvis" / "jarvis_home" / "CLAUDE.md").read_text()
    section = persona.split("## Language", 1)[1].split("\n## ", 1)[0]
    assert '"señor"' in section
    assert "Tool results reach you in English" in section
    assert "in both Spanish and English" in section
    assert "never translate a capture" in section
