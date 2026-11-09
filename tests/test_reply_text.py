"""Full text is streamed independently from filtered/slow/cancelled speech."""
import asyncio
import pytest
from jarvis import server
from jarvis.voice_control import ReplyOutcome
from tests.fixtures.waiting import until_async


@pytest.mark.asyncio
async def test_snapshots_are_coalesced_and_canonical_final_is_last(monkeypatch):
    frames = []
    monkeypatch.setattr(server, "_queue_voice", frames.append)
    reply = server._ReplyText(17)
    reply.feed("First ")
    reply.feed("words.")
    assert len(frames) == 1  # initial identity, no per-token task or frame
    assert await until_async(lambda: len(frames) == 2), frames   # the coalesced snapshot
    assert frames[-1]["text"] == "First words."
    assert not frames[-1]["done"]
    reply.feed(" Must not arrive after final.")
    reply.finish("Canonical final, including [source](https://example.test).",
                 ReplyOutcome.SUCCEEDED)
    await asyncio.sleep(0.07)
    assert frames[-1] == {"type": "reply", "utt": 17, "at": reply.at,
                          "text": "Canonical final, including [source](https://example.test).",
                          "done": True, "outcome": "succeeded"}
    assert sum(m["done"] for m in frames) == 1
    reply.feed("Ignored late delta")
    reply.finish()
    assert len(frames) == 3


@pytest.mark.asyncio
async def test_error_completion_keeps_already_generated_text(monkeypatch):
    frames = []
    monkeypatch.setattr(server, "_queue_voice", frames.append)
    reply = server._ReplyText(3)
    reply.feed("Partial but useful.")
    reply.finish()
    await asyncio.sleep(0.07)
    assert frames[-1]["text"] == "Partial but useful."
    assert frames[-1]["done"]
    assert frames[-1]["outcome"] == "failed", "unfinished text is never shown as complete"
    assert len(frames) == 2
