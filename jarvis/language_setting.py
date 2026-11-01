"""
language_setting.py — the language JARVIS hears and speaks: English or
Spanish, chosen in Settings (Voice), by the brain's `set_language`, or by one
exact spoken phrase (`spoken_words.Vocabulary.switch_command`: "switch to
Spanish", and back "cambia a inglés").

The choice is kept in JARVIS's own store (`data_paths.language_path()`), not
.env, and put in force with `spoken_words.set_active_language`, which is what
everything that depends on it reads: the word lists, Fish's language and the
brain's launch prompt. `change` first loads the language's speech model
(`speech_models.switch`: a few seconds, one model in memory) and only once it
has loaded saves the choice and puts it in force, so a model that is not
installed, or that fails to load, changes nothing (after a failed load the
language in force's model is loaded again). Spanish cannot be chosen
until its model is installed.

At startup `load` puts the saved choice in force, or English for this run
when the saved language's model is not installed (the saved choice is kept).

`change` is what the page's route, the brain's tool and the spoken phrase all
call. The page and the phrase are then confirmed aloud in the new language
(`choose`), through the announcer the server gives `mount`, which also tells
the brain at its next turn. The brain's tool is not: the brain says it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Callable, Optional

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from jarvis import brain, data_paths, speech_models, spoken_words, transcriber
from jarvis.speech_models import Refused
from jarvis.spoken_words import ENGLISH, SPANISH

log = logging.getLogger("jarvis.language")

# The languages' names, in English for the brain and the record.
NAMES = {ENGLISH: "English", SPANISH: "Spanish"}

# What JARVIS says once the language has changed, in the new language.
CONFIRMATIONS = {
    ENGLISH: "Speaking English now{address}.",
    SPANISH: "Ahora hablo en español{address}.",
}

# (the line said, the note the brain gets at its next turn)
Announce = Callable[[str, str], None]
_announce: Optional[Announce] = None
_lock = asyncio.Lock()


# ── the stored choice ─────────────────────────────────────────────────────

def saved() -> str:
    """The language last chosen, or English: nothing chosen yet, or a store
    that cannot be read."""
    path = data_paths.language_path()
    try:
        value = json.loads(path.read_text()).get("language")
    except FileNotFoundError:
        return ENGLISH
    except (OSError, ValueError, AttributeError) as e:
        log.warning("the saved language could not be read (%s); English", type(e).__name__)
        return ENGLISH
    return value if value in spoken_words.VOCABULARIES else ENGLISH


def _save(language: str) -> None:
    data_paths._write_atomically(data_paths.language_path(),
                                 json.dumps({"language": language}) + "\n")


def startup_model() -> transcriber.SpeechModel:
    """The speech model JARVIS starts with: the saved language's, or
    English's when that one is not installed."""
    model = transcriber.find_model(saved())
    if model.language != ENGLISH and transcriber.model_problem(model=model) is not None:
        log.warning("%s was chosen, but its speech model is not installed; "
                    "English for this run", NAMES[model.language])
        return transcriber.find_model(ENGLISH)
    return model


def load() -> transcriber.SpeechModel:
    """Put the language JARVIS starts with in force (at startup) and return
    its speech model, for speech input to load."""
    model = startup_model()
    spoken_words.set_active_language(model.language)
    return model


# ── what the page, the brain and the phrase share ────────────────────────

def status() -> dict:
    """The language in force, and speech input's models (speech_models.models)."""
    return {"language": spoken_words.active_language(), "speech": speech_models.models()}


def _language(language: str) -> str:
    language = str(language or "").strip().lower()
    if language not in spoken_words.VOCABULARIES:
        raise Refused(404, "JARVIS speaks "
                      + " and ".join(f"{NAMES[k]} ({k})" for k in spoken_words.VOCABULARIES)
                      + " only.")
    return language


async def change(language: str) -> bool:
    """Make `language` the one JARVIS hears and speaks: its speech model is
    loaded, then the choice is saved and put in force. False when it already
    was. Raises Refused, having changed nothing."""
    language = _language(language)
    async with _lock:
        service_language = speech_models.models()["language"]
        if language == spoken_words.active_language() and service_language == language:
            return False
        try:
            await speech_models.switch(language)
        except Refused:
            await _hear_again(spoken_words.active_language())
            raise
        _save(language)
        spoken_words.set_active_language(language)
    log.info("language changed to %s", NAMES[language])
    return True


async def _hear_again(language: str) -> None:
    """After a model that failed to load, speech input has none: load the
    language still in force again, so a failed change leaves him heard as
    before. Nothing to do when the old model was never let go."""
    if speech_models.models()["language"] == language:
        return
    try:
        await speech_models.switch(language)
    except Refused as refused:
        log.warning("speech input could not go back to %s: %s",
                    NAMES[language], refused.message)


def honorific() -> str:
    """The Honorific setting (Settings, You), as saved in .env."""
    return os.getenv("HONORIFIC", "")


def confirmation(language: str) -> str:
    """What JARVIS says in `language` once it is in force."""
    said = spoken_words.address(language, honorific())
    return CONFIRMATIONS[language].format(address=f", {said}" if said else "")


def brain_rule(language: str) -> str:
    """What the brain is told when the language has changed under it."""
    name = NAMES[language]
    return (f"Answer in {name} from now on, whatever language earlier turns were "
            f"in.{brain.language_rule(language, honorific(), explicit=True)}")


async def choose(language: str) -> bool:
    """`change`, then say so in the new language (the page and the phrase)."""
    changed = await change(language)
    if changed and _announce is not None:
        name = NAMES[spoken_words.active_language()]
        _announce(confirmation(spoken_words.active_language()),
                  f"Since your last turn he changed JARVIS's language to {name}, and "
                  f"you told him so in {name}. {brain_rule(spoken_words.active_language())}")
    return changed


# ── the page's routes ────────────────────────────────────────────────────

async def api_status():
    return status()


async def api_choose(language: str = Body(embed=True)):
    try:
        await choose(language)
    except Refused as refused:
        return JSONResponse(status_code=refused.status, content={"error": refused.message})
    return status()


def mount(app: FastAPI, announce: Optional[Announce] = None) -> None:
    global _announce
    _announce = announce
    app.add_api_route("/api/settings/language", api_status, methods=["GET"])
    app.add_api_route("/api/settings/language", api_choose, methods=["POST"])
