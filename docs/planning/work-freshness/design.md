# Work freshness: every state checked, dated and timed

Status: design for review. Work item `work-62cf02fa2c4d`, piece `freshness`.
It builds on Planning's state model on `feature/work-foundation` (commit
b22fb4b: `jarvis/work_tasks.py`, `work_task_schema.py`, `work_delivery.py`,
`work_execution.py`, `docs/planning/work-foundation/implementation.md`) and the
live model in `docs/work-states.md` on main. It changes neither. Nothing here
is built.

## Tim's requirements, as relayed

- No state may exist without a method that determines its current status and reports when that status was last updated.
- The displayed status must never be more than 15 seconds out of date.
- Every state has a time threshold. If an item stays in a state past its threshold without progress or a new notice, it either transitions or alerts Tim.
- Examples: waiting on CI alerts him after at most 1 hour. If Tim sends Matt a message and gives him three days, the item alerts Tim after those three days.

## The idea in one paragraph

Every non-terminal task is waiting on exactly one thing, and that thing is a
**wait** of a known **kind**. Each kind has one **probe**: the method that reads
its current status from evidence (a record JARVIS owns, a transcript, GitHub),
not from what someone reported. One scheduler runs every probe at most 10 s
apart and stamps each wait with `checked_at` (when JARVIS last read the source)
and `status_at` (when the source says the status last changed). If a probe
cannot read its source, the wait reads "Unknown since <time>", never its old
value. Every wait has a threshold: the kind's default, or `due_at` when Tim
names a deadline. Past it with no progress, the wait either moves on by itself
or raises one attention item to Tim, once.

## 1. Every state and every wait today

### Planning's responsibility model (`feature/work-foundation`)

Task states (`work_tasks.STATES`), quoted: `needs_ai`, `needs_human`,
`blocked`, `complete`, `cancelled`. `blocked` "is computed from named
prerequisite tasks". Facts attached to a task, not states of their own:

- Assignment (`work_task_assignments`): assigned, acknowledged (pickup), ended; an `epoch` per assignment.
- Execution observation (`observation`): `generating`, `runtime_active`, `tool`, `inactive`, `unknown`, `disconnected`, each with `observation_expires`. `work_execution.FRESH_SECONDS = 3.0`.
- `stop_requested` (dependency or cancellation) until the executor's stop is confirmed.
- `next_check_at` and `recovery_owner`; past `next_check_at`, `sweep` writes one `work_task_recovery` row per due timestamp and routes it to the recovery owner.
- Owner notification (`work_deliveries`): `queued`, `sent`, `delivered`, `undelivered`, `turn_observed`, `picked_up`, `blocked`, `failed`, `answered`, `cancelled`.
- Current result under review: target checked "at most once per 30 seconds per result".
- Pending task question (`work_task_questions`) with a saved continuation.

### The live model (`docs/work-states.md` on main)

Planned, Ready, Working, Waiting on *name*'s background work, Activity
unconfirmed, Waiting on you, Blocked, Ready for review, Done, Stalled; and the
lines "Answered, no follow-up" (`FOLLOW_UP_AFTER_SEC`, 30 min) and "Waiting, no
link to an answer" (`wait_links.UNLINKED_AFTER_SEC`, 24 h). Existing timers:
`QUIET_AFTER_SEC` 10 min, `STALLED_PIECE_AFTER_SEC` 24 h, `DONE_LINGER_SEC` 5 min.

### Every kind of wait the code has

| Kind | Where it is today | Typed link today? |
|---|---|---|
| Tim: a decision or task question | `work_decisions`, `work_task_questions`, `wait_links` `decision:`/`work:` | yes |
| Tim: review of a delivered result | `work_task_results` placement `current`, `next_actor='human'` | yes |
| Tim: authorization task (adoption) | `authorize-*` tasks from `_adopt` | yes |
| Tim: a package's questions or sign-off | `package_inbox`, `wait_links` `package:` | yes |
| An agent: pickup of an assignment | `work_task_assignments.acknowledged_at` | yes |
| An agent: executing | `observation` from `work_execution.poll` | yes |
| An agent: its own background work | `session_watch.background_waiting`, capped by `MONITOR_MAX_SEC` (30 min) | yes (subagent files) |
| An agent: owner notification delivered | `work_deliveries` + `messages` | yes |
| An agent: executor stop confirmed | `stop_requested` | no automatic check |
| A workflow | `workflow_ref`, `workflow_store`, `wait_links` `workflow:` | yes |
| A run | `runs` (`run_executor`) | via `who` `run:<id>` |
| Another task (dependency) | `work_task_dependencies` | yes |
| CI | free text in `--until` ("CI") | **no** |
| A person (message to Matt) | `messaging_store` replies and receipts exist; nothing links a Work wait to them | **no** |
| An external process (licence, vendor) | free text in `--until` | **no** |
| A result's review target still reachable | `work_delivery` sweep, 30 s | yes |

## 2. The wait record

One new table beside Planning's, written only by the Work store's writers and
the scheduler (routine choices are listed at the end):

```
work_waits(
  id TEXT PRIMARY KEY, item_id, task_key,
  kind TEXT NOT NULL,            -- one of freshness.KINDS
  subject TEXT NOT NULL,         -- typed ref: ci:4thWAIV/jarvis@<sha>, person:<github id>:<message id>, ...
  opened_at REAL NOT NULL,
  status TEXT NOT NULL,          -- the kind's own status words
  status_at REAL,                -- when the SOURCE says it last changed
  checked_at REAL,               -- when JARVIS last read the source successfully
  unknown_since REAL,            -- first failed read since the last good one; NULL when read
  unknown_reason TEXT,
  progress_at REAL NOT NULL,     -- last evidence of progress or a new notice
  due_at REAL,                   -- explicit deadline (Tim's, or an asker's)
  due_by TEXT,                   -- who set due_at: tim:voice, tim:page, asker:<label>
  escalated_due REAL,            -- the deadline an alert was raised for (one per deadline)
  snoozed_until REAL,
  ended_at REAL, ended_how TEXT)
```

- A task that is not terminal has exactly one open wait (`needs_human` -> a Tim kind; `needs_ai` -> an agent kind; `blocked` -> `dependency`). The adapter that sets the task state opens the wait in the same transaction. A wait of a kind with no registered probe is refused by the writer; the registry check is a test (section 4).
- **Threshold** = `due_at` when set, else `progress_at + KINDS[kind].default_sec`. Progress (a new transcript line, a CI run moving from queued to running, a receipt, a new report) moves `progress_at` on and so restarts a default threshold. It never moves an explicit `due_at`: Tim's three days stay three days.
- Legacy free-text waits (`--until "CI"`) cannot exist under this rule. Adoption maps each to a typed kind, or to `external` (below).

## 3. The table

Columns: **(a)** source of truth; **(b)** refresh, and which stamps it writes;
**(c)** shown when the source cannot be read; **(d)** default threshold and
override; **(e)** past the threshold. "Alert" always means one attention item
(section 5): "digest" means it may be spoken once in a digest; "silent" means
counted and listed, never spoken (`attention_store.SILENT_KINDS`).

Every row's (c) is the same rule: "Unknown since <first failed read> — <reason>"
with the last good value hidden; the threshold keeps running. An unknown that
lasts 10 min is its own alert (last row).

| State / wait | (a) Source of truth | (b) Refresh | (c) Can't read | (d) Default threshold; override | (e) Past it |
|---|---|---|---|---|---|
| `needs_human`: decision or task question | `work_decisions` row: `answer`, `withdrawn_at`, `signed_off_at` (JARVIS's own record, written by his act) | push: the answer's transaction ends the wait (`wait_links.settle`); scheduler re-reads every 10 s; `status_at` = asked_at, `checked_at` = read time | DB unreadable: "Unknown since" | 24 h from asked; `due_at` from the asker's `--needed-by` or Tim's own "remind me Friday" | Alert, digest: "Still waiting on you: *question*, asked *Mon 14:10*." |
| `needs_human`: review of a delivered result | `work_task_results` current row + the target check (stat, then digest on change) | push on deliver/accept; target stat every 10 s (today 30 s: changed, see choices) | target unreachable is not unknown: it withdraws the review (Planning's rule) and routes recovery | 24 h from delivery; `due_at` if the agent says "needed for Tuesday's release" | Alert, digest: "*Title* has been ready for your review since *time*." |
| `needs_human`: authorization task | the `authorize-*` task row | push on his answer; 10 s re-read | "Unknown since" | 24 h | Alert, digest: "*Title* is paused until you authorize it, since *time*." |
| `needs_human`: package questions / sign-off | `package_inbox` row state and `questions` counts read from the package's own `results/decisions.json` | push: inbox writes send a `packages` hint; scheduler re-reads every 10 s | inbox or results file unreadable: "Unknown since" | 24 h from arrival (not opened) and again 24 h from last answer (opened, unanswered); `due_at` from the package's `needed_by` | Alert, digest: "The *title* package has waited for you since *time*: *n* decisions open." |
| `needs_ai`: assigned, not picked up | `work_task_assignments.acknowledged_at`, and the delivery's message seen in the session transcript (`messages.status` from `SessionWatcher.track`) | session watcher's existing poll (`AGENTS_POLL_SEC` 1 s); stamps from that poll's time | session gone or transcript unreadable: "Unknown since" (`disconnected` already exists) | 10 min from assignment (= `QUIET_AFTER_SEC`); `next_check_at` overrides | Transition: recovery obligation to the recovery owner (Planning's `sweep`). If the owner is unresolved or itself silent 10 min more: alert, digest: "Nobody has picked up *task* since *time*; *owner* has not answered." |
| `needs_ai`: executing | assignment `observation` from `work_execution.poll`: the tracked task turn's transcript or subagent file changing (`runtime_active`, 3 s expiry) | same 1 s poll; `status_at` = last transcript change, `checked_at` = poll time | expired evidence reads `unknown` (already Planning's rule) | 10 min with no transcript change; `next_check_at` overrides | Transition to Stalled, then recovery obligation to the recovery owner. Recovery not acknowledged in 10 min: alert, digest: "*Agent* stopped on *task* at *time* and nobody has taken it over." |
| `needs_ai`: agent waiting on its own background work | `session_watch.background_waiting` (background tasks started in the subagent file and not seen to end) | 1 s poll; `status_at` = when the task started | file unreadable: "Unknown since" | 30 min (`MONITOR_MAX_SEC`); if the background task is a CI watch, the CI row's 1 h applies instead | Transition back to "executing" (whose 10 min then runs). No alert of its own. |
| `needs_ai`: owner notification | `work_deliveries` row and its `messages` row | 1 s poll (in `work_delivery.poll`) | "Unknown since" | `undelivered` after 60 s (existing `DELIVERY_TIMEOUT_SEC`); queued over 10 min (existing diagnostics rule); 30 min to `answered`/`turn_observed` | Undelivered/queued: silent update (as today). 30 min with no turn observed: alert, digest: "*Agent* has not taken up your answer on *task*, sent *time*." |
| `needs_ai`: executor stop requested | **none automatic today** (Planning: "Automatic executor-stop instrumentation remains an integration step") | interim: the assignment's observation going `inactive`/`disconnected` is taken as evidence of stop, read on the 1 s poll | "Unknown since" | 5 min from request | Alert, silent: "*Agent* has not confirmed it stopped *task* (asked *time*)." |
| `blocked`: on another task | the predecessor's own wait (Planning's `_satisfied`) | inherits the predecessor's probe and stamps | inherits | none of its own: the predecessor's threshold covers it, so one stuck thing gives one alert | Transition to `needs_ai`/`needs_human` when satisfied (Planning's `_reconcile`). |
| CI (new kind `ci`) | GitHub Actions run status for the exact commit: `gh api repos/<o>/<r>/actions/runs?head_sha=<sha>` (latest run per workflow), the way `self_update.ci_lookup` reads `ci.yml` | scheduler, every 10 s, **one request per repository** for all its open CI waits, with `If-None-Match` (a 304 costs no rate limit); `status_at` = run `updated_at`, `checked_at` = response time | `gh` missing, logged out, rate limited, timeout: "Unknown since *time* — GitHub cannot be read: *reason*" | 1 h from the run starting (queued time counts); a shorter or longer `due_at` only from Tim | Run concludes: transition. Success -> the task's continuation (`needs_ai`, delivered to the agent, or `needs_human` for review). Failure -> `needs_ai` with the failing job named. Past 1 h still queued or running: alert, digest: "CI on *repo* *PR/sha* has been running for over an hour." |
| A person (new kind `person`) | the messaging record (`messaging_store`): a message from that person in the same thread after the sent one ends the wait; `received` and `read` receipts are progress | `MessagingRuntime` syncs every 5 s (`SYNC_INTERVAL_SEC`); scheduler reads the local store every 10 s; `status_at` = receipt or reply time, `checked_at` = last successful team-server sync | not signed in or team server stale (`NETWORK_STALE_AFTER_SEC`): "Unknown since *time* — the team server cannot be reached" | 48 h from send; `due_at` when Tim gives one ("give him three days" -> sent_at + 72 h) | Reply: transition, the reply goes to the task's continuation (usually `needs_human`: "Matt replied"). Past due with no reply: alert, digest: "Matt hasn't answered your message about *subject*. You gave him until *Wed 11 Nov 14:10*." A send that never left: alert at 1 h, digest: "Your message to Matt has not gone yet." |
| A package sent to someone (`package` kind on our side) | `package_inbox` state for packages to Tim; for a package sent to a person, the person row above on the carrying message | as above | as above | 24 h; `due_at` | as the matching row above |
| A workflow (`workflow`) | `workflow_store` status and open waits (reported events), plus the attached sessions' subagent files (evidence) | `workflows` hint push; scheduler re-reads every 10 s; `status_at` = latest event, `checked_at` = read | "Unknown since" | 10 min with no event and no subagent file change (`SILENT_AFTER_SEC`); a workflow waiting on Tim uses the Tim rows | Finished/failed: transition. Silent: alert, digest: "The *name* workflow has been silent since *time*." |
| A run (`run`) | `runs` row written by `RunExecutor`, which owns the process and its output | `/ws/runs` hint push; scheduler re-reads every 10 s | "Unknown since" | the executor's own bounds (30 min with no output, 6 h total) end it; the wait adds 10 min for a stuck `queued` | Terminal state: transition. Queued 10 min: alert, silent: "Run *id* has not started." |
| An external process (new kind `external`) | one of a fixed set of checks named when the wait is opened: `http:<url>` (status code), `file:<path>` (exists, size), `gh:<o>/<r>#<n>` (issue or PR state). With none: `reported`, whose source is the owner's latest confirmation, shown as "reported by *who*, *time*", never as evidence | checks every 10 s like CI; `reported` re-asks the owner every threshold | "Unknown since" | 24 h; `due_at` | Check changes: transition. Past threshold: the owner is asked once (delivery); no confirmation in 1 h: alert, digest: "*What* has held *task* since *time*, and nobody has confirmed it is still moving." |
| `complete` | the acceptance record (`acceptance_ref`) | push | n/a | 5 min (`DONE_LINGER_SEC`) | Transition: moves to History. |
| `cancelled` | the cancellation event and confirmed stop | push | n/a | 5 min | Transition: moves to History. |
| Any wait whose source is unknown | the probe's last failure | each tick | it is this row | 10 min of unknown | Alert, silent: "JARVIS cannot check *what* for *task* since *time*: *reason*." |

## 4. Cross-cutting parts

### One refresh scheduler, with a budget

- `jarvis/freshness.py` holds `KINDS`: per kind its probe, its cadence and its default threshold. One loop in the server (`_watch_freshness`, an `_every` loop with a heartbeat, so Diagnostics sees it) ticks every 1 s and runs each kind whose cadence is due, off the event loop, in **one** worker thread.
- A probe reads **all** open waits of its kind in one pass: one SQL query, one GitHub request per repository, one look at the session watcher's snapshot it already took. No probe opens a transcript itself: agent kinds use only the session watcher's existing per-poll reads, which already exist and are bounded (`MAX_AGENT_FILES`).
- No route and no brain tool calls a probe. `GET /api/work` reads the stamped columns only. This is the lesson of PR 83, where a per-request transcript read, five times over, pinned the server at 200% CPU.
- Budget: each tick at most 200 ms of work; each probe has a 5 s timeout (it is then unknown, not late); total scheduler CPU at most 5% of one core over a minute, measured by `time.thread_time()` and shown on its Diagnostics row; GitHub at most one request per repository per 10 s (360 an hour, inside `gh`'s 5,000), with ETags. Over budget, a kind's cadence is not stretched silently: its waits go "Unknown since" and the Diagnostics row degrades.
- Cadences: agent kinds 1 s (the watcher's), everything else 10 s. 10 s + one 1 s tick + a 5 s probe timeout is still under 15 s before the page's own guard (below) takes over.

### How the page shows freshness

- Each status line carries its age: "Running since 13:40 · checked 4 s ago" in the secondary colour. Times are `timeFormat.ts`'s.
- After each kind's pass the server sends one small `freshness` frame on `/ws/sessions`: `{kind: checked_at}`, at most every 5 s. The page updates "checked N s ago" from it without re-reading `/api/work`; it re-reads only on a `work` hint (a status changed).
- **The page's own guard:** a wait whose `checked_at` is more than 15 s old is drawn "Unknown since *checked_at*" by the page itself, from one timer for the soonest expiry (like `nodeActivity`'s single timer). So a stalled server or a dropped socket can never leave an old status on screen.
- The read API applies the same rule, so the brain's `work_status` says "unknown since" too.
- An alert's wait shows "Overdue since *time*" in amber, with Snooze and Change deadline beside it.

### How the 15 s promise is tested

- **Registry test:** every state in `work_tasks.STATES` and every wait kind maps to a registered probe with cadence <= 10 s and a default threshold; adding a kind without one fails (as `test_brain_page_parity` does for tools).
- **Scheduler test, fake clock:** for each kind, a source that changes at time t appears in the read API by t + 15 s; a probe that hangs past its timeout yields "Unknown since", not the old value; 500 open waits stay inside the tick budget.
- **Read-path test:** a row with `checked_at` 16 s old reads as unknown.
- **No-probe-on-request test:** importing a probe outside `freshness.py` fails (a static check like `test_speech_call_sites.py`); a request handler run under a probe that raises proves no route touches it.
- **Page test** (marked `page`): freeze the socket; the pill turns "Unknown since" at 15 s; send a `freshness` frame; it returns.
- **Escalation test:** one wait past its threshold through 100 ticks and a restart produces exactly one attention item; snooze produces exactly one more at its end.

### How a deadline Tim states is captured

- **Voice.** `draft_message` and `steer_session` take an optional `reply_by`; a new acting tool `set_wait_deadline(wait or task, due)` sets one on any open wait. The brain turns his words into an absolute time in his time zone ("three days" -> sent time + 72 h) and the read-back says it: "To Matt. … I'll tell you if he hasn't answered by Wednesday 11 November, 14:10." Because the deadline is part of the read-back text, his yes approves it with the message (existing "Sending on his yes" rules); a changed deadline is a changed capture, read back again. The event records his exact words.
- **Page.** The Queue composer has "Expect a reply by" (1 day, 3 days, 1 week, a date); every wait line in the Work detail has Change deadline. Both call the same `freshness.set_due`, which the brain's tool calls too (page parity).
- An agent may propose a `due_at` (`work_report.py ... --needed-by`); it is shown as the agent's, `due_by='asker:<label>'`, and Tim's own replaces it.

### Escalation never repeated, and snooze

- An alert is keyed by (wait id, the threshold it is for), as Planning keys `work_task_recovery` by `due_at`. `escalated_due` records it in the same transaction as the attention item, so a restart or a second tick finds it and does nothing.
- It is one attention item per wait (`kind='work_overdue'`, source = wait id); later facts are revisions of that item, not new items. It is never re-spoken: the digest marks it seen when heard in full, as today.
- Progress after an alert resolves the item ("CI finished", "Matt replied"). A fresh default threshold may then alert once more; an explicit `due_at` never alerts twice.
- **Snooze** (page: 1 h, 1 day, until a date; voice: the acting `snooze_wait`): sets `snoozed_until`, resolves the item, and the next alert is keyed to `snoozed_until`, so he gets exactly one more when it ends. Changing the deadline does the same with the new `due_at`. "Hold spoken updates" still applies on top.

## 5. State diagram

```
                 open wait (kind, subject, due_at?)
                              |
                              v
   +------------------> [ CHECKED ] --probe fails--> [ UNKNOWN since t ]
   |                     |   |   ^                        |   |
   |  progress /         |   |   +------probe reads-------+   |
   |  new notice         |   |                                | 10 min
   |  (restarts default) |   | source says done               v
   +---------------------+   +-----------> [ ENDED ] --> task's next state
                         |                                 (continuation)
              threshold  |
              passed     v
          +-------- kind has a transition? --------+
          | yes (pickup, executing, CI concluded,  | no
          |      background, dependency, done)     |
          v                                        v
   [ TRANSITION ]                        [ OVERDUE: one alert ]
   (recovery owner,                        |      |       |
    stalled, History)                 snooze  change   progress
                                         |   deadline     |
                                         v      v         v
                                    [ CHECKED, alert re-keyed ]  [ resolved ]

 Display rule at every node: checked_at older than 15 s => "Unknown since checked_at".
```

## 6. Gaps: states with no evidence-based check today

1. **CI.** Only free text in `--until`. No Work wait reads GitHub. `self_update.ci_lookup` reads `ci.yml` on main by sha and nothing else. Needs the `ci` kind and its probe.
2. **A person.** The messaging record has the reply and the receipts, but no Work wait links to a message, and nothing holds a deadline. Needs the `person` kind and `reply_by`.
3. **External processes** (a licence, a vendor). No evidence at all; only an agent's words. The `external` kind gives three real checks; anything else stays `reported` and is marked as a claim.
4. **Executor stop.** Planning says it plainly: stop confirmation "currently requires an authorized command with stop evidence". The interim check (observation going inactive) is weaker than proof.
5. **Token generation.** Planning: "Existing CLI session transcripts do **not** prove current token generation." Freshness uses transcript change as *progress* only, which is enough for thresholds but not for a "generating" claim.
6. **Pickup** is the agent's own `acknowledge` command, a claim; the transcript turn (`turn_observed`) is the evidence and should count first.
7. **Workflows** report their own status; only their attached sessions' subagent files are evidence. A workflow with no attached session has claims only.
8. **Result target check** runs every 30 s, past the 15 s promise. Must move to 10 s (stat, digest only on change).
9. **Unlinked legacy waits** ("Waiting, no link to an answer") have no method; under this rule they must be mapped at adoption.
10. **No `due_at` anywhere today.** `next_check_at` is the nearest thing and is set by agents, not Tim.

## 7. Routine choices made (one line each)

- One `work_waits` table beside Planning's tables rather than more columns on `work_pieces`, because a task changes waits over its life and each needs its own stamps.
- Probe cadence 10 s (1 s for agent kinds already polled), leaving 5 s of the 15 s for a slow probe.
- The page enforces the 15 s rule itself, so freshness never depends on the server being alive.
- Per-kind `freshness` frame instead of per-wait pushes, to keep the socket quiet.
- CI via `gh api` with ETags, one request per repository per pass; the default is Tim's 1 h ceiling.
- Default for a person: 48 h with no deadline given.
- Defaults for Tim's own waits: 24 h; for agents: 10 min, matching `QUIET_AFTER_SEC`.
- Explicit `due_at` never moves with progress; default thresholds do.
- Overdue alerts are digest-eligible (spoken at most once, by the attention policy); unknown-source and stop alerts are silent.
- One alert per (wait, threshold); snooze and a new deadline re-key it, which is the only way a second alert happens.
- Result target checks move from 30 s to 10 s, by stat first and digest only when the stat changes.
- Unknown for 10 min is its own silent alert, so a broken probe cannot hide a stuck wait.
