/**
 * The Conversation view of the centre area, anchored to the bottom, top to
 * bottom:
 *
 *   - the history: every finished turn (JARVIS's in blue, Tim's in white), in
 *     all the height left up to the header, scrolling. A turn longer than
 *     four lines shows its first two and an ellipsis, and opens to full size
 *     when clicked. What the brain showed instead of saying it (its sources)
 *     is a collapsed yellow Sources item after the words it goes with;
 *   - the open captures (CaptureBox.tsx);
 *   - the blue box: four lines, what JARVIS is saying now, scrolled so the
 *     line being spoken stays in view;
 *   - the white box, at the very bottom: what Tim is saying now, as it is
 *     heard, one line growing to four. Clicked, it takes typing instead:
 *     Enter sends what he typed as if he had said it, Shift+Enter starts a
 *     new line.
 *
 * The history is the conversation's record (conversation.ts) as well as the
 * live captions: what was said before the page opened, and every message to
 * an agent with its status as it moves (Queued, Sent, Delivered, Working on
 * it, Answered), opening to the agent's answer. An agent's name opens that
 * agent's thread in the centre area. "Earlier" at the top reads further back.
 *
 * What is live is in the two boxes. Tim's finished words stay in the white
 * box until the brain answers: they go to a capture if it makes one of them
 * (and the history shows a collapsed item for them), and otherwise into the
 * history when JARVIS starts to reply. JARVIS's reply moves into the history
 * once he has finished saying it.
 */

import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { conversation, type Turn } from "./captions";
import { TurnWords } from "./CaptionBox";
import { CaptureList } from "./CaptureBox";
import { captureKind } from "./capture";
import { conversationChanged, fromRecord, getConversation, openThread, restoreCapture,
         STATUS_WORDS, type ConversationItem } from "./conversation";
import { useLiveRead } from "./liveRead";
import { SourcesChip } from "./SourcesChip";
import type { OrbState } from "./orb";
import { sendTyped, voiceState } from "./voiceStatus";

// A history turn longer than this many lines is shown as its first two.
const LONG_LINES = 4;

/** Which turns are live and which are history. */
export function placeTurns(turns: Turn[], voice: OrbState):
    { history: Turn[]; speaking: Turn | null; hearing: Turn | null } {
  // Something shown after the words being spoken goes into the history at
  // once; the words stay in their box until they are said.
  const last = [...turns].reverse().find((t) => !t.display);
  const speaking = last && last.speaker === "jarvis" && voice === "speaking" ? last : null;
  const hearing = last && last.speaker === "user" && !last.capture ? last : null;
  const history = turns.filter((t) => t !== speaking && t !== hearing);
  return { history, speaking, hearing };
}

export function ConversationView() {
  const { turns, speaking: word } = useSyncExternalStore(conversation.subscribe, conversation.get);
  const voice = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  const { history, speaking, hearing } = placeTurns(turns, voice);
  return (
    <div id="conversation">
      <History turns={history} />
      <CaptureList />
      <SpeakingBox turn={speaking} word={word} />
      <HearingBox turn={hearing} />
    </div>
  );
}

type Row = { at: number; key: string; turn?: Turn; item?: ConversationItem;
             answer?: ConversationItem; restorable?: boolean };

/** The live turns and the record's items, in the order they happened. An
 *  agent's answer is drawn inside the message it answers when both are here.
 *  `everything`: every record item (an agent's thread), not only those the
 *  live captions do not draw. */
export function historyRows(turns: Turn[], items: ConversationItem[],
                            everything = false): Row[] {
  const shown = everything ? items : items.filter(fromRecord);
  const asked = new Set(shown.filter((i) => i.kind === "to_agent").map((i) => i.message_id));
  const answers = new Map(shown.filter((i) => i.kind === "answer" && i.message_id != null)
    .map((i) => [i.message_id, i] as const));
  // A cleared capture can be restored until a later notice says it was.
  const restoredLater = (i: ConversationItem) => items.some((j) => j.id > i.id
    && j.capture_id === i.capture_id && j.detail?.capture_event === "restored");
  const rows: Row[] = [
    ...turns.map((t) => ({ at: t.at ?? 0, key: `t${t.id}`, turn: t })),
    ...shown.filter((i) => !(i.kind === "answer" && asked.has(i.message_id)))
      .map((i) => ({ at: i.at, key: `r${i.id}`, item: i,
                     answer: i.kind === "to_agent" && i.message_id != null
                       ? answers.get(i.message_id) : undefined,
                     restorable: i.detail?.capture_event === "cleared" && !restoredLater(i) })),
  ];
  return rows.sort((a, b) => a.at - b.at);
}

function History({ turns }: { turns: Turn[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const live = useLiveRead(() => getConversation(), [conversationChanged]);
  const [older, setOlder] = useState<ConversationItem[]>([]);
  const [earlier, setEarlier] = useState<number | null | undefined>(undefined);
  useEffect(() => {
    if (earlier === undefined && live.value) setEarlier(live.value.earlier);
  }, [live.value, earlier]);
  const latest = live.value?.items ?? [];
  const newestIds = new Set(latest.map((i) => i.id));
  const items = [...older.filter((i) => !newestIds.has(i.id)), ...latest];
  const rows = historyRows(turns, items);
  const newest = rows[rows.length - 1];
  // Rolling: the newest turn is in view whenever one arrives.
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [newest?.key, newest?.turn?.sentences.length, newest?.item?.message?.status]);
  async function loadEarlier() {
    if (earlier == null) return;
    const page = await getConversation(earlier).catch(() => null);
    if (!page) return;
    setOlder((o) => [...page.items, ...o]);
    setEarlier(page.items.length ? page.earlier : null);
  }
  return (
    <div className="conversation-history" ref={ref} aria-label="History">
      {earlier != null && <button type="button" className="history-earlier" onClick={loadEarlier}>
        Earlier</button>}
      {rows.map((r) => (r.item ? <RecordItem key={r.key} item={r.item} answer={r.answer}
                                             restorable={r.restorable} />
        : r.turn!.display
          ? <div key={r.key} className="history-turn jarvis kind-sources"><SourcesChip display={r.turn!.display} /></div>
          : <HistoryTurn key={r.key} turn={r.turn!} />))}
    </div>
  );
}

/** An agent's name, opening its thread. */
export function AgentLink({ item }: { item: ConversationItem }) {
  if (!item.agent_name) return null;
  if (!item.agent_session_id) return <span className="record-agent">{item.agent_name}</span>;
  return (
    <button type="button" className="record-agent"
            onClick={() => openThread.set({ sessionId: item.agent_session_id!, name: item.agent_name })}>
      {item.agent_name}
    </button>
  );
}

/** One item from the record: what was said before the page opened, a
 *  message to an agent with its live status (opening to the answer), a
 *  message from an agent, or a notice. Drawn as text. */
export function RecordItem({ item, answer, restorable }: {
  item: ConversationItem; answer?: ConversationItem; restorable?: boolean }) {
  const [open, setOpen] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  if (item.kind === "to_agent") {
    const status = item.message?.status ?? "staged";
    return (
      <div className={`history-turn record-message status-${status}`}>
        <div className="record-head">
          <span className="record-arrow" aria-hidden="true">→</span><AgentLink item={item} />
          <button type="button" className={`record-status status-${status}`}
                  disabled={!answer} onClick={() => setOpen(!open)}
                  title={answer ? (open ? "Hide the answer" : "Show the answer") : undefined}>
            {STATUS_WORDS[status] ?? status}{answer ? (open ? " ▴" : " ▾") : ""}
          </button>
        </div>
        <p className="record-text">{item.text}</p>
        {open && answer && <p className="record-answer">{answer.text}</p>}
      </div>
    );
  }
  if (item.kind === "answer" || item.kind === "from_agent") {
    return (
      <div className={`history-turn record-from kind-${item.kind}`}>
        <div className="record-head">
          <span className="record-arrow" aria-hidden="true">←</span><AgentLink item={item} />
          <span className="record-label">{item.kind === "answer" ? "Answer" : "Message"}</span>
        </div>
        <p className="record-text">{item.text}</p>
      </div>
    );
  }
  if (item.kind === "notice" && item.detail?.capture_event === "cleared") {
    // A capture closed without being sent: Restore brings it back, unread.
    return (
      <div className="history-turn record-notice record-cleared">
        <span>{item.text}</span>
        {restorable && <button type="button" className="record-restore"
                               onClick={async () => setRefused(await restoreCapture(item))}>
          Restore</button>}
        {refused && <span className="read-failure">{refused}</span>}
      </div>
    );
  }
  if (item.kind === "notice") return <p className="history-turn record-notice">{item.text}</p>;
  const speaker = item.kind === "jarvis" ? "jarvis" : "user";
  return <p className={`history-turn ${speaker} record-said kind-${item.kind}`}>{item.text}</p>;
}

/** A captured turn takes its capture kind's look in the history. */
function kindClass(turn: Turn): string {
  const kind = turn.capture ? captureKind(turn.capture) : null;
  return kind ? ` kind-${kind}` : "";
}

function HistoryTurn({ turn }: { turn: Turn }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [long, setLong] = useState(false);
  const [open, setOpen] = useState(false);
  // Measured unclamped: more than LONG_LINES lines makes it a long turn.
  useLayoutEffect(() => {
    const p = ref.current;
    if (!p || long) return;
    const line = parseFloat(getComputedStyle(p).lineHeight) || 20;
    if (p.scrollHeight > LONG_LINES * line + 1) setLong(true);
  }, [turn.sentences, long]);
  const clamped = long && !open;
  return (
    <p ref={ref} className={`history-turn ${turn.speaker}${clamped ? " clamped" : ""}${
         long ? " long" : ""}${kindClass(turn)}`}
       onClick={long ? () => setOpen(!open) : undefined}
       title={long ? (open ? "Show less" : "Show all") : undefined}>
      <TurnWords turn={turn} speaking={null} />
    </p>
  );
}

function SpeakingBox({ turn, word }: { turn: Turn | null;
                                       word: [number, number, number] | null }) {
  const ref = useRef<HTMLDivElement>(null);
  // The line being spoken stays in view, with the line before it above.
  useLayoutEffect(() => {
    const box = ref.current;
    const now = box?.querySelector<HTMLElement>(".spoken-word.now");
    if (!box || !now) return;
    const line = parseFloat(getComputedStyle(box).lineHeight) || 20;
    const top = now.offsetTop;           // the box is the word's offset parent
    if (top < box.scrollTop || top + line > box.scrollTop + box.clientHeight) {
      box.scrollTop = Math.max(0, top - line);
    }
  }, [word, turn?.id]);
  return (
    <div className="conversation-speaking" ref={ref} aria-label="JARVIS is saying">
      {turn && <p className="caption-turn jarvis"><TurnWords turn={turn} speaking={word} /></p>}
    </div>
  );
}

function HearingBox({ turn }: { turn: Turn | null }) {
  const ref = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  // What was heard shows until he starts typing; a draft keeps the box.
  const heard = turn && !typing && !draft ? turn.sentences.join(" ") : null;
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [turn?.sentences, draft]);
  useLayoutEffect(() => {
    if (typing) input.current?.focus();
  }, [typing]);
  function send() {
    const text = draft.trim();
    if (!text) return;
    sendTyped.current(text);
    setDraft("");
    // Sent, his words wait in the box as spoken ones do.
    input.current?.blur();
  }
  return (
    <div className="conversation-hearing" ref={ref} aria-label="You are saying"
         onClick={() => setTyping(true)}>
      {heard !== null && <p className="caption-turn user">{heard}</p>}
      <textarea ref={input} className="hearing-input" rows={1} hidden={heard !== null}
                aria-label="Type instead of speaking" placeholder="Speak, or click to type"
                value={draft} onChange={(e) => setDraft(e.target.value)}
                onFocus={() => setTyping(true)} onBlur={() => setTyping(false)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    send();
                  }
                }} />
    </div>
  );
}
