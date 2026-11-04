# Quiet notifications and human availability

Source review: 2026-11-03. Design for review; no application changes made. Coordinated with the turn-taking investigation. The immediate implementation should change announcement admission and delivery, not replace recognition or voice output.

## What causes the interruptions now

| Current path | Observed behavior | Consequence |
| --- | --- | --- |
| `jarvis/arrival_announcer.py:51,211` | Incoming human arrivals batch for 3 seconds, obtain a team presentation lease, construct a fixed sentence, and call the notice callback. | Messages a few seconds apart become separate announcements. |
| `jarvis/server.py:1643` | `_say_notice` records a notice and immediately asks the scheduler for NORMAL announcement speech. | There is a shared speech helper, but no shared decision about whether this information deserves speech now. |
| `jarvis/server.py:1757,1854,1889` | Needs-board changes and session-message batches have their own spoken paths; needs-you requests use URGENT. | Different sources cannot form one digest. A task needing attention can interrupt an unrelated conversation. |
| `jarvis/speech.py:1119` | `_release_gated` turns unread backlog into one URGENT utterance after the existing silence/transport gate. LOW batches concatenate existing text. | Reconnection can produce an urgent backlog announcement; concatenation retains repeated introductions and stale details. |
| `jarvis/server.py:1930` | `_notify_needs_you` uses a native macOS notification only when no voice client is connected. | A connected browser is treated as enough reason to use speech, although it does not establish that the human is listening. |
| `jarvis/human_activity.py:88`; `frontend/src/humanActivity.ts` | Existing activity tracks accepted speech and trusted GUI interactions; GUI reporting is throttled to 5 seconds. | Useful evidence exists, but it does not currently govern announcements or establish continuous presence. |

The activity module explicitly says: “These are input the application observed, not proof a person was there.” Keep that distinction. Team-server heartbeat, socket connection, agent activity, browser focus, and a machine remaining powered on are not human engagement.

## Recommended delivery contract

All routine events first become durable attention records in the existing local storage system, linked to their original message, task or run records. Do not create a second copy of the underlying conversation. Record source ID, source revision, thread/task identity, event kind, timestamps, attention status, and presentation outcomes. Repeated updates may replace the *digest candidate* for a task; they must not erase the source history.

Route human arrivals, session messages, needs-board changes, completions, stalls and failures through this one admission policy. Keep sent/delivered/read progress visual. A task asking for input is an action item, not automatically an urgent interruption. Only explicit configured alarms or a narrowly classified condition requiring immediate intervention in the user's current operation may request exceptional interruption. An event cannot promote itself by including urgent wording.

The brain receives a bounded structured set of eligible records and their current state. It may return a short digest or silence. It has no side-effect tools in this pass. Names, subjects, bodies and agent explanations remain untrusted data; they cannot become instructions to the digest generator. Keep full originals available on their existing cards. Exact user-approved text and requested readback remain exact, rather than being rewritten as a digest.

The deterministic policy controls eligibility. The brain controls conversational wording. A model outage leaves the factual visual records and a static visual availability indicator; it must not trigger a replacement canned speech announcement. This removes fixed conversational speech without making basic failure visibility depend on the brain.

## Initial timing and floor ownership

These values are proposed local-trial defaults, not measured optimal timings:

| Condition | Initial policy |
| --- | --- |
| Oldest eligible routine event | At least 60 seconds old |
| Unsolicited spoken digest cooldown | At least 120 seconds since the previous spoken digest |
| After an exchange | At least 10 seconds after the exchange completed and the latest accepted human activity |
| Floor owner | Must be `none`; tentative and likely end-of-turn stages still belong to the user |
| Other work in progress | No partial/buffered speech, ASR backlog, outstanding reply, approval readback or current speech playback |
| Human availability | Recent positive engagement; away and unknown states never admit unsolicited speech |
| Return after absence | Show catch-up visually first; no automatic replay of every missed announcement |

An old event does not override these gates. A timer expiring is permission to reconsider, not permission to interrupt. Direct requests such as asking for updates use the ordinary requested-reply path and do not wait for the digest interval.

Use the same attention/floor arbiter as turn-taking. Capture a revision when selecting records, generating text and scheduling audio. Recheck before audio starts. New accepted speech, trusted interaction, availability changes, newer source revisions or a new reply invalidate the grant. Pending generated audio is discarded or rescheduled, never forced through because its generation finished.

The existing activity endpoint is too coarse to detect speech start: it records accepted finals, while GUI reports can be throttled. Wire recognized partial activity immediately into the floor arbiter; add speech-active and recognition-backlog signals for actual capture protection. Until those signals exist, call the timers recognized-activity timers rather than acoustic silence detection. Do not claim that a 10-second timer alone prevents interruptions during slow recognition.

## Availability and return experience

| State | Speech | Visible experience |
| --- | --- | --- |
| Engaged in speaking, typing, listening or readback | Only the current requested exchange; routine notices deferred | Counts and discreet item-state changes; no toast for each event |
| Recently engaged, exchange finished | Eligible short brain digest after the gates above | One grouped update with access to originals |
| Explicitly away or locked, where lock evidence is available | Silent | Durable pending count and action items |
| Unknown, including startup without current-run activity | Silent | Same durable records, marked as awaiting attention |
| Returning through deliberate interaction | Respond normally to the human; no backlog burst | Catch-up count and an accessible action to review updates |

Use a tentative recent-engagement expiry of two minutes for the first local trial, distinct from the 120-second digest cooldown. Expiry means *unknown*, not proof that the person left. Clicking, typing or accepted speech refreshes evidence. Pointer movement alone must not cause an immediate spoken catch-up. Explicit away overrides activity until the user deliberately returns. Do not automatically disable microphone capture because there were no clicks; any listening disengagement must have a visible state and a deliberate wake path.

One optional quiet cue can indicate that a batch is waiting, but only after turn-taking cue behavior has been settled. It must have a different meaning and sound from the tentative/end-of-turn cues. Suppress it while the user speaks and while away; play at most once for a batch, never once per event. Always provide a visual equivalent and independent sound enablement/volume. Start with visual-only notification feedback if these sounds are not yet distinguishable.

For absent reporting, use the existing pending-work surfaces and a grouped, optional native desktop notice. Do not add email, phone, third-party push or other outbound integrations to this fix. Native notifications are best-effort because OS permissions and Focus can suppress them; they do not prove that the human read anything. The connected-client count is transport state, not presence.

## Durability and multiple nodes

The existing arrival presentation lease should continue to prevent different nodes announcing the same team event. Select an eligible engaged node before claiming; do not hold an announcement lease through an indefinite away period. If none is eligible, retain pending visual attention without speech.

Separate `recorded`, `shown`, `audio_started`, `audio_completed`, `read`, and `dismissed` meanings. The current arrival callback returns no playback receipt; `_announce` completes the announcement after the callback returns. Introduce an explicit presentation outcome instead of assuming that a scheduled utterance was heard. Do not silently change team-server lease semantics: preserve its existing contract and add local modality/outcome bookkeeping where appropriate. A visual presentation can be completed as visual without claiming audio completion.

On restart, reconnect or playback failure, restore the pending records and recompute eligibility. Do not promote unread backlog to URGENT or concatenate it into speech. Source items already read, dismissed, superseded or resolved elsewhere should leave the digest candidate set. An interrupted digest must retain the items that were not actually presented. Exactly-once human perception cannot be guaranteed across a crash; durable receipts and lease ownership reduce repetition without marking work read merely because audio played.

## Implementation sequence

1. Add shared attention records/admission policy using the repository's existing local database and migrations. Preserve existing source records and identifiers. Establish event-to-attention classification and a single clock-driven eligibility evaluator.
2. Route all routine server announcement call sites and arrival callbacks through admission. Remove their direct conversational speech; keep factual cards and status data. Explicit requested responses/readbacks retain their paths.
3. Integrate the turn-taking floor arbiter, activity revision and conservative unknown/away gating. Remove unread-backlog urgency escalation. Add visual catch-up and explicit away/return state.
4. Add bounded brain digest generation, untrusted-data handling, cancellation and playback outcome bookkeeping. Revalidate records and floor ownership after generation. No template fallback speech.
5. Adapt team arrival leasing to select an eligible node and record actual presentation modality without conflating read state. Add grouped optional native notices without duplicating spoken delivery.
6. Exercise local simulated clocks, speech/ASR delays, reconnects and multi-node claims before a live listening trial. Tune defaults from interruption and missed-update observations, not from shortening delays until output appears faster.

The first usable candidate should ship steps 1–4 together: routine arrivals become quiet immediately while the guarded digest restores useful awareness. Camera work is not a dependency.

## Required behavioral checks

- Arrivals spread more than three seconds apart form one digest candidate group, not separate speech requests.
- Human and agent events share the same cooldown and cannot bypass each other through a second helper.
- A user continuing during tentative or likely-end stages retains the floor; no digest audio begins.
- Speech starts during generation or TTS preparation; the revision check cancels stale output before playback.
- Delayed recognition and queued audio capture block unsolicited speech even when the last accepted text is old.
- Away/unknown, restart and reconnect retain items silently; deliberate return shows catch-up without a spoken backlog.
- An item changes or is read elsewhere while a digest is being prepared; stale text is regenerated or discarded.
- Brain/TTS/network failure preserves visible records without canned voice fallback or a tight retry loop.
- Two nodes compete for one arrival; one eligible presentation lease wins, and the ineligible node stays silent.
- Playback queued, started, interrupted and completed produce distinct outcomes; none changes message-read state.
- Locale applies to generated digest and static UI independently; payload text cannot inject instructions.

## Later: opt-in camera and hand presence

Treat camera-derived signals as additional local evidence, not as a prerequisite or authentication. A later prototype can supply person-present confidence and deliberate pause/resume gestures, with hysteresis so an obscured face or quick movement does not repeatedly change state. Preserve ordinary keyboard, pointer and voice alternatives.

Default processing should be local, explicitly enabled, visibly indicated and stopped when disabled. Retain only the minimum availability signal needed by policy; no frame archive or remote image upload is required for this purpose. Physical presence does not imply willingness to listen. A hand gesture may pause or resume listening but cannot approve outgoing messages or perform other consent-bound actions. This is a design direction, not a selected or benchmarked camera library.
