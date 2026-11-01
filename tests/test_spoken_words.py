"""Words as they are spoken: one splitter and one fold for every matcher.

The four splitters this replaced were ASCII only — "sí" came out as "s",
"espérate" as "esp" and "rate" — so a Spanish yes, no or cancel could never
match. On ASCII text the new splitter must give exactly what they gave, which
is what keeps English behaviour unchanged.
"""

import re

import pytest

from jarvis import agent_directory, jarvis_memory, server, speech, spoken_words

ASCII_SAMPLES = [
    "", "Start fresh.", "Jarvis, start fresh!", "yes, but not now",
    "Don't do that", "I'm sorry, say that again", "o'clock 'tis rock'n'roll '",
    "deploy v2.3 to prod_2 now", "chitauri-api_v2.final, hammer", "  MIXED case  42 ",
    "snake_case_name", "a--b__c..d", "what's running right now?",
]


@pytest.mark.parametrize("text", ASCII_SAMPLES)
def test_on_ascii_text_each_splitter_gives_what_the_old_one_gave(text):
    low = text.lower()
    assert spoken_words.words(text, digits=False) == re.findall(r"[a-z]+", low)
    assert spoken_words.words(text) == [w for w in re.split(r"[^a-z0-9]+", low) if w]
    assert spoken_words.words(text, apostrophes=True) == re.findall(r"[a-z0-9']+", low)
    assert spoken_words.folded_words(text, apostrophes=True) == re.findall(r"[a-z0-9']+", low)


@pytest.mark.parametrize("text", ASCII_SAMPLES)
def test_the_four_replaced_call_sites_are_unchanged_on_ascii(text):
    low = text.lower()
    assert server._action_words(text) == re.findall(r"[a-z]+", low)
    assert speech._tokens(text) == re.findall(r"[a-z0-9']+", low)
    key = agent_directory.spoken_key(text)
    assert agent_directory._words(text) == {w for w in re.split(r"[^a-z0-9]+", key) if w}
    stripped = re.sub(r"['’‘\"“”]", "", low)
    assert jarvis_memory._normalize_title(text) == \
        re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")


def test_accented_letters_stay_in_their_words():
    assert spoken_words.words("mañana, señor; sí, espérate") == \
        ["mañana", "señor", "sí", "espérate"]
    assert speech._tokens("mañana, señor; sí, espérate") == \
        ["manana", "senor", "si", "esperate"]
    assert server._action_words("¡Sí! De acuerdo.") == ["si", "de", "acuerdo"]
    assert agent_directory._words("José-Núñez") == {"jose", "nunez"}


def test_fold_takes_accents_off_and_nothing_else():
    assert spoken_words.fold("Espérate, señor: después, todavía, ¿cómo?") == \
        "Esperate, senor: despues, todavia, ¿como?"
    assert spoken_words.fold("plain ascii, 42!") == "plain ascii, 42!"


def test_english_is_the_active_language_until_the_setting_exists():
    assert spoken_words.active_language() == spoken_words.ENGLISH
    assert spoken_words.vocabulary() is spoken_words.VOCABULARIES["en"]


def test_the_spanish_vocabulary_is_the_approved_one():
    """The words Tim approved (Latin American Spanish), folded."""
    es = spoken_words.vocabulary(spoken_words.SPANISH)
    assert es.yes_words == {"si", "claro", "adelante", "hazlo", "dale"}
    assert es.yes_phrases == ("de acuerdo",)
    assert es.no_words == {"no", "espera", "luego", "despues", "nunca", "negativo",
                           "todavia"}
    assert es.cancel_phrases == {"espera", "esperate", "cancela", "alto", "detente"}
    assert "para" not in es.cancel_phrases
    assert es.replay_phrases == {("repite",), ("otra", "vez")}
    assert es.clear_command == "empezar de cero"


def test_the_spanish_vocabulary_follows_the_active_language(spanish):
    assert spoken_words.vocabulary() is spoken_words.VOCABULARIES["es"]


def test_every_list_is_stored_as_matchers_compare_it():
    """Folded and split the matchers' way, so no entry is one that no
    utterance could produce."""
    for language, v in spoken_words.VOCABULARIES.items():
        for entry in (*v.yes_words, *v.no_words, *v.replay_filler):
            assert spoken_words.folded_words(entry, apostrophes=True) == [entry], (language, entry)
        for phrase in (*v.yes_phrases, *v.cancel_phrases, v.clear_command):
            assert " ".join(spoken_words.folded_words(phrase, apostrophes=True)) == phrase
        for phrase in v.replay_phrases:
            assert all(spoken_words.folded_words(w, apostrophes=True) == [w]
                       for w in phrase), (language, phrase)
