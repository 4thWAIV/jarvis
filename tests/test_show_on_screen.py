"""What is shown and never said: the `show_on_screen` tool, the backstop that
keeps a "Sources:" block out of speech, and the kind every utterance carries
so the voice page can route it."""

import asyncio
import importlib

import pytest


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis import server as server_module
    importlib.reload(server_module)
    sent = []

    async def emit(msg):
        sent.append(msg)

    monkeypatch.setattr(server_module, "_voice_emit", emit)
    monkeypatch.setattr(server_module, "_spawn", lambda coro: asyncio.get_event_loop()
                        .run_until_complete(coro) if not asyncio.get_event_loop().is_running()
                        else asyncio.ensure_future(coro))
    server_module._sent = sent
    return server_module


SOURCES = {"kind": "sources", "title": "Sources", "items": [
    {"label": "Met Office", "url": "https://www.metoffice.gov.uk/weather"},
    {"label": "A trick", "url": "javascript:alert(1)"},
    {"label": "BBC", "url": "https://www.bbc.co.uk/weather", "note": "five-day"}]}


# --- the tool ----------------------------------------------------------------

def test_a_display_is_sent_to_the_page_and_never_spoken(server):
    assert server.tool_show_on_screen(SOURCES) == "Shown on screen."
    (msg,) = server._sent
    assert msg["type"] == "display" and msg["kind"] == "sources" and msg["title"] == "Sources"
    assert msg["utt"] is None, "outside a turn it goes with no reply"
    assert [i["host"] for i in msg["items"]] == ["www.metoffice.gov.uk", "", "www.bbc.co.uk"]
    assert msg["items"][1]["url"] == "", "only web addresses are kept as links"
    assert msg["items"][2]["note"] == "five-day"


@pytest.mark.parametrize("args, reason", [
    ({"kind": "poem", "title": "x", "items": [{"label": "a"}]}, "kind must be one of"),
    ({"kind": "links", "title": "", "items": [{"label": "a"}]}, "needs a title"),
    ({"kind": "links", "title": "x", "items": []}, "at least one item"),
    ({"kind": "links", "title": "x", "items": [{"label": ""}]}, "none of its items"),
])
def test_a_bad_display_is_refused_with_the_reason(server, args, reason):
    out = server.tool_show_on_screen(args)
    assert out.startswith("Not shown:") and reason in out
    assert server._sent == []


def test_a_display_made_in_a_turn_names_that_turn_s_reply(server, monkeypatch):
    """The page puts it after that reply's words, which may play after it
    arrives."""
    from types import SimpleNamespace
    monkeypatch.setattr(server, "_turn_utt", SimpleNamespace(id=42))
    server.tool_show_on_screen({"kind": "links", "title": "Links", "items": [{"label": "a"}]})
    assert server._sent[-1]["utt"] == 42


def test_nothing_shown_is_kept_on_the_server(server):
    """A shown item lives in the page's conversation, which a reload starts
    afresh, so the server keeps none to send again."""
    from fastapi.testclient import TestClient
    assert TestClient(server.app).get("/api/displays").status_code == 404


def test_the_brain_is_offered_the_tool_and_it_is_neither_acting_nor_tainting(server):
    from jarvis import jarvis_mcp
    assert "show_on_screen" in {t["name"] for t in jarvis_mcp.TOOL_SPECS}
    assert "show_on_screen" not in server.ACTING_TOOLS
    assert "show_on_screen" in server.TAINT_EXEMPT_TOOLS


# --- the backstop -------------------------------------------------------------

def _run(server, deltas):
    spoken, shown = [], []
    screen = server._ScreenOnly(spoken.append, shown.append)
    for d in deltas:
        screen.feed(d)
    screen.finish()
    return "".join(spoken), shown


def test_a_sources_block_is_not_spoken_and_its_links_go_on_the_screen(server):
    spoken, shown = _run(server, [
        "It will rain in London tomorrow, sir.\n\nSou", "rces:\n",
        "- [Met Office](https://www.metoffice.gov.uk/weather)\n",
        "- https://www.bbc.co.uk/weather\n"])
    assert spoken == "It will rain in London tomorrow, sir.\n\n"
    (display,) = shown
    assert display["kind"] == "sources"
    assert [i["label"] for i in display["items"]] == ["Met Office", "https://www.bbc.co.uk/weather"]


@pytest.mark.parametrize("header", ["Sources:", "**Sources:**", "## Sources", "References:", "Source:"])
def test_every_way_of_writing_the_header_is_caught(server, header):
    spoken, shown = _run(server, [f"Done, sir.\n{header}\n- https://example.com/a\n"])
    assert spoken == "Done, sir.\n" and len(shown) == 1


def test_a_reply_that_only_mentions_sources_is_spoken_whole(server):
    text = "Sources say it will rain, sir."
    assert _run(server, [text]) == (text, [])
    assert _run(server, ["Sou", "rce of the Nile is far, sir."]) == (
        "Source of the Nile is far, sir.", [])


def test_links_ending_a_reply_without_a_header_are_not_spoken_but_shown(server):
    """Found live: the OpenAI brain ends a web answer with its sources as
    links and no header, and their labels were read out and captioned."""
    reply = ("We can move voice-to-text local with Whisper.cpp, sir. "
             "[Whisper.cpp](https://github.com/ggml-org/whisper.cpp) "
             "[Coqui TTS](https://github.com/coqui-ai/TTS)")
    for deltas in ([reply], [reply[i:i + 7] for i in range(0, len(reply), 7)]):
        spoken, shown = _run(server, deltas)
        assert spoken.strip() == "We can move voice-to-text local with Whisper.cpp, sir."
        (display,) = shown
        assert display["kind"] == "sources"
        assert [(i["label"], i["url"]) for i in display["items"]] == [
            ("Whisper.cpp", "https://github.com/ggml-org/whisper.cpp"),
            ("Coqui TTS", "https://github.com/coqui-ai/TTS")]


def test_a_cited_site_in_brackets_and_a_bare_address_are_not_spoken(server):
    spoken, shown = _run(server, ["It rained, sir ([BBC](https://www.bbc.co.uk/weather)",
                                  "). More at https://www.metoffice.gov.uk/ today."])
    assert spoken == "It rained, sir. More at  today."
    assert [i["url"] for i in shown[0]["items"]] == [
        "https://www.bbc.co.uk/weather", "https://www.metoffice.gov.uk/"]


def test_square_brackets_that_are_not_a_link_are_spoken(server):
    assert _run(server, ["Tests pass [all of them], sir."]) == (
        "Tests pass [all of them], sir.", [])
    assert _run(server, ["A list [a", "] and more, sir."]) == ("A list [a] and more, sir.", [])


def test_inline_links_and_a_sources_block_make_one_display(server):
    spoken, shown = _run(server, ["Done, sir. [A](https://a.example/)\nSources:\n",
                                  "- [B](https://b.example/)\n"])
    assert spoken.strip() == "Done, sir."
    (display,) = shown
    assert [i["label"] for i in display["items"]] == ["A", "B"]


def test_a_bare_web_address_is_never_spoken(server):
    assert "http" not in server.strip_markdown_for_tts(
        "See https://example.com/page for more, sir.")


# --- kinds ----------------------------------------------------------------------

def test_every_server_line_says_its_kind():
    import ast
    from pathlib import Path
    tree = ast.parse((Path(__file__).parent.parent / "jarvis" / "server.py").read_text())
    unlabelled = [n.lineno for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and n.func.attr == "say" and isinstance(n.func.value, ast.Name)
                  and n.func.value.id == "speech"
                  and not any(k.arg == "label" for k in n.keywords)]
    assert unlabelled == []


async def _first_audio_kind(make):
    """The kind on the first audio message a scheduler sends for `make(sched)`."""
    from jarvis import speech as speech_module
    sent = []

    async def emit(msg):
        sent.append(msg)

    async def synth(text):
        return b"\x00" * 100

    sched = speech_module.SpeechScheduler(synth, emit, transport_ready=lambda: True)
    await sched.start()
    try:
        await make(sched)
        for _ in range(100):
            audio = [m for m in sent if m.get("type") == "audio"]
            if audio:
                return audio[0]["kind"]
            await asyncio.sleep(0.02)
    finally:
        await sched.stop()


@pytest.mark.asyncio
async def test_the_kind_goes_out_with_the_audio():
    async def readback(sched):
        await sched.say("Telling chitauri: use Postgres.", label="readback")

    async def reply(sched):
        turn = sched.begin_turn()
        sched.feed(turn, "It is done, sir.")
        await sched.end_turn(turn)

    assert await _first_audio_kind(readback) == "readback"
    assert await _first_audio_kind(reply) == "reply"
