"""What he asks to hear, the brain reads him; what JARVIS reads him word for
word, the brain is told of.

docs/planning/brain-reads/design.md: his Read on a message is a turn of his
("Read me message 42."), answered by the brain reading that message with
read_aloud in its own reply, so when he asks about it afterwards the brain
knows what he heard. Only two kinds of reading stay direct, because their
exact words are the point: a read-back for his approval (a capture, a
command, a keypress) and a package story's slides. Each tells the brain at
its next turn what was read and its number or id (`_note_spoken`). Nothing
here reads a real data/.
"""
import asyncio
import re
from types import SimpleNamespace

import pytest

from tests.attention_fixtures import attention  # noqa: F401  (a fixture)
from tests.test_message_read_reply import GUARD, _taken, server  # noqa: F401  (a fixture)
from tests.test_session_steer import Harness, _Client


class _ReadsIt:
    """A brain whose turn does what his Read asks: read_aloud on the message
    the request names, then a reply that reads it to him."""

    ready = True
    failed = False
    rotation_pending = False
    current_origin = "user"

    def __init__(self, server):
        self.server = server
        self.asked = []
        self.given = None

    async def turn(self, text, origin="user", on_delta=None, on_tool=None):
        self.asked.append(text)
        on_tool()
        number = int(re.search(r"message (\d+)\.", text).group(1))
        self.given = await self.server.tool_read_aloud({"message": number})
        on_delta("Agent Guard writes: Status. All good. In short, all is well.")
        from jarvis import brain
        return brain.TurnResult(origin=origin, text="Agent Guard writes: Status. All good. "
                                "In short, all is well.", stop_reason="result")


async def _his_read(server, monkeypatch, message_id, *, skip=False):
    """His Read on a message, its brain turn run to the end and its reply
    played to the page (or skipped); the brain, and every timeout the reply
    was waited on with."""
    h = Harness()
    brain = _ReadsIt(server)
    waited = []
    real_wait = h.sched.wait_for

    async def wait_for(utt, timeout=60.0):
        waited.append(timeout)
        return await real_wait(utt, timeout=timeout)
    monkeypatch.setattr(h.sched, "wait_for", wait_for)
    monkeypatch.setattr(server, "speech", h.sched)
    monkeypatch.setattr(server, "brain_instance", brain)

    async def emit(msg):
        pass
    monkeypatch.setattr(server, "_voice_emit", emit)
    turns = []
    monkeypatch.setattr(server, "_spawn", lambda coro: turns.append(asyncio.ensure_future(coro))
                        or turns[-1])
    skipped = []

    def skipping(text):
        if not skip:
            return False
        if not skipped:
            skipped.append(asyncio.ensure_future(h.sched.skip_reading()))
        return True
    client = _Client(h, skip=skipping)
    await h.sched.start()
    client.start()
    try:
        answer = await server.api_read_message(message_id)
        assert answer == {"id": message_id, "asking": True}
        await asyncio.wait_for(asyncio.gather(*turns), timeout=15)
    finally:
        await client.stop()
        await h.sched.stop()
    return brain, waited, h


@pytest.mark.asyncio
async def test_his_read_is_one_brain_turn_that_reads_exactly_that_message(server, monkeypatch,
                                                                         attention):
    m = _taken(server, monkeypatch, attention)
    _taken_other = server._record_message(f"agent:{GUARD}", GUARD, "Agent Guard", "",
                                          "Another\n\nNot this one.")
    brain, waited, h = await _his_read(server, monkeypatch, m.id)
    assert len(brain.asked) == 1 and brain.asked[0].endswith(f"Read me message {m.id}.")
    assert "All good." in brain.given and "Not this one." not in brain.given
    spoken = [t for _, _, t in h.audio()]
    assert spoken and all(t in "Agent Guard writes: Status. All good. In short, all is well."
                          for t in spoken), "only the brain's reply is spoken"
    assert server.conversation_log.get(m.id).state == "read", "heard in full"
    assert server.conversation_log.get(_taken_other.id).state == "new"
    (entry, _) = sorted(attention.items(("pending", "seen", "resolved")),
                        key=lambda e: e.state != "seen")
    assert entry.state == "seen"
    assert server.READ_ALOUD_TIMEOUT in waited, "a reply that reads a message may be long"
    [turn] = [i for i in server.conversation_log.page() if i.kind == "typed"]
    assert turn.detail == {"via": "read", "message": m.id}


@pytest.mark.asyncio
async def test_his_read_cut_short_leaves_the_message_new(server, monkeypatch, attention):
    m = _taken(server, monkeypatch, attention)
    await _his_read(server, monkeypatch, m.id, skip=True)
    assert server.conversation_log.get(m.id).state == "new"
    (entry,) = attention.items(("pending", "seen", "resolved"))
    assert entry.state == "pending"


@pytest.mark.asyncio
async def test_his_read_answers_a_read_back_waiting_but_approves_nothing(server, monkeypatch,
                                                                       attention):
    """A Read while a capture's read-back waits for his yes is his next
    words: it is no yes, so the capture must be read back again."""
    from jarvis import transmission
    m = _taken(server, monkeypatch, attention)
    capture = server._new_capture()
    server._set_capture(capture, "Ship it.", "Agent Guard", GUARD)
    transmission.read_back(capture, "Ship it.")
    assert transmission.awaiting_answer(capture)
    await _his_read(server, monkeypatch, m.id)
    assert not transmission.awaiting_answer(capture), "his Read was his next words"
    assert server._approve_on_his_yes(server._take_read_back_answers(), "yes") == []
    assert not transmission.accepted(capture), "it must be read back again"


# -- the reads kept direct, each told to the brain ----------------------------------------

def _notes(server) -> list:
    return list(server._notes_for_brain)


@pytest.mark.parametrize("heard,changed,says", [
    (True, False, "counts as read back in full"),
    (True, True, "changed while it was read"),
    (False, False, "cut short"),
])
def test_a_capture_read_from_its_box_is_told_to_the_brain(server, monkeypatch, heard, changed,
                                                         says):
    async def wait_for(utt, timeout=None):
        if changed:
            capture["text"] = "Ship it now."
        return heard
    monkeypatch.setattr(server.speech, "wait_for", wait_for)

    async def read_from_its_box():
        server._set_capture(capture, "Ship it.", "Agent Guard", GUARD)
        await server._finish_reading_aloud(capture, "Ship it.", server.transmission.recipient(
            capture), SimpleNamespace(chunks=["x"]))
    capture = server._new_capture()
    asyncio.run(read_from_its_box())
    [note] = _notes(server)
    assert note.startswith("Outside your turn JARVIS read him a capture's read-back")
    assert f"capture {capture['id']}, to Agent Guard" in note and says in note


class _ReadBackSpeech:
    """A voice whose read-backs are heard in full (or not), with no cancel."""

    def __init__(self, heard=True, cancelled=False):
        self.heard, self.cancelled, self.said = heard, cancelled, []

    async def say(self, text, priority=None, immediate=None, label=None):
        self.said.append((label, text))
        return SimpleNamespace(was_cancelled=self.cancelled, was_abandoned=False, chunks=[text])

    async def wait_for(self, utt, timeout=None):
        return self.heard

    async def open_cancel_window(self, seconds):
        return False


@pytest.mark.parametrize("heard,says", [(True, "the command was run"),
                                        (False, "not heard in full")])
def test_a_command_s_read_back_is_told_to_the_brain(server, monkeypatch, heard, says):
    monkeypatch.setattr(server, "speech", _ReadBackSpeech(heard))

    async def terminal(command):
        return {"success": True}
    monkeypatch.setattr(server.actions, "open_terminal", terminal)
    item = server._StagedCommand(project="guard", path="/tmp/guard", command="npm test",
                                 documented=True)
    asyncio.run(server._perform_command(item))
    [note] = _notes(server)
    assert "the read-back of the command run_command staged (command npm test)" in note
    assert says in note
    assert server.speech.said[0][0] == "readback", "still read back directly, word for word"


def test_a_keypress_s_read_back_is_told_to_the_brain(server, monkeypatch):
    monkeypatch.setattr(server, "speech", _ReadBackSpeech(True))

    async def answer(pid, key):
        return server.dialog.SENT
    monkeypatch.setattr(server.dialog, "answer", answer)
    item = server._StagedDialog(session_id=GUARD, voice_name="Agent Guard", project="guard",
                                pid=1, key="return")
    asyncio.run(server._perform_dialog(item))
    [note] = _notes(server)
    assert "the read-back of the keypress answer_dialog staged" in note
    assert "the key was pressed" in note


def test_a_cancelled_keypress_is_told_too(server, monkeypatch):
    monkeypatch.setattr(server, "speech", _ReadBackSpeech(False, cancelled=True))
    item = server._StagedDialog(session_id=GUARD, voice_name="Agent Guard", project="guard",
                                pid=1, key="escape")
    asyncio.run(server._perform_dialog(item))
    [note] = _notes(server)
    assert "he cancelled it" in note


def test_a_reading_said_again_is_told_unless_it_was_the_brain_s_own(server):
    chunk = lambda **kw: SimpleNamespace(**{"capture": None, "cue": None, **kw})  # noqa: E731
    server._note_replayed(SimpleNamespace(kind="turn", label="reply", chunks=[chunk()]))
    assert _notes(server) == [], "the brain has its own reply"
    server._note_replayed(SimpleNamespace(kind="say", label="reply", chunks=[chunk(capture=3)]))
    server._note_replayed(SimpleNamespace(kind="say", label="reply", chunks=[
        chunk(cue={"package": "p-1", "slide": 2}), chunk(cue={"package": "p-1", "slide": 3})]))
    server._note_replayed(SimpleNamespace(kind="say", label="readback", chunks=[chunk()]))
    server._note_replayed(SimpleNamespace(kind="say", label="system", chunks=[chunk()]))
    notes = _notes(server)
    assert len(notes) == 3
    assert "a capture's read-back (capture 3)" in notes[0]
    assert "(package p-1, slide 2, slide 3)" in notes[1]
    assert "a command's or keypress's read-back" in notes[2]
    assert all("he asked to hear it again" in n for n in notes)


def test_the_story_slides_read_him_are_told_to_the_brain():
    from jarvis import story_teller
    told = []
    teller = story_teller.StoryTeller(lambda: None, None, None,
                                      spoken=lambda *a: told.append(a))
    t = SimpleNamespace(package=SimpleNamespace(id="p-1"), told=[1, 2, 3])
    teller._told(t, "the story is waiting for his answer on slide 3 of 5")
    teller._told(t, "the story was told to its end")
    assert told == [("slides 1 to 3 of its story", "package p-1",
                     "the story is waiting for his answer on slide 3 of 5"),
                    ("nothing more of its story", "package p-1",
                     "the story was told to its end")]
    assert story_teller._slides([2]) == "slide 2"
    assert story_teller._slides([1, 3, 4]) == "slides 1, 3 and 4"


def test_the_server_tells_the_brain_of_story_slides_through_the_one_helper(server):
    server.story_teller._spoken("slide 1 of its story", "package p-1", "the story was told "
                                "to its end")
    [note] = _notes(server)
    assert note == ("Outside your turn JARVIS read him slide 1 of its story (package p-1) word "
                    "for word: the story was told to its end.")
