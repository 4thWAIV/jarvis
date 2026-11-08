# One channel: every message to and from Tim through one system

Work item work-889e5bcb8dd3, piece `one-channel`. Written against main at
eaf3683. This is a design, not a change: nothing here is built yet, except the
small fix in part 1, which goes in a separate pull request.

## 1. Why an agent's answer has no Read

When an agent answers one of Tim's messages, the session watcher reports the
`answered` event. `server._attend_message` records it as an attention item of
kind `agent_message`, with the `messages` row's id as its source and the agent's
name as its only untrusted word. The answer itself goes into the conversation
record as an `answer` item, under the message it answers.

The Updates list is drawn by `frontend/src/UpdatesNotice.tsx`. It has exactly
two special rows. `PackageLine` handles kind `package`, with Open and Seen.
`SessionMessageLine` handles kind `session_message`, with Read, Reply and Seen.
Every other kind falls through to the generic row, which shows the item's words
(`attention.itemWords`) and a single Seen button. `agent_message` is one of
those other kinds, so an answer shows as "Planning answered your message" with
Seen and nothing else.

Read, Seen and Reply were not applied to it because they were never built as
actions on a message. They were built for one record. `SessionMessageLine`
posts to `/api/session-messages/{n}/read` and `/reply`. On the server,
`_read_message_aloud(number)` and `_reply_capture(number)` read
`run_store.get_session_message`, which is the `session_messages` table and
nothing else. An answer lives in the `messages` table, as its `reply` column,
so neither function can reach it.

The brain can already read an answer, with `read_message_reply`. That marks the
update seen once the reply has been heard (`_read_to_him`). But there is no
route the page can call to have it read aloud, and no route to open a reply.
The three buttons were added for the case that was reported at the time, and
no one record held both kinds of message.

The small fix is the same three buttons on the agent-answer row. Read speaks
"<agent> answers: <reply>", that answer only, seen once it has been heard in
full. Reply opens a capture addressed to the session that answered: read back
and sent on his yes. Seen is unchanged. It is a separate pull request (see the
end of this document) and does not wait for the design.

## 2. Every channel that reaches Tim today

The table lists each way something reaches him now: where it appears, what he
can do with it from there, and how JARVIS knows who sent it.

The proofs mentioned in the table are these:

- "Transcript proof" is the rule from 092555b and 8656e6a. The listed agent's
  own transcript (or one of its subagents' files, or a Codex rollout) must show
  it writing the file or running the command.
- "Key proof" is the access key's label, worked out by `reporters.py`. The
  label's listed agent is the sender, or, if no listed agent has that name, the
  label itself.
- "Watcher proof" means the fact was read by JARVIS from the session's own
  transcript or roster, not reported by the session.

| Channel | Where it appears | Actions there | How the sender is proven |
|---|---|---|---|
| Agent's answer to his message (`messages` row to `answered`) | Updates line (`agent_message`); Conversation record, under his message; the agent's thread (`AgentThread.tsx`); spoken digest | Seen only (Read and Reply missing, see part 1); the brain reads it with `read_message_reply` | Watcher proof: found in the transcript of the session it was sent to, after the signature and staged time (`SessionWatcher.track`) |
| Agent's message not picked up, failed, or waiting on its own approval (`undelivered`, `send_failed`, `not_live`, `needs_approving`) | Updates line; spoken digest | Seen | JARVIS's own record of its send |
| Session message (`data/messages/<project>/`) | Updates line (`session_message`); Conversation record (`from_agent`); the agent's thread; spoken digest | Read aloud, Reply, Seen; the brain has `session_messages`, `read_session_message`, `read_session_message_aloud` and `reply_to_session_message` | Transcript proof; a subagent is shown as "name (parent)"; unproven messages are held (`.held`), never shown |
| Needs board (`data/needs/<project>.md`) | Updates line (`needs_board`); count on the agent in the Agents tab; spoken digest; the brain's `session_needs` | Seen only; no reply, and no way to answer an item there | Transcript proof per file version; unproven boards are held (8656e6a) |
| Session needs him: permission prompt or input (`needs_you`) | Updates line; macOS notification when he is not engaged; spoken digest; Agents tab state | Seen; `answer_dialog` (keystroke) by voice | Watcher proof (the session's own transcript and roster) |
| Session finished (`session_finished`) | Updates line; spoken digest | Seen | Watcher proof |
| Workflow news: new phase, decision, blocker, finished, gone quiet (`workflow`) | Updates line; Work tab, Workflows view and detail; workflow decisions also join the needs board; spoken digest | Seen; answer or Dismiss a decision on the page or by voice (`answer_workflow_decision`) | Key proof on every event; `agent_attached` must be the key's own agent's session |
| Work decisions, blockers, done, reviews (`work`, `work_follow_up`, `work_stalled`) | Work tab row and detail (Decisions, Blocking); Updates line (follow-up and stalled are silent); spoken digest for the others | Answer a decision; Approve, Not done yet, Add work; Seen; the brain's `answer_work_decision`, `review_work` | Key proof on every report, shown as "reported by"; a decision's asker is its key |
| Package inbox (`package`) | Queue tab, Packages section; one reduced Updates line; never spoken (`SILENT_KINDS`) | Open, Seen, Done (sign-off), Dismiss; answers inside the package | Caller's key, or transcript proof that the claimed session ran make_package (older rows: PR #81) |
| Run or package run ended (`run`, `package_run`) | Updates line; Runs; spoken digest | Seen; Retry | JARVIS's own run record (`run_store`) |
| Message from a person (`arrival`) | Queue tab, thread and item; Updates line; spoken digest only on the node holding the presentation lease | Read in the Queue (marks read, sends a receipt); Reply composer, reviewed and read back; Seen; the brain's `people_messages`, `read_people_thread` | Team server: the signed-in member's GitHub identity on its device session, bound by the server |
| A person's message that did not go (`send_failed` and similar) | Queue item state; Updates line | Retry, Cancel, Discard | His own record |
| Attention digest | Spoken by the brain, at most two sentences, under the `digest` origin, when `attention.py` admits it | None of its own: it points at the items above | It is made only from the items' facts; other people's words stay walled |
| macOS notification | macOS Notification Centre (`notifier.py`), only for `needs_you` and only when he is not engaged or no page is open | None (it opens nothing) | Watcher proof, through `needs_you` |
| JARVIS's own spoken notices (a language change, an update) | Spoken; Conversation record (`notice`) | None | JARVIS itself |
| Brain down, sign-in needed, usage limit (`brain`); communications down; not signed in (`sign_in`); node id minted; knowledge proposals and project candidates | Updates line; Diagnostics; sign-in reminder line | Seen; their own buttons (Sign in, Later) | JARVIS's own state |
| Shell command or keypress outcome after its read-back | Updates line; spoken digest unless it went as asked | Seen | JARVIS's own action record |
| Messages other Claude Code or Codex sessions send each other (SendMessage, peer messages) | Not in JARVIS at all: they arrive in the receiving session's terminal, and Tim sees them only there | None in JARVIS | None: the brain receives no peer messages (`brain.py` has no inbox), so nothing is relayed. A coordinator session that speaks for him reaches agents directly |
| Answers files (`data/answers/<project>/`) | Not to Tim: these go from him to agents | n/a | n/a |

Four things stand out:

1. A message from an agent reaches him in four different shapes: an answer, a
   session message, a needs-board line, and a workflow or Work report. Each is
   kept in its own record and drawn by its own component, with different
   actions.
2. Only the session message has all three of Read, Seen and Reply.
3. Replies go back by four different paths: a capture to a session, a decision
   answer, a package sign-off, and a person's review.
4. What is spoken is already one rule, the attention admission and its
   digest, plus explicit Read. Only the macOS notification sits outside it.

## 3. The design: one channel

### The record

`conversation_log` already holds his conversation as items, with per-agent
threads (`agent_session_id`, read by `/api/conversation/agents/{id}` and
`AgentThread.tsx`). It becomes the one record of every message to and from
him. It gains a counterpart and a proof on each inbound item:

- `counterpart_kind`: `agent`, `person`, `workflow`, `work` or `package`.
- `counterpart_ref`: the agent's session id; the person's GitHub id; the
  workflow, work item or inbox id.
- `sender_evidence` and `sender_subagent`: the proof that admitted it,
  `transcript`, `subagent_transcript`, `codex_rollout`, `access_key`,
  `team_server` or `watcher`.
- `source`: the row in the record it came from (`messages.id`,
  `session_messages.id`, a work event's seq, and so on), so the source record
  stays the truth for its own state and the thread only points at it.

A thread is all items with one counterpart, in time order. Every channel
writes its inbound item through one function, `conversation_log.inbound(kind,
counterpart, sender_proof, text, source)`. That function refuses an item with
no proof, which is the "no backdoors" rule in one place. Outbound items (his
messages, captures, decision answers, sign-offs, replies to people) are already
recorded, or will be, through the same function's outbound twin.

### The list

One list on the page, Messages, replaces the special rows in Updates and the
separate places where a message can be found. It has one row per thread with
anything unseen: the counterpart's name, the newest message's first line, and a
count. Opening a row shows the thread: the existing `AgentThread.tsx`,
generalised to `ThreadView.tsx`, for agents, workflows and work items. The
Queue keeps its own view for people, because receipts, files and review are
theirs, but its threads are listed here too. Updates stays for what is not a
message: runs, diagnostics, sign-in, action outcomes and the rest. The rows it
loses are `agent_message` answers, `session_message`, `needs_board`,
`arrival` and the decision events of `workflow` and `work`.

### The actions

Every message row and every message in a thread has the same three actions,
and the brain has the same three tools:

- **Read aloud**: JARVIS speaks "<sender> writes: <text>", that message only,
  and it is seen once heard in full. One server function, `read_aloud(item)`,
  dispatched by the item's source. It replaces `_read_message_aloud` and the
  brain's per-kind read-aloud tools.
- **Seen**: marks the item, and its attention item, seen. This already exists
  as `attention_api.mark_seen_source`; the thread's item points at the
  attention source.
- **Reply**: opens a capture addressed to the proven counterpart, read back as
  "To <name>[, for <subagent>]." and sent only on his yes or Send. One
  function, `reply_capture(item)`, dispatched by counterpart:
  - an agent: `steer_session` through `transmission`, as today;
  - a person: a draft reviewed through `message_approvals`;
  - a workflow or Work decision: the decision answer, whose read-back is the
    answer itself;
  - a package: a sign-off.

  Every one of these already goes through `transmission` or its own
  read-back. The new part is that one function chooses which.

A decision keeps its own Answer buttons, with its options, beside Reply, since
Reply in words to a decision becomes his answer in words.

### What is spoken

There is one rule, `attention.py`'s admission and digest, unchanged. A message
is an attention item. The digest may mention who wrote and how many messages
are waiting. Only Read aloud speaks a message's words, on his act. The macOS
notification becomes a delivery of the same admission when no page is open,
not a second rule just for `needs_you`.

### What becomes a view, and what is retired

These become views of the one record:

- `session_messages` keeps its rows, as the source; its list tool becomes the
  thread filter.
- The `messages` table's `reply`.
- The needs boards: each proven rewrite becomes one message, "Planning's needs
  board: 3 items", whose body is the board. The board file stays the agents'
  interface.
- Workflow and Work decisions and reviews.
- Package inbox arrivals.
- People's messages, which the Queue still owns.

These are retired:

- `SessionMessageLine` and the generic `agent_message` row in
  `UpdatesNotice.tsx`.
- The `/api/session-messages/{n}/read` and `/reply` routes, which become
  `/api/messages/{item}/read` and `/reply`.
- `read_session_message_aloud` and `reply_to_session_message`, which become
  `read_aloud` and `reply`.
- `_read_message_aloud` and `_reply_capture`.
- The `from_agent` and `answer` item kinds, folded into inbound items with
  counterparts.
- `notifier`'s own call site.

### The work, in phases

Sizes assume one agent working in a worktree. A day here means a focused day,
including tests.

1. **The record.** Size: 2 days.
   - Add counterpart, proof and source columns to `conversation_log`, with a
     migration under its own schema version.
   - Add `inbound` and `outbound`, with the proof required.
   - Backfill threads from the existing `answer`, `from_agent` and
     `to_agent` items.
   - Files: `jarvis/conversation_log.py`, `jarvis/run_store.py`,
     `tests/test_conversation_log.py`.
2. **Every channel writes through it.** Size: 3 days, mostly tests.
   - Call `inbound` from `_attend_message` (answers),
     `_take_session_messages`, `_proven_boards` (one item per proven rewrite),
     workflow and Work decision events (`_workflow_recorded`,
     `_work_recorded`), `_receive_inbox_package` and the arrival announcer.
   - Files: `jarvis/server.py`, `jarvis/work_api.py`,
     `jarvis/arrival_announcer.py`, `jarvis/package_inbox_api.py`.
3. **One set of actions.** Size: 2 days.
   - Write `read_aloud(item)` and `reply_capture(item)`, dispatched by source
     and counterpart, and their routes `/api/messages/{item}/read|reply|seen`.
   - Replace the per-kind tools with `read_aloud`, `reply` and the existing
     `mark_updates_seen`.
   - Update the parity, speech-call-site and header-lines tests.
   - Files: `jarvis/server.py`, `jarvis/jarvis_mcp.py`,
     `jarvis/claude_provider.py`, `jarvis/tool_activity.py`, the three test
     files.
4. **The page's one list.** Size: 3 days, with the page tests.
   - Build `MessagesPanel.tsx` (the list) and `ThreadView.tsx`, generalised
     from `AgentThread.tsx`, with the three actions.
   - Update `UpdatesNotice.tsx` to drop the message kinds.
   - Add the UI contract family, and English and Spanish catalogs.
   - Files: `frontend/src/MessagesPanel.tsx`, `frontend/src/ThreadView.tsx`,
     `frontend/src/conversation.ts`, `frontend/src/UpdatesNotice.tsx`,
     `jarvis/ui_contract/families/messages.json`, the catalogs, and a
     `test_voice_page_messages.py`.
5. **One rule for speech.** Size: 1 day.
   - Route the macOS notification through the attention admission as its
     no-page delivery.
   - Make the digest name counterparts by thread.
   - Files: `jarvis/attention.py`, `jarvis/notifier.py`, `jarvis/server.py`.
6. **Retire the old paths.** Size: 1 day.
   - Remove the routes, tools, components and item kinds listed above.
   - Update CLAUDE.md ("Messages from sessions", "Attention", "Tool
     activity") and the talk-to-jarvis skill.

The total is about 12 working days. Phases 1 to 3 are worth doing first and on
their own, because they give every message the three actions even before the
page changes. Phase 4 is the largest risk, because it moves where he looks.

## The small fix now

Separately from this design, a pull request to main applies the session
message's Read, Reply and Seen to an agent's answer:

- Read speaks "<agent> answers: <reply>", seen once heard.
- Reply opens a capture to the session that answered.
- The routes are `/api/messages/{n}/read` and `/reply`.
- The brain gets the same calls (`read_message_reply_aloud`,
  `reply_to_message`).

Phase 3 above later folds these into the one `read_aloud` and `reply`.
