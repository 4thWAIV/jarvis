"""The voice protocol's shared words: what the page and the server both
name, each defined once here (the page's copy is `frontend/src/voiceControl.ts`).
"""
from __future__ import annotations


class ReplyOutcome:
    """How a reply's text ended, sent on its last `reply` frame as
    `outcome`, so unfinished text is never shown as complete."""
    SUCCEEDED = "succeeded"    # the provider finished the turn
    FAILED = "failed"          # it stopped with an error, timed out, died or hit a limit
    CANCELLED = "cancelled"    # the turn itself was cancelled before it finished
    ALL = frozenset({SUCCEEDED, FAILED, CANCELLED})


# The `playback` frame the page sends about one audio chunk: what it alone
# can know. `played` keeps its own frame; skipped and interrupted are the
# server's own doing (skip_to_end, hush, stop), so the page never reports them.
PLAYBACK_FRAME = "playback"
PLAYBACK_STARTED = "started"
PLAYBACK_FAILED = "failed"
