"""The message tool's arguments as the brain now gives them.

`steer_session` no longer takes free text: the brain first captures Tim's words
(`capture`), reads the capture back to him (`read_capture`), and passes that
capture's number. Tests written against the old `{"name", "prompt"}` shape go
through `captured`, which opens a capture already read back in full, the way
those two tools would leave it (without telling a page), and returns the new
arguments. An empty prompt opens no capture.
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
    server._captures[number] = {"id": number, "text": text, "to": "", "heard": [],
                                "read": text, "state": "capturing"}
    return {**rest, "capture": number}
