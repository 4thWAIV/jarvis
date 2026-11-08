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

His correction, relayed: "A failed check must immediately put the Work item in an explicit Error state, show the reason, and alert me. There must be no hidden escape states anywhere in this system: whenever it cannot determine or maintain a valid state, the only escape is straight to Error with a reason. Never silently keep showing unknown, stale, or invented progress."

## The idea in one paragraph

Every non-terminal task is waiting on exactly one thing, and that thing is a
**wait** of a known **kind**. Each kind has one **probe**: the method that reads
its current status from evidence (a record JARVIS owns, a transcript, GitHub),
not from what someone reported. One scheduler runs every probe at most 10 s
apart and stamps each wait with `checked_at` (when JARVIS last read the source)
and `status_at` (when the source says the status last changed). Every wait has
a threshold: the kind's default, or `due_at` when Tim names a deadline; past it
with no progress, the wait either moves on by itself or alerts Tim, once. When
JARVIS cannot determine or keep a valid state for any reason, the task goes
straight to **Error**, with the reason, and Tim is alerted. There is no unknown,
stale, quiet or "reported" state anywhere.

## 1. Every state and every wait today

### Planning's responsibility model (`feature/work-foundation`)

Task states (`work_tasks.STATES`), quoted: `needs_ai`, `needs_human`,
`blocked`, `complete`, `cancelled`. `blocked` "is computed from named
prerequisite tasks". Facts attached to a task, not states of their own:

- Assignment (`work_task_assignments`): assigned, acknowledged (pickup), ended; an `epoch` per assignment.
- Execution observation (`observation`): `generating`, `runtime_active`, `tool`, `inactive`, `unknown`, `disconnected`, each with `observation_expires`. `work_execution.FRESH_SECONDS = 3.0`. Under this design `unknown` and `disconnected` are not shown: an unreadable session is Error (`source_unreadable`), a gone executor on a live assignment is Error (`contradiction`), and a readable transcript that has not changed is "executing, last change at *t*", which is a determined fact.
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

**`activity_unknown` contradicts Tim's rule.** The live interim safeguard
(`work_store.ACTIVITY_UNKNOWN`, shown as "Activity unconfirmed") is exactly an
escape state: an agent claims work JARVIS cannot see, and the page keeps
showing it, unconfirmed. The same goes for the live "quiet" ("no recent
update"), "Waiting, no link to an answer" and "Stalled" lines. In the rebuild
each becomes Error: `no_check` for a claim with no evidence-based check,
`contradiction` where evidence says otherwise. None survives as a state.

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
  checked_at REAL NOT NULL,      -- when JARVIS last read the source successfully
  progress_at REAL NOT NULL,     -- last evidence of progress or a new notice
  due_at REAL,                   -- explicit deadline (Tim's, or an asker's)
  due_by TEXT,                   -- who set due_at: tim:voice, tim:page, asker:<label>
  escalated_due REAL,            -- the deadline an alert was raised for (one per deadline)
  snoozed_until REAL,
  ended_at REAL, ended_how TEXT)

work_errors(
  id TEXT PRIMARY KEY, item_id, task_key, wait_id,
  code TEXT NOT NULL,            -- one of freshness.ERROR_CODES
  reason TEXT NOT NULL,          -- plain words, shown as written
  from_state TEXT NOT NULL,      -- task state and wait status it came from
  entered_at REAL NOT NULL,
  detail TEXT NOT NULL,          -- the failed check's facts, JSON
  left_at REAL, left_how TEXT,   -- 'recovered' or 'tim'
  left_reason TEXT)              -- why it cleared
```

- A task that is not terminal has exactly one open wait (`needs_human` -> a Tim kind; `needs_ai` -> an agent kind; `blocked` -> `dependency`) or is in Error. The adapter that sets the task state opens the wait in the same transaction. A wait of a kind with no registered probe is not refused silently: the task goes to Error `no_check` (section 3).
- **Threshold** = `due_at` when set, else `progress_at + KINDS[kind].default_sec`. Progress (a new transcript line, a CI run moving from queued to running, a receipt, a new report) moves `progress_at` on and so restarts a default threshold. It never moves an explicit `due_at`: Tim's three days stay three days.
- Legacy free-text waits (`--until "CI"`) have no check, so at adoption they are mapped to a typed kind or they enter Error `no_check`.

## 3. The Error state

`error` is a task state beside Planning's five, reachable from every
non-terminal state, and from `complete` or `cancelled` only by `contradiction`.

**Entered at once, in the same transaction that finds the cause**, when:

| Code | When | Plain words (shown) |
|---|---|---|
| `check_failed` | a probe fails, raises or passes its 5 s timeout | "JARVIS could not check *what*: *error*." |
| `source_unreadable` | the source cannot be read (DB, transcript, GitHub, team server, file) | "JARVIS cannot read *source*: *reason*." |
| `stale_server` | a wait's `checked_at` is more than 15 s old on the server | "Not checked since *time*: the checker has stopped." |
| `stale_page` | the page sees a status more than 15 s old | "This page has not heard from JARVIS since *time*." |
| `invalid_transition` | any writer (a reporter, the page, the brain, `sweep`, `_reconcile`, `settle`, a probe) attempts a transition the model does not allow; the command is still refused and changes nothing else | "*Who* tried to move *task* from *state* to *state*, which is not allowed." |
| `contradiction` | evidence contradicts a claim (agent says working, transcript shows its turn ended; says waiting on CI, GitHub shows it concluded; a delivered result's target is missing or changed; a live assignment's session is gone; a done task's acceptance record is missing) | "*Claim*, but *evidence*." |
| `no_check` | no evidence-based check exists for the wait's kind or subject | "JARVIS has no way to check *what*." |

On entry it records the code, the reason, the state it came from (task state
and wait status) and the time, as a `work_errors` row and a `task_error` event.
It raises **one alert**: an attention item `work_error` per Work item (source =
item id; a second task's error is a revision of the same item, so a burst is one
item), digest-eligible, so spoken at most once: "*Task* on *item* is in error:
*reason*." It shows as a red **Error** pill with the reason on the piece's row,
on the item (the item is in Error while any task is), and in the brain's
`work_status` ("Error since *time*: *reason*"). The threshold of Error is zero:
it alerts on entry and is never re-alerted (section 5).

**Error is left only two ways:**

1. **A successful check** that re-establishes a valid state: the probe reads the source and the evidence fits a valid state. The task moves to that state, the row gets `left_how='recovered'` and `left_reason` (for example "GitHub answered again: run 123 in progress"), and the alert item is resolved. For `no_check` this can only happen once the wait has a check (a new kind, or the subject remapped to a checkable one).
2. **Tim's act**: on the page or by voice (`resolve_work_error`, an acting tool, same service as the page) he cancels the task, reassigns it, remaps its wait to a checkable kind, or sets the state he knows to be true; the new state must itself pass its check on the next pass, or the task goes back to Error. Recorded `left_how='tim'` with his words as the reason.

No timer, report or agent command leaves Error.

## 4. The table

Columns: **(a)** source of truth; **(b)** refresh, and which stamps it writes;
**(c)** what happens when the source cannot be read; **(d)** default threshold
and override; **(e)** past the threshold. "Alert" always means one attention
item (section 5): "digest" means it may be spoken once in a digest; "silent"
means counted and listed, never spoken (`attention_store.SILENT_KINDS`).

Every row's (c) is the same rule: **Error**, `source_unreadable` or
`check_failed`, at once, with the reason; nothing of the last value is shown.

| State / wait | (a) Source of truth | (b) Refresh | (c) Can't read | (d) Default threshold; override | (e) Past it |
|---|---|---|---|---|---|
| `needs_human`: decision or task question | `work_decisions` row: `answer`, `withdrawn_at`, `signed_off_at` (JARVIS's own record, written by his act) | push: the answer's transaction ends the wait (`wait_links.settle`); scheduler re-reads every 10 s; `status_at` = asked_at, `checked_at` = read time | Error | 24 h from asked; `due_at` from the asker's `--needed-by` or Tim's own "remind me Friday" | Alert, digest: "Still waiting on you: *question*, asked *Mon 14:10*." |
| `needs_human`: review of a delivered result | `work_task_results` current row + the target check (stat, then digest on change) | push on deliver/accept; target stat every 10 s (today 30 s: changed, see choices) | Error; a target missing or changed is Error `contradiction` (replacing Planning's silent withdrawal), and the recovery owner is still notified | 24 h from delivery; `due_at` if the agent says "needed for Tuesday's release" | Alert, digest: "*Title* has been ready for your review since *time*." |
| `needs_human`: authorization task | the `authorize-*` task row | push on his answer; 10 s re-read | Error | 24 h | Alert, digest: "*Title* is paused until you authorize it, since *time*." |
| `needs_human`: package questions / sign-off | `package_inbox` row state and `questions` counts read from the package's own `results/decisions.json` | push: inbox writes send a `packages` hint; scheduler re-reads every 10 s | Error | 24 h from arrival (not opened) and again 24 h from last answer (opened, unanswered); `due_at` from the package's `needed_by` | Alert, digest: "The *title* package has waited for you since *time*: *n* decisions open." |
| `needs_ai`: assigned, not picked up | the delivery's message seen in the session transcript (`messages.status` from `SessionWatcher.track`); `acknowledged_at` is the agent's claim and counts only with that turn seen | session watcher's existing poll (`AGENTS_POLL_SEC` 1 s); stamps from that poll's time | Error; session gone with the assignment live is `contradiction` | 10 min from assignment; `next_check_at` overrides | Transition: recovery obligation to the recovery owner (Planning's `sweep`). If the owner is unresolved or has not picked it up in 10 min more: alert, digest: "Nobody has picked up *task* since *time*; *owner* has not answered." |
| `needs_ai`: executing | the tracked task turn's transcript or subagent file (`work_execution.poll`): readable, and when it last changed | same 1 s poll; `status_at` = last transcript change, `checked_at` = poll time | Error; turn ended while the agent claims working is `contradiction` | 10 min with no transcript change; `next_check_at` overrides | Transition: recovery obligation to the recovery owner. Recovery not picked up in 10 min: alert, digest: "*Agent* stopped on *task* at *time* and nobody has taken it over." |
| `needs_ai`: agent waiting on its own background work | `session_watch.background_waiting` (background tasks started in the subagent file and not seen to end) | 1 s poll; `status_at` = when the task started | Error | 30 min (`MONITOR_MAX_SEC`); if the background task is a CI watch, the CI row's 1 h applies instead | Transition back to "executing" (whose 10 min then runs). No alert of its own. |
| `needs_ai`: owner notification | `work_deliveries` row and its `messages` row | 1 s poll (in `work_delivery.poll`) | Error | 60 s to arrive in the transcript (`DELIVERY_TIMEOUT_SEC`); 30 min to `answered`/`turn_observed` | Not arrived in 60 s: alert, silent: "Your answer on *task* has not reached *agent*." 30 min with no turn: alert, digest: "*Agent* has not taken up your answer on *task*, sent *time*." |
| `needs_ai`: executor stop requested | **none today**: Error `no_check` on request, until stop instrumentation exists | — | Error | — | — |
| `blocked`: on another task | the predecessor's own wait (Planning's `_satisfied`) | inherits the predecessor's probe and stamps | the predecessor's Error marks this task Error too, reason "*predecessor* is in error" | none of its own: the predecessor's threshold covers it, so one stuck thing gives one alert | Transition to `needs_ai`/`needs_human` when satisfied (Planning's `_reconcile`). |
| CI (new kind `ci`) | GitHub Actions run status for the exact commit: `gh api repos/<o>/<r>/actions/runs?head_sha=<sha>` (latest run per workflow), the way `self_update.ci_lookup` reads `ci.yml` | scheduler, every 10 s, **one request per repository** for all its open CI waits, with `If-None-Match` (a 304 costs no rate limit); `status_at` = run `updated_at`, `checked_at` = response time | Error: "GitHub cannot be read: *gh missing / logged out / rate limited / timeout*"; no run for the sha is Error `contradiction` | 1 h from the run starting (queued time counts); a shorter or longer `due_at` only from Tim | Run concludes: transition. Success -> the task's continuation (`needs_ai`, delivered to the agent, or `needs_human` for review). Failure -> `needs_ai` with the failing job named. Past 1 h still queued or running: alert, digest: "CI on *repo* *PR/sha* has been running for over an hour." |
| A person (new kind `person`) | the messaging record (`messaging_store`): a message from that person in the same thread after the sent one ends the wait; `received` and `read` receipts are progress | `MessagingRuntime` syncs every 5 s (`SYNC_INTERVAL_SEC`); scheduler reads the local store every 10 s; `status_at` = receipt or reply time, `checked_at` = last successful team-server sync | Error: "The team server cannot be reached" (not signed in, or the last sync older than 15 s) | 48 h from send; `due_at` when Tim gives one ("give him three days" -> sent_at + 72 h) | Reply: transition, the reply goes to the task's continuation (usually `needs_human`: "Matt replied"). Past due with no reply: alert, digest: "Matt hasn't answered your message about *subject*. You gave him until *Wed 11 Nov 14:10*." A send that has not left in 1 h: alert, digest: "Your message to Matt has not gone yet." |
| A package sent to someone (`package` kind on our side) | `package_inbox` state for packages to Tim; for a package sent to a person, the person row above on the carrying message | as above | Error | 24 h; `due_at` | as the matching row above |
| A workflow (`workflow`) | the attached sessions' subagent files (evidence), with `workflow_store` events as claims checked against them | `workflows` hint push; scheduler re-reads every 10 s; `status_at` = latest file change, `checked_at` = read | Error; no attached session is `no_check`; events saying running while no session is alive is `contradiction` | 10 min with no subagent file change (`SILENT_AFTER_SEC`); a workflow waiting on Tim uses the Tim rows | Finished/failed (and the sessions agree): transition. No change for 10 min: alert, digest: "The *name* workflow has not moved since *time*." |
| A run (`run`) | `runs` row written by `RunExecutor`, which owns the process and its output; the process alive | `/ws/runs` hint push; scheduler re-reads every 10 s | Error; `running` with no live process is `contradiction` | the executor's own bounds (30 min with no output, 6 h total) end it; 10 min stuck in `queued` | Terminal state: transition. Queued 10 min: alert, silent: "Run *id* has not started." |
| An external process (new kind `external`) | one of a fixed set of checks named when the wait is opened: `http:<url>` (status code), `file:<path>` (exists, size), `gh:<o>/<r>#<n>` (issue or PR state). With none: Error `no_check` | checks every 10 s like CI | Error | 24 h; `due_at` | Check changes: transition. Past threshold: alert, digest: "*What* has held *task* since *time*." |
| `complete` | the acceptance record (`acceptance_ref`) | push; re-read every 10 s until History | acceptance record missing: Error `contradiction` | 5 min (`DONE_LINGER_SEC`) | Transition: moves to History. |
| `cancelled` | the cancellation event and confirmed stop | push | Error | 5 min | Transition: moves to History. |
| `error` | the `work_errors` row; its cause's probe keeps running every pass | every pass | the cause stays; a new cause is a revision | zero: alerts on entry | Leaves only by a successful check (`recovered`) or Tim's act (section 3). |

## 5. Cross-cutting parts

### One refresh scheduler, with a budget

- `jarvis/freshness.py` holds `KINDS`: per kind its probe, its cadence and its default threshold; and `ERROR_CODES`. One loop in the server (`_watch_freshness`, an `_every` loop with a heartbeat, so Diagnostics sees it) ticks every 1 s and runs each kind whose cadence is due, off the event loop, in **one** worker thread.
- A probe reads **all** open waits of its kind in one pass: one SQL query, one GitHub request per repository, one look at the session watcher's snapshot it already took. No probe opens a transcript itself: agent kinds use only the session watcher's existing per-poll reads, which already exist and are bounded (`MAX_AGENT_FILES`).
- No route and no brain tool calls a probe. `GET /api/work` reads the stamped columns only. This is the lesson of PR 83, where a per-request transcript read, five times over, pinned the server at 200% CPU.
- Budget: each tick at most 200 ms of work; each probe has a 5 s timeout (then `check_failed`); total scheduler CPU at most 5% of one core over a minute, measured by `time.thread_time()` and shown on its Diagnostics row; GitHub at most one request per repository per 10 s (360 an hour, inside `gh`'s 5,000), with ETags. A kind that overruns its budget is not slowed down silently: its waits go to Error `check_failed` ("the checker is over its budget").
- A watchdog, separate from the scheduler (its own `_every` loop), puts any wait whose `checked_at` is more than 15 s old into Error `stale_server`. The read API applies the same rule when it reads, so a dead watchdog still cannot serve a stale status.
- **Startup.** The first full pass runs in the server's startup, before it accepts connections (each kind in parallel, bounded by the 5 s probe timeout), so no page or tool ever reads a status from before the restart. A wait that fails that pass is Error.
- Cadences: agent kinds 1 s (the watcher's), everything else 10 s, leaving 5 s of the 15 s for the probe timeout.

### How the page shows freshness

- Each status line carries its age: "Running since 13:40 · checked 4 s ago" in the secondary colour. Times are `timeFormat.ts`'s.
- After each kind's pass the server sends one small `freshness` frame on `/ws/sessions`: `{kind: checked_at}`, at most every 5 s. The page updates "checked N s ago" from it without re-reading `/api/work`; it re-reads on a `work` hint (a status changed, or an Error was entered or left).
- **The page's own check:** a status whose `checked_at` the page sees as more than 15 s old is drawn as Error `stale_page` with its reason ("This page has not heard from JARVIS since *time*"), from one timer for the soonest expiry. The page posts it to `POST /api/work/errors` when it can reach the server, which records it and raises the alert; if it cannot, the server's watchdog has already put the same waits into `stale_server`, and the page shows its own red Error in place of every status meanwhile. It never shows the old value.
- Error is a red pill with its reason on the row, a red banner on the item ("In error: *reason* — since *time*"), and the actions that leave it (Cancel, Reassign, Change what it waits for, Set state).
- An overdue wait shows "Overdue since *time*" in amber, with Snooze and Change deadline beside it.

### How the 15 s promise is tested

- **Registry test:** every state in `work_tasks.STATES`, plus `error`, and every wait kind maps to a registered probe with cadence <= 10 s and a default threshold; a kind with no check must map to Error `no_check` (as `test_brain_page_parity` does for tools).
- **Scheduler test, fake clock:** for each kind, a source that changes at time t appears in the read API by t + 15 s; a probe that hangs past its timeout yields Error `check_failed` at once, not the old value; 500 open waits stay inside the tick budget.
- **Watchdog test:** stop the scheduler; at 15 s every wait is Error `stale_server`, with one alert per Work item; start it; each is `recovered` with its reason.
- **Read-path test:** a row with `checked_at` 16 s old reads as Error.
- **Error-entry tests:** one per code, each from every non-terminal state: one `work_errors` row, the from-state recorded, one attention item, and the reason on the row, item and `work_status`. Leaving: a passing check gives `recovered`; Tim's act gives `tim`; a report or timer leaves it in Error.
- **No-probe-on-request test:** importing a probe outside `freshness.py` fails (a static check like `test_speech_call_sites.py`); a request handler run under a probe that raises proves no route touches it.
- **Page test** (marked `page`): freeze the socket; the pill turns Error `stale_page` at 15 s; send a `freshness` frame; it returns.
- **Escalation test:** one wait past its threshold through 100 ticks and a restart produces exactly one attention item; snooze produces exactly one more at its end.

### How a deadline Tim states is captured

- **Voice.** `draft_message` and `steer_session` take an optional `reply_by`; a new acting tool `set_wait_deadline(wait or task, due)` sets one on any open wait. The brain turns his words into an absolute time in his time zone ("three days" -> sent time + 72 h) and the read-back says it: "To Matt. … I'll tell you if he hasn't answered by Wednesday 11 November, 14:10." Because the deadline is part of the read-back text, his yes approves it with the message (existing "Sending on his yes" rules); a changed deadline is a changed capture, read back again. The event records his exact words.
- **Page.** The Queue composer has "Expect a reply by" (1 day, 3 days, 1 week, a date); every wait line in the Work detail has Change deadline. Both call the same `freshness.set_due`, which the brain's tool calls too (page parity).
- An agent may propose a `due_at` (`work_report.py ... --needed-by`); it is shown as the agent's, `due_by='asker:<label>'`, and Tim's own replaces it.

### Escalation never repeated, and snooze

- An overdue alert is keyed by (wait id, the threshold it is for), as Planning keys `work_task_recovery` by `due_at`. `escalated_due` records it in the same transaction as the attention item, so a restart or a second tick finds it and does nothing.
- An Error alert is keyed by the `work_errors` row: one per entry into Error. While the task stays in Error, a different cause is a revision of the same item, never a new alert. Leaving and re-entering Error (a new row) is a new alert, because it is a new failure.
- It is one attention item per wait (`work_overdue`) or per Work item (`work_error`); later facts are revisions, not new items. Neither is re-spoken: the digest marks it seen when heard in full, as today.
- Progress after an overdue alert resolves the item ("CI finished", "Matt replied"). A fresh default threshold may then alert once more; an explicit `due_at` never alerts twice.
- **Snooze** (page: 1 h, 1 day, until a date; voice: the acting `snooze_wait`) applies to overdue alerts only, never to Error: sets `snoozed_until`, resolves the item, and the next alert is keyed to `snoozed_until`, so he gets exactly one more when it ends. Changing the deadline does the same with the new `due_at`. "Hold spoken updates" still applies on top.

## 6. State diagram

```
                 open wait (kind, subject, due_at?)
                              |
               no check for this kind? ----yes----+
                              | no                |
                              v                   |
   +------------------> [ CHECKED ] -------------+---> [ ERROR: code, reason,
   |                     |   |                        from-state, time;
   |  progress /         |   |  check fails, source    one alert ]
   |  new notice         |   |  unreadable, >15 s old     |       |
   |  (restarts default) |   |  (server or page),         |       |
   +---------------------+   |  invalid transition,  check passes  Tim acts
                         |   |  contradiction         (recovered)  (cancel, reassign,
                         |   |                            |        remap, set state)
                         |   | source says done           v        |
                         |   +-----------> [ ENDED ] <----+--------+--> valid state
                         |                    |                       (checked next pass)
              threshold  |                    v
              passed     v              task's next state (continuation)
          +-------- kind has a transition? --------+
          | yes (pickup, executing, CI concluded,  | no
          |      background, dependency, done)     |
          v                                        v
   [ TRANSITION ]                        [ OVERDUE: one alert ]
   (recovery owner,                        |      |       |
    History)                          snooze  change   progress
                                         |   deadline     |
                                         v      v         v
                                    [ CHECKED, alert re-keyed ]  [ resolved ]

 Every arrow that cannot be taken validly goes to ERROR. There is no other escape.
```

## 7. Gaps: waits with no evidence-based check today

Each of these goes **straight to Error `no_check`** under this rule until it
gets a check. They are ordered by how many live items they would put into Error
at adoption. Counts are a read-only snapshot of the live database at
2026-11-08 14:15 (12 open Work items), not a promise about the count on the day.

1. **Review with no checked result** (live "Verifying" / "Ready for review"): **47 pieces.** None has a `work_task_results` current row, so there is nothing to check; 24 carry free evidence references that are not a checked result. Error `no_check` until each is mapped to a delivered result or back to AI work.
2. **AI work with no assignment** (live "Ready" and "Planned"): **43 pieces** (42 ready, 1 planned). With no assignment there is no executor and no transcript to read. Error `no_check` until assigned.
3. **"Working" claimed with no bound assignment** (live `activity_unknown`): **16 pieces.** 11 name only an agent session, a claim with no task-bound turn; 5 name a subagent (`/sub:`), whose file can be read, but not against an assignment epoch. All 16 are Error `no_check` until mapped to an assignment; the 5 then have a working check at once. Token generation itself stays unprovable (Planning: "Existing CLI session transcripts do **not** prove current token generation"); this design uses transcript change only as progress and claims nothing about generation.
4. **Free-text waits with no link** (live "Waiting, no link to an answer"): **3** (2 pieces, 1 item), all holds of Tim's in words ("your word to start it", the paused loading investigation, EvoLang). Error `no_check` until each is mapped to an authorization task, which is checkable. (Planning's rehearsal counted four at its time.)
5. **Executor stop confirmation:** **0 live**, but every cancellation and dependency stop after adoption. Planning: "Automatic executor-stop instrumentation remains an integration step." Error `no_check` from the moment a stop is requested.
6. **Result target check at 30 s:** **0 live** (no current results yet), but every delivered result after adoption would go to Error `stale_server` between checks. Must move to 10 s before adoption.
7. **Workflows with no attached session:** **0 live** pieces name a workflow, but any such wait is Error `no_check`, since `workflow_store` holds only the workflow's own reports.
8. **CI:** **0 live** typed waits; any piece really waiting on CI is hidden inside items 1 and 3 above. No Work wait reads GitHub today; `self_update.ci_lookup` reads `ci.yml` on main only. Error `no_check` until the `ci` kind and its probe exist.
9. **A person:** **0 live**. The messaging record has replies and receipts, but no Work wait links to a message. Error `no_check` until the `person` kind and `reply_by` exist.
10. **External processes** (a licence, a vendor): **0 live** typed. Anything outside the three fixed checks is Error `no_check`; there is no "reported by" fallback.

Not an Error cause, but missing: there is no `due_at` anywhere today. `next_check_at` is the nearest thing, and agents set it, not Tim.

## 8. Routine choices made (one line each)

- One `work_waits` table beside Planning's tables rather than more columns on `work_pieces`, because a task changes waits over its life and each needs its own stamps.
- One `work_errors` table, so every entry into Error and how it was left is kept, not overwritten.
- `error` is a task state, and an item is in Error while any of its tasks is.
- An invalid transition from any writer, a reporter included, puts the task in Error and the command is still refused, because the writer's view of the state disagrees with the record.
- A blocked task whose predecessor is in Error is in Error too, naming the predecessor.
- One `work_error` attention item per Work item, so a burst of task errors is one alert with revisions.
- Error alerts are digest-eligible (spoken at most once); they cannot be snoozed.
- A separate watchdog loop enforces the 15 s rule on the server, and the read API enforces it again on every read.
- The server's first full check pass runs before it accepts connections, so a restart never shows pre-restart status.
- The page's stale Error is posted back to the server for the alert; the server's watchdog covers the case where the page cannot reach it.
- A target missing or changed on a delivered result is Error `contradiction`, replacing Planning's silent withdrawal; the recovery owner is still notified.
- A message to an agent not seen in its transcript within 60 s alerts silently instead of a silent "undelivered" status.
- Probe cadence 10 s (1 s for agent kinds already polled), leaving 5 s of the 15 s for a slow probe.
- Per-kind `freshness` frame instead of per-wait pushes, to keep the socket quiet.
- CI via `gh api` with ETags, one request per repository per pass; the default is Tim's 1 h ceiling.
- Default for a person: 48 h with no deadline given.
- Defaults for Tim's own waits: 24 h; for agents: 10 min, matching `QUIET_AFTER_SEC`.
- Explicit `due_at` never moves with progress; default thresholds do.
- One overdue alert per (wait, threshold); snooze and a new deadline re-key it, which is the only way a second overdue alert happens.
- Result target checks move from 30 s to 10 s, by stat first and digest only when the stat changes.
