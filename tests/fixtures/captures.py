"""The message tool's arguments as the brain now gives them.

`steer_session` no longer takes free text: the brain first captures Tim's words
(`capture`), reads the capture back to him (`read_capture`), and passes that
capture's number. Tests written against the old `{"name", "prompt"}` shape go
through `captured`, which opens a capture addressed to the session named,
already read back in full naming it and approved by his yes, the way those two tools and his next words would leave it
(without telling a page), and returns the new arguments. An empty prompt opens no capture.
"""


def captured(server, args: dict) -> dict:
    text = str(args.get("prompt") or "").strip()
    rest = {k: v for k, v in args.items() if k != "prompt"}
    if not text:
        return {**rest, "capture": None}
    # Each call is a message of its own, dealt with in turn: one never sent
    # (refused, say) is out of the way, as the brain would merge or redo it.
    for n in [n for n, c in server._captures.items() if not c.get("staged")]:
        del server._captures[n]
    number = next(server._capture_ids)
    capture = {"id": number, "text": text, "to": "", "heard": [],
               "read": text, "approved": text, "state": "capturing"}
    # Addressed to the session the steer names, which its read-back named:
    # his yes covers that recipient alone.
    session, problem, _reason = server._resolve_or_explain(str(args.get("name") or ""))
    if not problem:
        capture.update(to=session.voice_name, to_session=session.session_id)
    capture["read_to"] = server.transmission.recipient(capture)
    server._captures[number] = capture
    return {**rest, "capture": number}
