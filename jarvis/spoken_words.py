"""Words as they are spoken, in each language JARVIS hears.

Everything that matches what the user said against fixed words — the yes and
no that clear or compact the conversation, the cancel words, "say that
again", the clear command, echo rejection, finding an agent by name, memory
search — splits and folds text here. The splitter keeps accented letters, so
a Spanish "sí" is the word "si" and not the letter "s", and "espérate" is one
word and not "esp" and "rate".

`fold` takes accents off ("después" -> "despues"), because the recogniser
may or may not write them; every list below is folded before it is compared,
so it can be written as the words are spelt. Plain ASCII text comes out of
both exactly as the old `[a-z0-9]` splitters gave it.

Which language's words count is `active_language()`: the language chosen in
Settings, which language_setting.py keeps and sets here; English until another
is chosen.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

ENGLISH = "en"
SPANISH = "es"


_active = ENGLISH


def active_language() -> str:
    """The language the user speaks to JARVIS in: the one place it is decided.

    English until another is chosen; language_setting.py sets it, through
    `set_active_language` and nowhere else."""
    return _active


def set_active_language(language: str) -> None:
    """Make `language` the one in force (language_setting.py's alone)."""
    global _active
    if language not in VOCABULARIES:
        raise ValueError(f"no words for language {language!r}")
    _active = language


# How he is addressed, by the Honorific setting (Settings, You), in each
# language: the one table. Only these fixed words ever reach the brain's
# launch prompt: any other value in .env counts as the default, "sir", which
# in Spanish is "señor" (and "ma'am" is "señora"), since "sir" is never said
# there.
_ADDRESS = {
    "sir": {ENGLISH: "sir", SPANISH: "señor"},
    "ma'am": {ENGLISH: "ma'am", SPANISH: "señora"},
    "señor": {ENGLISH: "señor", SPANISH: "señor"},
}
_DEFAULT_HONORIFIC = "sir"
HONORIFICS = (*_ADDRESS, "none")


def default_address(language: str) -> str:
    return _ADDRESS[_DEFAULT_HONORIFIC][language]


def address(language: str, honorific: str | None) -> str | None:
    """The word he is addressed by in `language` under the Honorific setting,
    or None for no form of address."""
    chosen = (honorific or "").strip().lower()
    if chosen == "none":
        return None
    return _ADDRESS.get(chosen, _ADDRESS[_DEFAULT_HONORIFIC])[language]


# Letters, digits, any alphabet; `_` is a word character to `\w` and is not
# a letter, so it is cut out.
_LETTERS = re.compile(r"[^\W\d_]+")
_LETTERS_DIGITS = re.compile(r"[^\W_]+")
_LETTERS_DIGITS_APOSTROPHES = re.compile(r"(?:[^\W_]|')+")


def words(text: str, *, digits: bool = True, apostrophes: bool = False) -> list[str]:
    """The lowercased words of `text`, accented letters kept.

    `digits=False` gives letters only; `apostrophes=True` keeps "don't" and
    "i'm" whole. On ASCII text these are `[a-z]+`, `[a-z0-9]+` and
    `[a-z0-9']+` exactly."""
    if apostrophes:
        pattern = _LETTERS_DIGITS_APOSTROPHES
    else:
        pattern = _LETTERS_DIGITS if digits else _LETTERS
    return pattern.findall((text or "").lower())


def fold(text: str) -> str:
    """`text` with its accents taken off: "Espérate, señor" -> "Esperate, senor"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def folded_words(text: str, *, digits: bool = True, apostrophes: bool = False) -> list[str]:
    """`words` of the folded text: the form every matcher compares."""
    return words(fold(text), digits=digits, apostrophes=apostrophes)


def _key(phrase: str) -> str:
    return " ".join(folded_words(phrase, apostrophes=True))


@dataclass(frozen=True)
class Vocabulary:
    """One language's fixed words, every entry folded.

    `yes_words` / `yes_phrases` / `no_words`: the answer to "clear the whole
    conversation?" and "shall I compact it?" (`server._rotation_answer`).
    `cancel_phrases`: a whole utterance that cancels and cuts him off.
    `replay_phrases` / `replay_filler`: a whole utterance, filler trimmed
    from its ends only, that replays his last answer.
    `clear_command`: the one whole utterance that asks to clear.
    `switch_command`: the one whole utterance that switches JARVIS to
    `switch_to`, the other language (language_setting.py)."""
    yes_words: frozenset
    yes_phrases: tuple
    no_words: frozenset
    cancel_phrases: frozenset
    replay_phrases: frozenset
    replay_filler: frozenset
    clear_command: str
    switch_command: str
    switch_to: str


def _vocabulary(*, yes_words, yes_phrases, no_words, cancel_phrases,
                replay_phrases, replay_filler, clear_command,
                switch_command, switch_to) -> Vocabulary:
    return Vocabulary(
        yes_words=frozenset(_key(w) for w in yes_words),
        yes_phrases=tuple(_key(p) for p in yes_phrases),
        no_words=frozenset(_key(w) for w in no_words),
        cancel_phrases=frozenset(_key(p) for p in cancel_phrases),
        replay_phrases=frozenset(tuple(_key(p).split()) for p in replay_phrases),
        replay_filler=frozenset(_key(w) for w in replay_filler),
        clear_command=_key(clear_command),
        switch_command=_key(switch_command),
        switch_to=switch_to,
    )


# "Say that again" must be replayed, not answered — regenerating an answer
# costs a brain turn AND comes back as different words, which does not even
# answer what the user asked. So it is matched narrowly: the whole utterance,
# with filler words trimmed off BOTH ends only (never the middle, so a filler
# word embedded in a real sentence cannot collapse it into a trigger).
# Deliberately NOT matched: anything that asks a real question, however
# related — "what did you mean by that", "what do you mean", "can you
# explain that again", "say more about that". Those go to the brain like any
# other turn.
#
# A yes must be short and unhedged (`server._rotation_answer`); any word of
# refusal or delay in the same breath makes it a no. The splitter cuts
# "don't" into "don" and "t" for those words, hence "don".
_ENGLISH = _vocabulary(
    yes_words=("yes", "yeah", "yep", "yup", "sure", "ok", "okay", "please",
               "certainly", "absolutely", "affirmative", "proceed", "compact",
               "rotate"),
    yes_phrases=("go ahead", "do it", "go for it", "sounds good"),
    no_words=("no", "nope", "nah", "not", "don", "dont", "wait", "later",
              "hold", "stop", "never", "negative"),
    cancel_phrases=("wait", "no", "stop", "cancel", "hold on"),
    replay_phrases=("say that again", "say it again", "repeat", "repeat that",
                    "repeat it", "what did you say", "what was that",
                    "come again", "one more time", "pardon", "pardon me"),
    replay_filler=("sorry", "jarvis", "please", "um", "uh", "i'm"),
    clear_command="start fresh",
    switch_command="switch to spanish",
    switch_to=SPANISH,
)

# Latin American Spanish (es-MX), as approved: "dale", not Spain's "vale".
# "si" without its accent counts, since the recogniser may drop it. "para" is
# not a cancel word: it is an everyday preposition, and these words also cut
# him off mid-sentence. "¿cómo?", "¿qué?", "¿mande?" and "perdón" are common
# speech, not requests to repeat, so they go to the brain.
_SPANISH = _vocabulary(
    yes_words=("sí", "si", "claro", "adelante", "hazlo", "dale"),
    yes_phrases=("de acuerdo",),
    no_words=("no", "espera", "luego", "después", "nunca", "negativo",
              "todavía"),
    cancel_phrases=("espera", "espérate", "cancela", "alto", "detente"),
    replay_phrases=("repite", "otra vez"),
    replay_filler=("jarvis",),
    clear_command="empezar de cero",
    # Heard only through the Spanish model, so the way back is Spanish words.
    switch_command="cambia a inglés",
    switch_to=ENGLISH,
)

VOCABULARIES = {ENGLISH: _ENGLISH, SPANISH: _SPANISH}


def vocabulary(language: str | None = None) -> Vocabulary:
    """The fixed words of `language`, or of the active language."""
    return VOCABULARIES[language or active_language()]
