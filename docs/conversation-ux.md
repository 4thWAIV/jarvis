# Conversation

Conversation uses a single timeline for the user’s messages and JARVIS’s complete
written responses. Audio playback is independent: text appears as it is generated,
without waiting for the voice to read it. The orb, side panels, navigation, capture
approval flow and both appearance themes remain available in their existing places.

- JARVIS's words are in the accent blue and the user's sent words in the main
  text colour (white); words still being heard are white too, in the
  conversation control.
- While a reply is read aloud, the word being said glows as it does in a capture
  read-back (`SpokenWords.tsx`, one highlighter driven by `wordClock.ts`). The
  whole reply is shown from the start; the glow follows the voice through it and
  ends at **Skip to end**, Stop, or the end of the reading. A reply skipped or
  not read has none.
- Replies stay expanded and selectable. Headings, lists, emphasis and code use the
  dashboard’s existing text-node Markdown renderer. Source links retain their
  separate expandable Sources control.
- The conversation control is the composer's heading row
  (`ConversationControl.tsx`), one grid in every state, so it never changes
  height (67 px with its rule when the composer is 560 px or wider, 99 px
  below): an activity light, the state's title and "Message JARVIS" on the
  first line, 15 px under the composer's top edge; the detail (his words
  with their newest end in view, or JARVIS's or an error's sentence, one
  line, whole in its tooltip) 12 px under it; and the buttons at the
  detail's right end, or on a row of their own when narrow. Its states come
  from one model (`controlState.ts`): words being heard ("You are saying"),
  his turn not sent yet (gathering, the three pause stages, commit pending,
  stalled), JARVIS thinking or speaking (**Skip to end**, or **Escape**,
  stops that reply’s reading immediately; the written response continues to
  completion, and model generation is not cancelled), and two at once,
  called out in yellow ("Overlap: JARVIS is reading", "· your words are
  waiting", then his words). Captures and other announcements retain the
  existing Stop/hush semantics.
- The conversation's failures are the control's too, in red (`--ui-error`):
  a reply failed or cancelled and why, not all read aloud, Reconnecting
  (with what the lost connection cut), a typed message refused while not
  connected, his words not sent or not heard, the microphone missing,
  blocked or taken by another page, no audio from the mic, History
  unavailable (with Retry) and audio waiting for a click. The light is red
  and still, or lit one bar at a time while JARVIS tries again by itself. A
  live state keeps the control; an error shows only when it would otherwise
  be empty. A one-off error stays until he speaks or sends again; a lasting
  one stays while it holds and comes back after a live state. A typed send
  refused wins over Reconnecting; of other errors at once, the newest shows.
  History keeps its notes under the reply, and the health dot and
  Diagnostics keep theirs.
- The composer has a visible Send button. Enter sends; Shift+Enter inserts a line.
  IME composition does not submit. Long drafts scroll inside the editor, without
  covering the timeline or Send control.
- Unsent drafts survive reloads and view changes in the same browser tab using
  session storage. A disconnected submission retains its draft, and the
  conversation control says why it was not sent. Sending uses the existing WebSocket submission contract; this
  change does not add a durable server acknowledgment or an offline outbox.
- The timeline follows the newest words, as a reply grows and as the bottom box
  changes. While a reply is read aloud, following keeps the word being read in
  view instead: a reply that fits stays at the bottom as before, but one taller
  than the timeline is scrolled along with the reading, the word kept above the
  lower quarter of the box, never carried past by words arriving or the box
  changing size, and the timeline moves up only as far as the word itself when
  the word is above it. A pause between sentences, while the next one is still
  on its way, is not the end: the timeline stays where it is. When the reading
  has truly ended (its last chunk played and the server's speech idle), or is
  skipped or stopped, the timeline settles at the bottom. Only the user's own scrolling up (wheel, touch,
  keys, the scrollbar) pauses following, and then the reading never moves the
  timeline; a scroll the page makes, or the content or the box changing size,
  never does. Scrolling back to the bottom, or **Jump to latest**, follows
  again. One hook in `MessageList.tsx` owns that decision.
- The bottom box (captures and the composer with its control) is
  as tall as what it holds and never scrolls as a whole; the timeline gives up
  its height first, and a capture is never covered. History failures show Retry in the control, and earlier-page failures can be
  retried. Loaded records remain available when the server’s recent page rolls on.

## Review previews

Fictional messages rendered by the actual frontend:

- [Classic Refined](images/conversation-playback-classic.png)
- [Graphite](images/conversation-playback-graphite.png)

Regenerate with `scripts/capture_docs.py --theme classic` or `--theme graphite`.

## Components and protocol

`frontend/src/conversation/` contains Composer, ConversationControl (with its
state model, controlState.ts), ChatMessage, ReplyText, MessageList and RecordItem. ConversationView composes those with the existing
CaptureList. Styling is scoped to Conversation and uses the shared theme tokens.
No component framework or new runtime dependency is added.

The voice socket emits cumulative `reply {utt, at, text, done}` snapshots, coalesced
at 50 ms, independently of synthesis/playback. Final successful conversation records
carry `detail.utt` and `detail.reply_started_at`. User records carry
`detail.client_turn_id` when supplied. The timeline reconciles live turns with the
durable record; it no longer excludes records newer than page load.

`skip_to_end {utt}` cancels only a conversation reply’s narration. The scheduler
broadcasts `reading_skipped {utt}` and retires its audio without claiming it was
played. Clients discard late/queued audio for that utterance. Connection epochs and
stable reply timestamps distinguish IDs reused after a backend restart. Partial
replies interrupted by a disconnect remain visible and are marked as partial.

## Verification

Browser journeys use the real built frontend with synthetic WebSocket/API fixtures
and silent audio, never physical microphone input or an AI provider. They cover
multiline/IME and button submission, draft recovery, full long replies, independent
text and audio, skip/late audio, capture Stop, backend ID reuse, record reconciliation,
scrollback, history recovery, both themes and narrower viewports. Existing workspace,
caption, mute and source-display checks cover surrounding behavior. Backend tests
exercise generation completion and errors, skipped synthesis and pending replies,
truthful acknowledgments, unrelated announcements and backpressure.

These checks do not establish human acceptance of the visual design, live model
response quality, or the quality of a physical speaker/microphone conversation.
