# Messages are read by the brain: design

Branch `feature/brain-reads-messages`, from origin/main 8c3d6fd. Direction: `.agents/in-progress/brain-reads/request.md`. No code changes in this stage.

## 1. Reads that bypass the brain

The full list is the `APPROVED` table in `tests/test_speech_call_sites.py`, which fails any call to the voice outside it. These are the ones that read him something:

| Where (server.py unless named) | Route / tool | Fate |
|---|---|---|
| `read_aloud` → `_speak_message` (`speech.say(lead + text)`) | `POST /api/messages/{id}/read`, tool `read_aloud` | **Through the brain.** `_speak_message` and its `APPROVED` row are removed. |
| `api_capture_read` → `speech.read_aloud` | `POST /api/captures/{n}/read` | **Kept direct** (exact read-back). Adds a note: "Capture N to <recipient> was read back to him from its box, heard in full / cut short." |
| `tool_read_capture` (`read_into` / `read_aloud`) | tool `read_capture` | Kept direct. The brain already has the tool result, so no note. |
| `_perform_command`, `_perform_dialog` | staged by `run_command`, `answer_dialog` | Kept direct (exact read-back). Adds a note: what was read back and its outcome (ran, cancelled, failed). |
| `StoryTeller._tell` (`narrate`) via `_package_story` | Tell the story, `tell_package_story`, `open_inbox_package` | Kept direct. Today the brain hears only start and stop. Adds a note when the story stops, waits or ends: "Package P's story: slides 1–4 told (titles), now waiting at question Q / ended." The titles stay walled. |
| `SpeechScheduler.replay_last`, `voice_handler` | "say that again" | Kept direct. A replayed brain reply is already in the brain's context. A replayed read-back adds the read-back note. |
| `_say_notice` (language, update) | language route and phrase, `update_jarvis` | Already notes the brain. Unchanged. |

All of these notes go through the existing `_notes_for_brain`, which `_with_notes_for_brain` prefixes to the next turn. One helper, `_note_spoken(what, ident, heard)`, writes them so the wording lives in one place.

Clearing and compaction questions, the digest and `/api/tts-test` read him nothing he asked to hear, so they are out of scope.

## 2. The Inbox Read starts a brain turn

Today `take_utterance` is a closure inside `voice_handler`. Its body becomes a module-level function, `_take_his_turn(text, heard, kind, detail)`. The socket and the Read route both call it, so there is one path for clear and rotation answers, read-back answers (`_take_read_back_answers`) and `_handle_utterance`.

`POST /api/messages/{id}/read` does three things:
1. Checks the message with `_message_to_him`. It still returns 404 when the message is missing and 409 when there is no voice.
2. Sets `_on_message(id)`.
3. Calls `_take_his_turn("Read me message {id}.", None, ItemKind.TYPED, detail={"via": "read", "message": id})`, and returns `{"id": id, "asking": true}`.

The turn's origin is `user`. It appears in the Conversation as his own line, "Read me message 42.", with the id in its detail. His click answers any read-back that is waiting, the same as any utterance would. It is not a yes, so it approves nothing.

The tool `read_aloud` stops speaking. It resolves the message (section 3) and marks a session message read as it does today. It returns the sender, Work item, local time, carried package and the text, with the other person's words in the untrusted block. It also returns an instruction: read the words as written, then sum them up in one sentence.

The tool then calls `_read_to_him(_MESSAGE_READ, id)`, and adds the message's pending update from `conversation_log.update_of(item)`. `_handle_utterance` already passes `_turn_reads` to `_seen_once_heard` once the whole reply is heard. So a message is marked read and its update seen only when that turn's reply is heard in full. A reply that is cut off, skipped or failed leaves the message new.

If the brain is down, the turn fails and is shown with its reason. There is no direct fallback.

`jarvis_home/CLAUDE.md` (lines 279–303) and the tool description are updated to match.

## 3. The time resolver

`messages_api.resolve(spec: MessageSpec, *, now: datetime | None = None, tz: tzinfo | None = None) -> Resolved`

- `MessageSpec` holds `message`, `at`, `sender`, `work`, `new` and `next`.
- `Resolved` holds `id`, `candidates` and `why`.

`server._message_meant` becomes a thin wrapper over `resolve` that supplies `_message_on`. Every tool that takes a message goes through it: `read_aloud`, `reply`, `mark_message` (which today reads `args["message"]` directly), and `open_inbox_package` through `_carried_by`. Each gains `at`, `from` and `work`. Routes keep taking ids, and those ids pass through the same function.

**Timezone.** `tz` defaults to the Mac's local zone, `datetime.now().astimezone().tzinfo`. Each `item.at` is turned into local time with `datetime.fromtimestamp(at, tz)`. Tests pass `tz` and `now`.

**Formats.** All are matched to the minute:
- `15:42`, `3:42`, `3:42pm`, `3:42 p.m.`, `3.42 pm`
- Spanish `15:42 h`
- with `today` or `yesterday` (`hoy`, `ayer`) in front
- `2026-11-12 15:42`

**Matching:**
- A time with no date covers the last 24 hours.
- A 12-hour time with no am/pm matches both halves of the day.
- `sender` matches `spoken_words.fold` against the sender's one name, using `agent_directory.spoken_key`.
- `work` matches the Work item's id or title.
- `new` limits the match to messages in the NEW state.

**Ties.** Nothing is ever broken silently:
- One match resolves.
- Several matches return up to five, as "message 41 from Bridge at 15:42 about X", and the brain asks which one, in the same style as `_ask_which`.
- No match names the nearest message by minutes, with its id and time. Two equally near messages are both named.

`message_json` adds `local`, for example `"2026-11-12 15:42"`. `messages_to_him` shows it as "today 15:42" or "yesterday 15:42". Both forms are accepted by `resolve`.

## 4. Tests (focused only)

- `tests/test_message_resolve.py` (new):
  - every format listed above
  - am/pm ambiguity
  - the 24-hour window
  - date prefixes
  - filters by sender, Work item and NEW
  - one match, several, none, and the nearest
  - a pinned `tz` and `now`, including a DST edge
- `test_message_read_reply.py`:
  - Read records a TYPED item with `via=read` and the message id
  - Read calls `_handle_utterance` with that text
  - no `speech.say`
  - read and seen only after the reply is heard in full; a cut reply leaves the message new
  - 404 and 409 unchanged
- `test_speech_call_sites.py`: the `_speak_message` row is removed. A runtime test checks that Read reaches the voice only through `begin_turn`.
- `test_brain_page_parity.py`: Read and `read_aloud` share `_message_to_him` and `resolve`. `mark_message` and `open_inbox_package` accept `at`.
- Note tests: a capture read from its box, a command or keypress read-back, story progress and a replayed read-back each leave exactly one `_notes_for_brain` entry naming what was read and its id.
- `test_one_channel_round3.py`: "What's new?" then "read it" or "next" resolves as before.
