"""
subsystem_diagnostics.py — how each of JARVIS's major parts is doing, one
item per part, for the Diagnostics tab: above all, whether he can still talk
to the agents.

Each part has one probe, a plain function from what it reads to a
`Subsystem`: a state (`ok`, `degraded`, `down`, `idle` with nothing to judge,
`unknown` with no reading yet), one line saying why, and the checks that
decided it. Nothing is stored: every state is worked out when it is read,
the way the workflow monitor works out "quiet", from records that already
exist (the `messages` table, the workflow record, the voice record) and from
what the server holds in memory (the session watcher, the brain, and the
heartbeats and outcomes kept here).

  - Agent communications: the session watcher polling, the messages JARVIS
    sends reaching their sessions, and those waiting their session's turn.
    (Agents' messages to him come by one signed route, messages_api.py,
    which has no loop to judge.)
  - Voice input: voice_diagnostics.summary, judged, not repeated.
  - Brain, voice output (Fish Audio), the tool channel (the brain's MCP child
    to /internal/tool) and the workflow monitor.
  - The folder catalog: its folders' availability and how far discovery
    has read, never worse than degraded.
  - The team server: team_client's own status, never worse than degraded,
    since JARVIS works without it.

The page judges its own connection. `gather` does the reads; the server's
route returns it, and the server's watch (`_watch_subsystems`) hints the
page and speaks when communications go down and come back.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from jarvis import data_paths, run_store, session_steer, voice_diagnostics, workflow_store
from jarvis.said import Said, english as _english
from jarvis.session_watch import DELIVERY_TIMEOUT_SEC, GONE

OK, DEGRADED, DOWN, IDLE, UNKNOWN = "ok", "degraded", "down", "idle", "unknown"
# Worst last: a subsystem is as bad as its worst check.
_RANK = {IDLE: 0, OK: 1, UNKNOWN: 2, DEGRADED: 3, DOWN: 4}

# A server this young has not had the chance to start its loops.
STARTUP_GRACE_SEC = 30.0

# The session watcher polls about every 3 s (a 1 s sleep and the poll).
WATCHER_LATE_SEC = 15.0
WATCHER_DOWN_SEC = 60.0
WATCHER_FAILURES_DOWN = 3

# How far back messages to sessions are judged, and how many are read.
MESSAGES_WINDOW_SEC = 3600.0
MESSAGES_READ = 50
# A message still `sent` this long after it went was never followed: the
# watcher that should have marked it undelivered did not get to it.
UNFOLLOWED_AFTER_SEC = DELIVERY_TIMEOUT_SEC + 30.0
# What a failed message's `failure` says when the channel lost it, as
# against JARVIS declining to send it (needs_a_human_hand, not_steerable,
# no_voice) or its session ending first.
LOST_FAILURES = frozenset({"not_live", "failed", "refused", "not delivered within a day"})
REACHED_STATUSES = frozenset({"delivered", "working", "answered"})
# Lost messages to this many different sessions in a row is the channel,
# not one session.
LOST_SESSIONS_DOWN = 2

# A message queued this long for its session's turn is held: found live, a
# session idle for two hours that JARVIS read as mid-turn kept two.
QUEUED_LONG_SEC = 600.0
# How long a node ID made although one had been kept is shown as degraded.
NODE_ID_NOTICE_SEC = 7 * 24 * 3600.0

# Voice output: this many failed syntheses in a row is down; one this
# recent is degraded.
VOICE_FAILURES_DOWN = 3
VOICE_FAILURE_RECENT_SEC = 300.0
# Voice input: speech dropped this recently is "Dropping speech" (the page's
# own DROPPING_FOR_SEC).
VOICE_DROPPING_SEC = 60.0

# The brain's own MCP server, through which it reaches /internal/tool
# (server.RESERVED_SERVER_NAME).
TOOL_SERVER = "jarvis"

WORKFLOW_LOOP = "workflow silence"
FOLDERS_LOOP = "folder discovery"


# ── the words: a code and its values, and the English said from them ─────
#
# Every line here is JARVIS's own, so each is a code and the values it names
# (`Said`), as well as the English the brain reads (`detail`, `summary`,
# `name`). The page says the code in the interface's language from its
# catalog (frontend/src/messages/right.en.ts, `right.subsystem.said.<code>`
# and `right.subsystem.name.<code>`, with these same English words and
# placeholders: tests/test_subsystem_diagnostics.py holds the two together).
# A value is never translated: an agent's name, an error another program
# wrote, a figure. A name with no code (a product, a stage's raw id) is
# shown as it is.

# The English of every line, by code. A dict is plural forms, chosen by the
# value `count`.
SAID: dict[str, str | dict[str, str]] = {
    "starting": "Starting",
    "notStarted": "Not started",
    "loopSilent": "Has not finished a look for {age}",
    "loopFailed": "The last look failed: {error}",
    "loopOk": "Last look {age} ago",
    "badCheck": "{name}: {detail}",
    "watcherNotRunning": "Not running: no session is being watched",
    "watcherFailing": "{failures} looks failed in a row: {error}",
    "watcherSilent": "Has not finished a look at the sessions for {age}",
    "watcherOk": {"one": "Watching {count} session; last look {age} ago",
                  "other": "Watching {count} sessions; last look {age} ago"},
    "outgoingNone": "No messages sent in the last hour",
    "outgoingLostMany": "The last {count} messages, to {agents} different agents, did not reach them",
    "outgoingLostManyUnseen": ("The last {count} messages, to {agents} different agents, did not reach "
                               "them; the latest: it never showed up in the agent's conversation"),
    "outgoingLostManyWhy": ("The last {count} messages, to {agents} different agents, did not reach "
                            "them; the latest: {why}"),
    # One message lost, or several (`count`): the one is never counted.
    "outgoingLostOne": "The last message to {agent} did not reach it",
    "outgoingLostOneUnseen": ("The last message to {agent} did not reach it: it never showed up in the "
                              "agent's conversation"),
    "outgoingLostOneWhy": "The last message to {agent} did not reach it: {why}",
    "outgoingLost": "The last {count} messages to {agent} did not reach it",
    "outgoingLostUnseen": ("The last {count} messages to {agent} did not reach it: it never showed up in "
                           "the agent's conversation"),
    "outgoingLostWhy": "The last {count} messages to {agent} did not reach it: {why}",
    "outgoingOk": {"one": "{count} reached their agents in the last hour",
                   "other": "{count} reached their agents in the last hour"},
    "outgoingOkSomeLost": {"one": "{count} reached their agents in the last hour, {lost} did not",
                           "other": "{count} reached their agents in the last hour, {lost} did not"},
    "queuedNone": "No message waiting for its turn",
    "queuedOk": {"one": "{count} message waiting for its turn, the oldest {age}",
                 "other": "{count} messages waiting for their turn, the oldest {age}"},
    "queuedHeld": "A message to {agent} has waited {age} for its turn",
    "queuedHeldSeen": "A message to {agent} has waited {age} for its turn; JARVIS sees it as {state}",
    "voiceIdle": "Not listening",
    "voiceDropping": "Dropping speech",
    "voiceDroppingCause": "Dropping speech: {cause}",
    "voiceBehind": "Falling behind: {cause}",
    "voiceOk": "Hearing you",
    "brainAtLimit": "At the usage limit",
    "brainWithinLimit": "Within the limit",
    "toolsFailing": "Tool calls are failing: {error}",
    "brainLoginExpired": "Its login has expired",
    "brainCrashed": "Stopped after crashing repeatedly",
    "brainRestarting": "Restarting after a crash or a stall",
    "brainAutostartOff": "Not started (autostart is off)",
    "brainNoProcess": "No brain process is running",
    "brainWarming": "Warming up",
    "brainRestarted": {"one": "Restarted {count} time in the last {age}",
                       "other": "Restarted {count} times in the last {age}"},
    "brainReady": "Ready",
    "outputNoKey": "No Fish Audio key: nothing can be said",
    "outputNothing": "Nothing said yet",
    "outputFailing": "The last {count} sentences could not be voiced",
    "outputSomeFailed": {"one": "{count} sentence not voiced in the last {age}",
                         "other": "{count} sentences not voiced in the last {age}"},
    "outputOk": "Last sentence voiced {age} ago",
    "callsLast": "Last tool call {age} ago",
    "callsNone": "No tool calls yet",
    "toolsBrainStopped": "The brain is not running",
    "toolsUnreported": "The brain has not reported its tools",
    "toolsWouldNotStart": "The brain's link to JARVIS's tools would not start",
    "toolsLink": "The brain's link to JARVIS's tools is {status}",
    "toolsNotConnected": "The brain's link to JARVIS's tools is not connected",
    "connected": "Connected",
    "workflowsQuiet": {"one": "{count} workflow has gone quiet", "other": "{count} workflows have gone quiet"},
    "workflowsRunning": {"one": "{count} workflow running", "other": "{count} workflows running"},
    "workflowsNone": "No workflows running",
    "foldersOff": "Not running",
    "foldersUnavailable": ("{count} of {total} not available ({missing} missing, {denied} access denied, "
                           "{elsewhere} on an unavailable node)"),
    "foldersOk": {"one": "{count} folder, all available", "other": "{count} folders, all available"},
    "foldersNone": "No folders registered",
    "discoveryOff": "Discovery is switched off",
    "discoveryFailed": "Could not read {source}: {error}",
    "discoveryPending": "{count} history files still to read",
    "discoveryPendingSince": "{count} history files still to read; last full look {age} ago",
    "discoveryDone": "Up to date",
    "discoveryDoneSince": "Up to date; last full look {age} ago",
    "discoveryNotYet": "Not yet looked",
    "knowledgeOff": "Switched off",
    "knowledgeRestarting": "Restarting: {reason}",
    "knowledgeNotUp": "Not up yet",
    "knowledgeNotAnswering": "Not answering its health check",
    "knowledgeIngestOff": "Switched off",
    "knowledgeReadOnly": "Read only: {problem}",
    "knowledgeOk": {"one": "Running; {count} chunk indexed", "other": "Running; {count} chunks indexed"},
    "knowledgeHeld": "Paused while the conversation needs the machine",
    "knowledgeFree": "Free to work",
    "knowledgeIngestNotYet": "No pass yet",
    "knowledgeIngestOk": {"one": "Last pass {age} ago: {count} document read",
                          "other": "Last pass {age} ago: {count} documents read"},
    "knowledgeIngestMore": "Last pass {age} ago; more is left to read",
    "knowledgeIngestFailed": "The last pass failed: {error}",
    "knowledgeIngestRunning": "Reading now",
    "knowledgeNoEmbedder": "No embedder chosen; search uses words and the graph",
    "knowledgeEmbedding": {"one": "Embedding with {embedder}; {count} waiting",
                           "other": "Embedding with {embedder}; {count} waiting"},
    "teamOnline": "Online",
    "teamOnlineSince": "Online, last contact {age} ago",
    "teamOnlineAs": "Online as {login}",
    "teamOnlineAsSince": "Online as {login}, last contact {age} ago",
    "teamOnlineTeam": "Online: {team}",
    "teamOnlineTeamSince": "Online: {team}, last contact {age} ago",
    "teamOnlineTeamAs": "Online: {team} as {login}",
    "teamOnlineTeamAsSince": "Online: {team} as {login}, last contact {age} ago",
    "teamStale": "Can't reach the team server",
    "teamStaleSince": "Can't reach the team server; last contact {age} ago",
    "teamSignedOut": "Signed out; sign in again in Settings",
    # Why it signed out, in team_client's words; its code rides beside it
    # as `errorSaid`, which the page words from its own catalog.
    "teamError": "{error}",
    "teamConnecting": "Connecting",
    "teamPairing": "Waiting for this Mac to be approved on the team server",
    "nodeIdMinted": "This Mac was given a new node ID {age} ago, although one had been kept; references made under the old one still resolve here",
    "teamNotConnected": "Not connected",
}

# The English of every subsystem's and check's name, by code.
NAMES: dict[str, str] = {
    "communications": "Communications",
    "voice_input": "Voice input",
    "brain": "Brain",
    "brainProvider": "Brain ({provider})",
    "voice_output": "Voice output",
    "tool_channel": "Tool channel",
    "workflows": "Workflow monitor",
    "folders": "Folder catalog",
    "team_server": "Team server",
    "knowledge": "Knowledge engine",
    "knowledgePace": "Pace",
    "knowledgeEmbedder": "Embedder",
    "knowledgeIngest": "Ingest",
    "watcher": "Session watcher",
    "outgoing": "Messages to agents",
    "queued": "Queued messages",
    "mic": "Microphone",
    "browser": "Page",
    "connection": "Connection",
    "recogniser": "Recogniser",
    "server": "Server",
    "nodeId": "Node ID",
    "usage": "Usage limit",
    "process": "Process",
    "toolCalls": "Tool calls",
    "toolServer": "JARVIS's tools in the brain",
    "workflowsRunning": "Workflows",
    "workflowsWatch": "Watch for quiet",
    "folderList": "Folders",
    "discovery": "Discovery",
    "discoveryLoop": "Discovery loop",
}


def say(code: str, **values) -> tuple[Said, str]:
    """A line by its code: what the page is sent, and its English."""
    return Said(code, values), _english(SAID[code], values)


def named(code: str, **values) -> tuple[Said, str]:
    """A name by its code: what the page is sent, and its English."""
    return Said(code, values), _english(NAMES[code], values)


@dataclass(frozen=True)
class Check:
    id: str
    name: str
    state: str
    detail: str
    name_said: Optional[Said] = None
    said: Optional[Said] = None


@dataclass
class Subsystem:
    id: str
    name: str
    state: str
    summary: str
    checks: list[Check] = field(default_factory=list)
    messages: Optional[list[dict]] = None       # communications only
    name_said: Optional[Said] = None
    said: Optional[Said] = None


Line = tuple[str, tuple[Said, str]]     # a state, and what is said of it


def check(id: str, name: str, state: str, line: tuple[Said, str]) -> Check:
    """A check whose name is the name code `name` and whose detail is `line`."""
    name_said, name_text = named(name)
    return Check(id, name_text, state, line[1], name_said, line[0])


def subsystem(id: str, state: str, line: tuple[Said, str], checks: list[Check],
              name: Optional[tuple[Said, str]] = None, **extra) -> Subsystem:
    """A subsystem named by its id's name code (or `name`), saying `line`."""
    name_said, name_text = name or named(id)
    return Subsystem(id, name_text, state, line[1], checks, name_said=name_said, said=line[0], **extra)


def worst(states) -> str:
    return max(states, key=_RANK.__getitem__, default=UNKNOWN)


def from_checks(id: str, checks: list[Check], fine: tuple[Said, str], **extra) -> Subsystem:
    """A subsystem as bad as its worst check, saying which check and why
    when that is down or degraded, and `fine` otherwise."""
    state = worst(c.state for c in checks)
    bad = next((c for c in checks if c.state == state), None) if state in (DOWN, DEGRADED) else None
    line = (Said("badCheck", {"check": bad.id}), f"{bad.name}: {bad.detail}") if bad \
        else say("starting") if state == UNKNOWN else fine
    return subsystem(id, state, line, checks, **extra)


def age(seconds: float) -> str:
    """A span in a few characters: 12 s, 3 min, 2 h."""
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f} s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.0f} h"


# ── what the server notes as it goes ─────────────────────────────────────

@dataclass
class Beat:
    """One background loop's heartbeat: each look that finished, or failed."""
    every: float
    started_at: float
    last_ok_at: Optional[float] = None
    failures: int = 0            # in a row
    error: Optional[str] = None

    def ok(self, now: Optional[float] = None) -> None:
        self.last_ok_at = time.time() if now is None else now
        self.failures, self.error = 0, None

    def failed(self, error: str) -> None:
        self.failures += 1
        self.error = error[:200]

    def down_after(self) -> float:
        return 4 * self.every + 30.0


beats: dict[str, Beat] = {}


def beat(name: str, every: float, now: Optional[float] = None) -> Beat:
    """A loop's heartbeat, new each time the loop starts."""
    beats[name] = Beat(every, time.time() if now is None else now)
    return beats[name]


class Outcomes:
    """The last few outcomes of something tried many times: (at, ok, detail)."""

    def __init__(self, keep: int = 20) -> None:
        self.items: deque = deque(maxlen=keep)

    def note(self, ok: bool, detail: str = "", now: Optional[float] = None) -> None:
        self.items.append((time.time() if now is None else now, ok, detail))


# Each Fish Audio synthesis, and each request on /internal/tool (refused for
# its token, or taken), and each tool call the brain's provider failed before
# it reached /internal/tool.
voice_output_calls = Outcomes()
tool_calls = Outcomes()


# ── the probes ───────────────────────────────────────────────────────────

def judge(b: Optional[Beat], now: float) -> Line:
    """A background loop's state, and what is said of it."""
    if b is None:
        return UNKNOWN, say("notStarted")
    quiet = now - (b.last_ok_at or b.started_at)
    if quiet > b.down_after():
        return DOWN, say("loopSilent", age=age(quiet))
    if b.failures:
        return DEGRADED, say("loopFailed", error=b.error)
    if b.last_ok_at is None:
        return UNKNOWN, say("starting")
    return OK, say("loopOk", age=age(quiet))


def watcher_check(watcher, now: float, started_at: float) -> Check:
    """The session watcher: the one thing that sees the agents' sessions,
    sends queued messages and follows every message sent."""
    def one(state: str, line: tuple[Said, str]) -> Check:
        return check("watcher", "watcher", state, line)
    young = now - started_at < STARTUP_GRACE_SEC
    if watcher is None or not watcher.running:
        if young:
            return one(UNKNOWN, say("starting"))
        return one(DOWN, say("watcherNotRunning"))
    if watcher.poll_failures >= WATCHER_FAILURES_DOWN:
        return one(DOWN, say("watcherFailing", failures=watcher.poll_failures,
                             error=watcher.last_poll_error))
    if watcher.last_poll_ok_at is None:
        if young:
            return one(UNKNOWN, say("starting"))
        quiet = now - started_at
    else:
        quiet = now - watcher.last_poll_ok_at
    if quiet > WATCHER_DOWN_SEC:
        return one(DOWN, say("watcherSilent", age=age(quiet)))
    if watcher.poll_failures:
        return one(DEGRADED, say("loopFailed", error=watcher.last_poll_error))
    if quiet > WATCHER_LATE_SEC:
        return one(DEGRADED, say("loopOk", age=age(quiet)))
    sessions = sum(s.state != GONE for s in watcher.snapshot.sessions)
    return one(OK, say("watcherOk", count=sessions, age=age(quiet)))


def message_outcome(m, now: float) -> Optional[str]:
    """"reached", "lost", or None (pending, queued, cancelled, or never sent
    by JARVIS's own choice)."""
    status = str(m.status)
    if m.delivered_at is not None or status in REACHED_STATUSES:
        return "reached"
    if status == "undelivered":
        return "lost"
    if status == "failed":
        return "lost" if session_steer.outcome_code(m.failure) in LOST_FAILURES else None
    if status == "sent" and now - (m.sent_at or m.staged_at) > UNFOLLOWED_AFTER_SEC:
        return "lost"
    return None


def _why_lost(m) -> tuple[str, dict]:
    """How a lost message's line ends: "Unseen" when it never showed up in
    the agent's conversation, "Why" with what the channel said when it said
    more than the bare outcome, else nothing."""
    if str(m.status) == "undelivered":
        return "Unseen", {}
    failure = m.failure or ""
    reason = failure.split(":", 1)[1].strip() if ":" in failure else ""
    return ("Why", {"why": reason}) if reason else ("", {})


def outgoing_check(messages: list, now: float) -> Check:
    """Messages to sessions, newest first, from the last hour."""
    def one(state: str, line: tuple[Said, str]) -> Check:
        return check("outgoing", "outgoing", state, line)
    recent = [(m, message_outcome(m, now)) for m in messages
              if now - (m.sent_at or m.staged_at) <= MESSAGES_WINDOW_SEC]
    judged = [(m, o) for m, o in recent if o is not None]
    if not judged:
        return one(IDLE, say("outgoingNone"))
    streak = []
    for m, outcome in judged:
        if outcome == "reached":
            break
        streak.append(m)
    sessions = {m.session_id for m in streak}
    if len(sessions) >= LOST_SESSIONS_DOWN:
        end, why = _why_lost(streak[0])
        return one(DOWN, say("outgoingLostMany" + end, count=len(streak), agents=len(sessions), **why))
    if streak:
        end, why = _why_lost(streak[0])
        if len(streak) == 1:
            return one(DEGRADED, say("outgoingLostOne" + end, agent=streak[0].agent_name, **why))
        return one(DEGRADED, say("outgoingLost" + end, count=len(streak), agent=streak[0].agent_name, **why))
    reached = sum(o == "reached" for _, o in judged)
    lost = len(judged) - reached
    return one(OK, say("outgoingOkSomeLost", count=reached, lost=lost) if lost
               else say("outgoingOk", count=reached))


def queued_check(queued: list, watcher, now: float) -> Check:
    """Messages waiting for their session's turn, oldest first: one held
    too long names its agent and what JARVIS takes that session to be doing."""
    def one(state: str, line: tuple[Said, str]) -> Check:
        return check("queued", "queued", state, line)
    if not queued:
        return one(IDLE, say("queuedNone"))
    oldest = min(queued, key=lambda m: m.staged_at)
    waited = now - oldest.staged_at
    if waited <= QUEUED_LONG_SEC:
        return one(OK, say("queuedOk", count=len(queued), age=age(waited)))
    snapshot = getattr(watcher, "snapshot", None)
    session = snapshot.by_id(oldest.session_id) if hasattr(snapshot, "by_id") else None
    if session is not None:
        return one(DEGRADED, say("queuedHeldSeen", agent=oldest.agent_name, age=age(waited),
                                 state=str(session.state)))
    return one(DEGRADED, say("queuedHeld", agent=oldest.agent_name, age=age(waited)))


def communications(watcher, messages: list, now: float, started_at: float,
                   queued: list = ()) -> Subsystem:
    checks = [
        watcher_check(watcher, now, started_at),
        outgoing_check(messages, now),
        queued_check(list(queued), watcher, now),
    ]
    first = checks[0]
    return from_checks("communications", checks, (first.said, first.detail),
                       messages=[message_json(m, now) for m in messages[:20]])


def message_json(m, now: float) -> dict:
    return {"id": m.id, "agentName": m.agent_name, "status": str(m.status),
            "outcome": message_outcome(m, now), "at": m.sent_at or m.staged_at,
            "failure": m.failure}


_STAGE_STATES = {"ok": OK, "warn": DEGRADED, "bad": DOWN, "unknown": UNKNOWN}
# The audio's stages, by their names' codes (the page's own voice stages).
_STAGE_NAMES = {"mic": "mic", "browser": "browser", "connection": "connection",
                "recogniser": "recogniser", "jarvis": "server"}


def _stage(stage: str, state: str, detail: str) -> Check:
    """One stage of the audio: a figure, no words."""
    code = _STAGE_NAMES.get(stage)
    name_said, name_text = named(code) if code else (None, stage)
    return Check(stage, name_text, state, detail, name_said)


def voice_input(summary: dict, now: float) -> Subsystem:
    """Voice input, judged from the voice record's own summary."""
    state = summary["state"]
    dropped = summary.get("lastDropAt")
    checks = [_stage(stage, _STAGE_STATES.get(v["state"], UNKNOWN),
                     "" if v["value"] is None else f"{v['value']:.2f}")
              for stage, v in summary["pipeline"].items()]
    if state == "idle":
        return subsystem("voice_input", IDLE, say("voiceIdle"),
                         [Check(c.id, c.name, IDLE, "", c.name_said) for c in checks])
    cause = summary.get("cause") or ""
    # The cause's own code rides beside its English (`causeSaid`), for the
    # page to word from its catalog (voice_diagnostics.CAUSES).
    coded = ({"causeSaid": {"code": summary["causeCode"], "values": summary.get("causeValues") or {}}}
             if summary.get("causeCode") else {})
    if state == "no-frames" or (dropped is not None and now - dropped < VOICE_DROPPING_SEC):
        return subsystem("voice_input", DOWN,
                         say("voiceDroppingCause", cause=cause, **coded) if cause else say("voiceDropping"), checks)
    if state != "healthy":
        return subsystem("voice_input", DEGRADED, say("voiceBehind", cause=cause, **coded), checks)
    return subsystem("voice_input", OK, say("voiceOk"), checks)


def brain(b, autostart: bool, now: float, started_at: float,
          calls: Optional[Outcomes] = None) -> Subsystem:
    """The brain, from the live Brain; and its tool calls, since a brain that
    cannot use a tool cannot do anything he asks of it."""
    def one(state: str, line: tuple[Said, str]) -> Subsystem:
        usage = (check("usage", "usage", DEGRADED, say("brainAtLimit"))
                 if b is not None and b.rate_limited else
                 check("usage", "usage", OK, say("brainWithinLimit")))
        provider = getattr(getattr(b, "provider", None), "name", None)
        checks = [check("process", "process", state, line), usage]
        if calls is not None and calls.items and not calls.items[-1][1]:
            failing = say("toolsFailing", error=calls.items[-1][2])
            checks.append(check("tools", "toolCalls", DOWN, failing))
            if state in (OK, DEGRADED):
                state, line = DOWN, failing
        return subsystem("brain", state, line, checks,
                         name=named("brainProvider", provider=provider) if provider else None)
    if b is None:
        return one(UNKNOWN, say("notStarted"))
    if b.failed:
        return one(DOWN, say("brainLoginExpired") if b.failure_reason == "auth" else say("brainCrashed"))
    if b.restarting:
        return one(DEGRADED, say("brainRestarting"))
    if not b.running:
        if not autostart:
            return one(UNKNOWN, say("brainAutostartOff"))
        if now - started_at < STARTUP_GRACE_SEC:
            return one(UNKNOWN, say("starting"))
        return one(DOWN, say("brainNoProcess"))
    if not b.ready:
        return one(UNKNOWN, say("brainWarming"))
    if b.rate_limited:
        return one(DEGRADED, say("brainAtLimit"))
    if b.recent_restarts:
        return one(DEGRADED, say("brainRestarted", count=b.recent_restarts,
                                 age=age(b.config.restart_window)))
    return one(OK, say("brainReady"))


def voice_output(has_key: bool, calls: Outcomes, now: float, local: Optional[dict] = None) -> Subsystem:
    """Fish Audio, from each synthesis's outcome."""
    def one(state: str, line: tuple[Said, str]) -> Subsystem:
        # Fish Audio is a product's name: it has no code, and is shown as it is.
        return subsystem("voice_output", state, line,
                         [Check("fish", "Fish Audio", state, line[1], None, line[0])])
    if local is not None:
        problem = local.get("error")
        state = DOWN if problem else (OK if local.get("state") == "ready" else UNKNOWN)
        summary = problem or ("Local Kokoro CPU voice ready" if state == OK else "Local Kokoro model installed; loads on first speech")
        return Subsystem("voice_output", "Voice output", state, summary,
                         [Check("local", "Local voice (Kokoro)", state, summary)])
    if not has_key:
        return one(DOWN, say("outputNoKey"))
    items = list(calls.items)
    if not items:
        return one(UNKNOWN, say("outputNothing"))
    last = items[-VOICE_FAILURES_DOWN:]
    if len(last) == VOICE_FAILURES_DOWN and not any(ok for _, ok, _ in last):
        return one(DOWN, say("outputFailing", count=VOICE_FAILURES_DOWN))
    failed = [at for at, ok, _ in items if not ok and now - at < VOICE_FAILURE_RECENT_SEC]
    if failed:
        return one(DEGRADED, say("outputSomeFailed", count=len(failed), age=age(VOICE_FAILURE_RECENT_SEC)))
    return one(OK, say("outputOk", age=age(now - items[-1][0])))


def tool_channel(b, calls: Outcomes, now: float) -> Subsystem:
    """The brain's MCP child and /internal/tool."""
    def one(state: str, line: tuple[Said, str]) -> Subsystem:
        taken = [at for at, ok, _ in calls.items if ok]
        last = say("callsLast", age=age(now - taken[-1])) if taken else say("callsNone")
        return subsystem("tool_channel", state, line,
                         [check("server", "toolServer", state, line),
                          check("calls", "toolCalls", OK if taken else IDLE, last)])
    if calls.items and not calls.items[-1][1]:
        return one(DOWN, say("toolsFailing", error=calls.items[-1][2]))
    if b is None or not b.running:
        return one(UNKNOWN, say("toolsBrainStopped"))
    server = next((s for s in b.mcp_servers if s.get("name") == TOOL_SERVER), None)
    if server is None:
        return one(UNKNOWN, say("toolsUnreported"))
    status = str(server.get("status", "")).lower()
    if status == "failed":
        return one(DOWN, say("toolsWouldNotStart"))
    if status != "connected":
        return one(DEGRADED, say("toolsLink", status=status) if status else say("toolsNotConnected"))
    return one(OK, say("connected"))


def workflows(active: list, b: Optional[Beat], now: float) -> Subsystem:
    """The workflow monitor: its watch for quiet, and the workflows running."""
    quiet = [w for w in active if w.state == workflow_store.SILENT]
    if quiet:
        shown = (DEGRADED, say("workflowsQuiet", count=len(quiet)))
    elif active:
        shown = (OK, say("workflowsRunning", count=len(active)))
    else:
        shown = (IDLE, say("workflowsNone"))
    checks = [check("running", "workflowsRunning", *shown),
              check("watch", "workflowsWatch", *judge(b, now))]
    s = from_checks("workflows", checks, shown[1])
    if s.state == OK and shown[0] == IDLE:
        s.state = IDLE                  # watching, with nothing to watch
    return s


def folder_catalog(health: Optional[dict], b: Optional[Beat], now: float) -> Subsystem:
    """The folder catalog (folder_service.health): whether its folders are
    there, and how far discovery has read the sessions' records. Never
    worse than degraded: nothing JARVIS says or sends waits on it."""
    if health is None:
        return subsystem("folders", IDLE, say("foldersOff"), [])
    counts = health.get("counts") or {}
    total = sum(counts.values())
    unavailable = {k: n for k, n in counts.items() if k != "available" and n}
    if unavailable:
        shown = (DEGRADED, say("foldersUnavailable", count=sum(unavailable.values()), total=total,
                               missing=counts.get("missing", 0), denied=counts.get("access_denied", 0),
                               elsewhere=counts.get("unavailable_node", 0)))
    elif total:
        shown = (OK, say("foldersOk", count=total))
    else:
        shown = (IDLE, say("foldersNone"))
    checks = [check("folders", "folderList", *shown)]
    coverage = health.get("coverage") or {}
    failed = [x for x in coverage.get("sources", []) if x.get("error")]
    pending = sum(x.get("pending") or 0 for x in coverage.get("sources", []))
    last_ok = max((x.get("last_ok_at") or 0 for x in coverage.get("sources", [])), default=0)
    since = {"age": age(now - last_ok)} if last_ok else {}
    if not health.get("discovery", True):
        found = (IDLE, say("discoveryOff"))
    elif failed:
        found = (DEGRADED, say("discoveryFailed", source=failed[0]["source"], error=failed[0]["error"]))
    elif pending:
        found = (OK, say("discoveryPending" + ("Since" if since else ""), count=pending, **since))
    elif last_ok:
        found = (OK, say("discoveryDoneSince", **since))
    else:
        found = (UNKNOWN, say("discoveryNotYet"))
    checks.append(check("discovery", "discovery", *found))
    if health.get("discovery", True):
        checks.append(check("watch", "discoveryLoop", *judge(b, now)))
    s = from_checks("folders", checks, shown[1])
    if s.state == DOWN:
        s.state = DEGRADED
    if s.state == OK and shown[0] == IDLE:
        s.state = IDLE
    return s


def knowledge(health: Optional[dict], now: float) -> Subsystem:
    """The knowledge engine (knowledge_runtime.health): its process, its
    pace, its ingest and its embedder. The engine is always up, so one that
    is not running (restarting, not yet up, or not answering) is down,
    whatever `JARVIS_KNOWLEDGE` says; with that off, ingest and the embedder
    are switched off (idle) while the engine itself can be fine. No runtime
    at all (`JARVIS_ENGINE_AUTOSTART` off) is switched off."""
    if health is None:
        return subsystem("knowledge", IDLE, say("knowledgeOff"), [])
    state = health.get("state")
    engine = health.get("engine") or {}
    store = engine.get("store") or {}
    on = health.get("knowledge", True)
    if state == "starting" or state == "idle":
        shown = (DOWN, say("knowledgeNotUp"))
    elif state == "restarting":
        shown = (DOWN, say("knowledgeRestarting", reason=health.get("reason") or ""))
    elif not engine:
        shown = (DOWN, say("knowledgeNotAnswering"))
    elif store and not store.get("writable", True):
        problems = store.get("problems") or [""]
        shown = (DEGRADED, say("knowledgeReadOnly", problem=problems[-1]))
    else:
        shown = (OK, say("knowledgeOk", count=(engine.get("keyword") or {}).get("chunks", 0)))
    checks = [check("process", "process", *shown)]
    if health.get("pace") == "hold":
        checks.append(check("pace", "knowledgePace", OK, say("knowledgeHeld")))
    else:
        checks.append(check("pace", "knowledgePace", OK, say("knowledgeFree")))
    ingest = health.get("ingest")
    if not on:
        checks.append(check("ingest", "knowledgeIngest", IDLE, say("knowledgeIngestOff")))
    elif ingest is not None:
        last = ingest.get("last_finished_at")
        if ingest.get("running"):
            line = (OK, say("knowledgeIngestRunning"))
        elif ingest.get("error"):
            line = (DEGRADED, say("knowledgeIngestFailed", error=ingest["error"]))
        elif last is None:
            line = (UNKNOWN, say("knowledgeIngestNotYet"))
        elif ingest.get("more"):
            line = (OK, say("knowledgeIngestMore", age=age(now - last)))
        else:
            line = (OK, say("knowledgeIngestOk", count=ingest.get("documents", 0), age=age(now - last)))
        checks.append(check("ingest", "knowledgeIngest", *line))
    vectors = engine.get("vectors") or {}
    if not on:
        checks.append(check("embedder", "knowledgeEmbedder", IDLE, say("knowledgeIngestOff")))
    elif engine:
        if vectors.get("embedder"):
            checks.append(check("embedder", "knowledgeEmbedder", OK,
                                say("knowledgeEmbedding", embedder=vectors["embedder"],
                                    count=vectors.get("waiting", 0))))
        else:
            checks.append(check("embedder", "knowledgeEmbedder", IDLE, say("knowledgeNoEmbedder")))
    s = from_checks("knowledge", checks, shown[1])
    s.state = shown[0]          # the process decides; the others inform
    if s.state == OK and any(c.state == DEGRADED for c in checks):
        s.state = DEGRADED      # a failing ingest is worth seeing
    return s


def team_server(team: dict, now: float, minted: Optional[dict] = None) -> Subsystem:
    """The team server connection, from team_client's snapshot. Optional,
    so at worst degraded: a team server that is gone never turns the
    Diagnostics tab red. A node ID made although one had been kept
    (`data_paths.node_id_minted`) is a check of its own, degraded for
    NODE_ID_NOTICE_SEC, since this node is who the network knows."""
    state = team.get("state")
    since = ""
    last = team.get("last_contact_at")
    if last:
        try:
            since = age(now - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp())
        except ValueError:
            since = ""
    server = team.get("server_url") or team.get("last_server_url") or ""
    contact = {"age": since} if since else {}
    if state == "connected":
        me = (team.get("me") or {}).get("login")
        name = (team.get("team") or {}).get("name")
        values = {**({"team": name} if name else {}), **({"login": me} if me else {}), **contact}
        code = "teamOnline" + ("Team" if name else "") + ("As" if me else "") + ("Since" if since else "")
        shown = (OK, say(code, **values))
    elif state == "stale":
        shown = (DEGRADED, say("teamStaleSince" if since else "teamStale", **contact))
    elif state == "signed_out":
        error = team.get("last_error")
        said = team.get("last_error_said")
        # Said by team_client's code where it has one; English with no code
        # (another program's words) is shown as it came.
        shown = (DEGRADED, say("teamError", error=error, errorSaid=said) if error and said
                 else (None, error) if error else say("teamSignedOut"))
    elif state == "connecting":
        shown = (UNKNOWN, say("teamConnecting"))
    elif state == "pairing":
        shown = (IDLE, say("teamPairing"))
    else:
        shown = (IDLE, say("teamNotConnected"))
    checks = [check("connection", "connection", *shown)]
    if server:
        name_said, name_text = named("server")
        checks.append(Check("server", name_text, IDLE, server, name_said))
    if minted and isinstance(minted.get("at"), (int, float)):
        when = now - minted["at"]
        checks.append(check("node_id", "nodeId", DEGRADED if when < NODE_ID_NOTICE_SEC else IDLE,
                            say("nodeIdMinted", age=age(when))))
    return subsystem("team_server", shown[0], shown[1], checks)


# ── all of them ──────────────────────────────────────────────────────────

def gather(*, watcher, brain_now, autostart: bool, has_fish_key: bool, started_at: float,
           team: Optional[dict] = None, folders: Optional[dict] = None,
           knowledge_health: Optional[dict] = None,
           local_voice: Optional[dict] = None,
           now: Optional[float] = None) -> list[Subsystem]:
    """Every subsystem, read now. Blocking (it reads the database): run it
    off the event loop."""
    now = time.time() if now is None else now
    messages = run_store.list_messages(limit=MESSAGES_READ)
    beats_now = dict(beats)
    return [
        communications(watcher, messages, now, started_at, run_store.queued_messages()),
        voice_input(voice_diagnostics.summary(now), now),
        brain(brain_now, autostart, now, started_at, tool_calls),
        voice_output(has_fish_key, voice_output_calls, now, local_voice),
        tool_channel(brain_now, tool_calls, now),
        workflows(workflow_store.active(), beats_now.get(WORKFLOW_LOOP), now),
        folder_catalog(folders, beats_now.get(FOLDERS_LOOP), now),
        knowledge(knowledge_health, now),
        team_server(team or {}, now, data_paths.node_id_minted()),
    ]


def to_json(subsystems: list[Subsystem], now: float) -> dict:
    """The route's body. A name or line with no code (`name_said` or `said`
    null) is shown as it came: a product's name, a figure, another program's
    words."""
    out = []
    for s in subsystems:
        row = asdict(s)
        if row["messages"] is None:
            del row["messages"]
        out.append(row)
    return {"now": now, "subsystems": out}
