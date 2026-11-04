"""What the voice socket does with his stop settings' turn frames: a stop
under Discard throws away what was being gathered from the page, so nothing
he was saying is sent; without it, the gathered words go as before."""

from tests.test_voice_ws import _drain_until, client  # noqa: F401  (the fixture)


def test_a_discarded_turn_is_never_sent(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "delete the whole branch", "isFinal": True,
                      "clientId": "page-a", "captureEpoch": 3})
        ws.send_json({"type": "turn_discard", "clientId": "page-a"})
        # Typing sends what was gathered first: after the discard there is none.
        ws.send_json({"type": "transcript", "text": "typed words", "isFinal": True, "typed": True})
        _drain_until(ws, lambda m: m.get("text") == "Echo: typed words.", limit=30)
        assert server.brain_instance.turns == [("typed words", "user")]


def test_without_a_discard_the_gathered_words_still_go(client):
    c, server = client
    with c.websocket_connect("/ws/voice") as ws:
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "transcript", "text": "what time is it", "isFinal": True,
                      "clientId": "page-a", "captureEpoch": 3})
        ws.send_json({"type": "transcript", "text": "typed words", "isFinal": True, "typed": True})
        _drain_until(ws, lambda m: m.get("text") == "Echo: typed words.", limit=30)
        assert [t for t, _ in server.brain_instance.turns] == ["what time is it", "typed words"]
