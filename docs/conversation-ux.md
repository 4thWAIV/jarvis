# Conversation

Conversation uses a single timeline for the user’s messages and JARVIS’s complete
written responses. Audio playback is independent: text appears as it is generated,
without waiting for the voice to read it. The orb, side panels, navigation, capture
approval flow and both appearance themes remain available in their existing places.

- JARVIS's words are in the accent blue and the user's sent words in the main
  text colour (white); words still being heard, not yet sent, stay grey.
- While a reply is read aloud, the word being said glows as it does in a capture
  read-back (`SpokenWords.tsx`, one highlighter driven by `wordClock.ts`). The
  whole reply is shown from the start; the glow follows the voice through it and
  ends at **Skip to end**, Stop, or the end of the reading. A reply skipped or
  not read has none.
- Replies stay expanded and selectable. Headings, lists, emphasis and code use the
  dashboard’s existing text-node Markdown renderer. Source links retain their
  separate expandable Sources control.
- The old fixed caption rectangle becomes a compact playback bar while JARVIS is
  speaking. **Skip to end**, or **Escape**, stops that reply’s reading immediately;
  the written response continues to completion. It does not cancel model generation.
  Captures and other announcements retain the existing Stop/hush semantics.
- The composer has a visible Send button. Enter sends; Shift+Enter inserts a line.
  IME composition does not submit. Long drafts scroll inside the editor, without
  covering the timeline or Send control.
- Unsent drafts survive reloads and view changes in the same browser tab using
  session storage. A disconnected submission retains its draft and explains why
  it was not sent. Sending uses the existing WebSocket submission contract; this
  change does not add a durable server acknowledgment or an offline outbox.
- The timeline follows the newest words, as a reply grows and as the bottom box
  changes. Only the user's own scrolling up (wheel, touch, keys, the scrollbar)
  pauses following; a scroll the page makes, or the content or the box changing
  size, never does. Scrolling back to the bottom, or **Jump to latest**, follows
  again. One hook in `MessageList.tsx` owns that decision.
- The bottom box (captures, playback bar, the turn being heard, the composer) is
  as tall as what it holds and never scrolls as a whole; the timeline gives up
  its height first, and a capture is never covered. History failures show Retry, and earlier-page failures can be
  retried. Loaded records remain available when the server’s recent page rolls on.

## Review previews

Fictional messages rendered by the actual frontend:

- [Classic Refined](images/conversation-playback-classic.png)
- [Graphite](images/conversation-playback-graphite.png)

Regenerate with `scripts/capture_docs.py --theme classic` or `--theme graphite`.

## Components and protocol

`frontend/src/conversation/` contains Composer, ChatMessage, ReplyText, MessageList,
PlaybackBar and RecordItem. ConversationView composes those with the existing
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
