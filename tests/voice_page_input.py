"""Helpers for the voice page tests that need it listening.

The page loads quiet: nothing is heard until Start listening is clicked on
that page (voiceInput.ts), and the button waits for the voice input
settings (conftest's page server answers them; a test may route its own).
A test that hears speech clicks it, as a person would.
"""

import json

from jarvis import voice_control

LISTEN = "#btn-listen"
SETTINGS_PATH = "**/api/settings/voice-input"


async def start_listening(page, timeout: float = 10000) -> None:
    """Click Start listening once it is offered."""
    await page.wait_for_selector(f"{LISTEN}:not([disabled])", timeout=timeout)
    await page.click(LISTEN)


async def route_settings(page, stop="keep", switch="stop", status=200, sounds=True, **more):
    """Answer the voice input settings with these (or fail with `status`),
    the rest the defaults (`more` overrides any: cues, volume, intervals),
    and accept a change, answering what was saved, its schedule and
    patience worked out as the server does. Returns the list of changes
    the page posted."""
    state = {**voice_control.defaults(), "stop": stop, "switch": switch, "sounds": sounds,
             "saved": True, **more}

    def timed():
        schedule = dict(voice_control.schedule_of(state))
        state.update(schedule=schedule, patience=schedule["send"])
    timed()
    posted: list[dict] = []

    async def handle(route):
        if route.request.method == "POST":
            change = json.loads(route.request.post_data or "{}")
            posted.append(change)
            for name, choice in (change.get("cues") or {}).items():
                state["cues"] = {**state["cues"], name: {**state["cues"][name], **choice}}
            state["intervals"] = {**state["intervals"], **(change.get("intervals") or {})}
            state.update({k: v for k, v in change.items()
                          if k in ("stop", "switch", "sounds", "volume")})
            timed()
        if status != 200:
            await route.fulfill(status=status, content_type="application/json",
                                body=json.dumps({"code": "unreadable", "error": "no"}))
            return
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(state))

    await page.route(SETTINGS_PATH, handle)
    return posted
