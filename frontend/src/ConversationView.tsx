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
import { conversation, turnKey, useRecordOpen, type Turn } from "./captions";
import { TurnWords } from "./CaptionBox";
import { CaptureList } from "./CaptureBox";
import { captureKind } from "./capture";
import { conversationChanged, fromRecord, getConversation, restoreCapture,
         STATUS_WORDS, type ConversationItem } from "./conversation";
import { useLiveRead } from "./liveRead";
import { SourcesChip } from "./SourcesChip";
import type { OrbState } from "./orb";
import { createStore } from "./store";
import type { AvailabilityReason, TargetRef } from "./uiContract";
import { bind, bindingProps } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, useUiTarget, type Availability, type CommandOutcome, type Socket } from "./uiRegistry";
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

// ── earlier pages ───────────────────────────────────────────────────────

/** One page of a list read newest first, as its reader gives it. */
export interface EarlierPage<T> { items: T[]; earlier: number | null }

interface Pages<T> { older: T[]; earlier: number | null }

/** Lists read newest page first, with earlier pages loaded above the
 *  latest on request (the history, an agent's thread, a transcript): what
 *  each has loaded, and where its next earlier page starts, by list key.
 *  A mounted list follows its latest page's cursor until an earlier page is
 *  loaded and lets its pages go when it unmounts; only `load`, which a
 *  list's earlier command runs, loads one, so a click and the brain load
 *  the same page. */
export function earlierPages<T>(read: (key: string, before: number) => Promise<EarlierPage<T>>) {
  const lists = createStore<ReadonlyMap<string, Pages<T>>>(new Map());
  const loading = new Set<string>();
  const put = (key: string, pages: Pages<T> | null) => {
    const next = new Map(lists.get());
    if (pages) next.set(key, pages); else next.delete(key);
    lists.set(next);
  };
  const none: Pages<T> = { older: [], earlier: null };
  const refuse = (status: CommandOutcome["status"], reason: string): CommandOutcome =>
    ({ status, effects: [], reason });
  return {
    /** A mounted list's pages; `latest` is its latest page's cursor
     *  (undefined until it is read). */
    use(key: string, latest: number | null | undefined): Pages<T> {
      const pages = useSyncExternalStore(lists.subscribe, () => lists.get().get(key));
      useEffect(() => {
        if (latest === undefined) return;
        const now = lists.get().get(key);
        if (!now || (!now.older.length && now.earlier !== latest)) put(key, { older: [], earlier: latest });
      }, [key, latest]);
      useEffect(() => () => put(key, null), [key]);
      return pages ?? none;
    },
    /** What the list has loaded above its latest page now. */
    get: (key: string): Pages<T> | undefined => lists.get().get(key),
    availability(key: string): Availability {
      const pages = lists.get().get(key);
      if (!pages) return { available: false, reason: "not-mounted" };
      return pages.earlier === null ? { available: false, reason: "no-entity" } : { available: true };
    },
    /** Load the list's next earlier page. */
    async load(key: string, signal: AbortSignal): Promise<CommandOutcome> {
      const pages = lists.get().get(key);
      if (!pages || pages.earlier === null) return refuse("unavailable", pages ? "no-entity" : "not-mounted");
      if (loading.has(key)) return refuse("busy", "not-loaded");
      loading.add(key);
      let page: EarlierPage<T>;
      try {
        page = await read(key, pages.earlier);
      } catch {
        return refuse("unavailable", "not-loaded");
      } finally {
        loading.delete(key);
      }
      if (signal.aborted) return refuse("cancelled", "superseded");
      // Gone, or moved meanwhile: this page no longer goes above it.
      if (lists.get().get(key) !== pages) return refuse("cancelled", "superseded");
      put(key, { older: [...page.items, ...pages.older], earlier: page.items.length ? page.earlier : null });
      return { status: "completed", effects: ["list.paged"] };
    },
    /** Read earlier pages, a few at most, until one holds an item `wanted`
     *  picks out (a revealer's bounded load): true once loaded, else why
     *  not. */
    async seek(key: string, wanted: (item: T) => boolean, signal: AbortSignal): Promise<true | AvailabilityReason> {
      const has = () => !!lists.get().get(key)?.older.some(wanted);
      for (let n = 0; n < SEEK_PAGES && !has(); n++) {
        const pages = lists.get().get(key);
        if (!pages) return "not-mounted";
        if (pages.earlier === null) return "entity-gone";
        if ((await this.load(key, signal)).status !== "completed") return "not-loaded";
      }
      return has() ? true : "not-loaded";
    },
  };
}

/** At most this many earlier pages are read to find one item. */
const SEEK_PAGES = 10;

/** Where every centre view's targets hang from: the main rail, entering
 *  at their right edge. */
export const CENTRE_SOCKET: Socket = { rail: "rail.main", side: "right" };

// ── the history's paging, and finding a record in it ───────────────────

const HISTORY = "conversation";
const historyPages = earlierPages<ConversationItem>((_, before) => getConversation(before));

registerCommand("conversation.earlier", {
  availability: () => historyPages.availability(HISTORY),
  execute: (_, { signal }) => historyPages.load(HISTORY, signal),
});

// A record not on the page yet is looked for further back, a page at a
// time, by its id; a live turn the page no longer has is gone.
registerRevealer("main.conversation", "conversationRecord", async (ref, { signal }) =>
  ref.key?.startsWith("r") ? historyPages.seek(HISTORY, (i) => `r${i.id}` === ref.key, signal) : "entity-gone");

const conversationRef = (kind: "conversation" | "conversationRecord" | "sources", key?: string): TargetRef =>
  ({ region: "main", view: "main.conversation", kind, ...(key === undefined ? {} : { key }) });

export function ConversationView() {
  const { turns, speaking: word } = useSyncExternalStore(conversation.subscribe, conversation.get);
  const voice = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  const { history, speaking, hearing } = placeTurns(turns, voice);
  const ui = useUiTarget({ ref: conversationRef("conversation"), label: "Conversation", socket: CENTRE_SOCKET });
  return (
    <div id="conversation" ref={ui.whole}>
      <History turns={history} partRef={ui.part} />
      <CaptureList view="main.conversation" />
      <SpeakingBox turn={speaking} word={word} partRef={ui.part("speaking")} />
      <HearingBox turn={hearing} partRef={ui.part("input")} />
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
    ...turns.map((t) => ({ at: t.at ?? 0, key: turnKey(t), turn: t })),
    ...shown.filter((i) => !(i.kind === "answer" && asked.has(i.message_id)))
      .map((i) => ({ at: i.at, key: `r${i.id}`, item: i,
                     answer: i.kind === "to_agent" && i.message_id != null
                       ? answers.get(i.message_id) : undefined,
                     restorable: i.detail?.capture_event === "cleared" && !restoredLater(i) })),
  ];
  return rows.sort((a, b) => a.at - b.at);
}

/** A part's callback ref, from a registered target (`useUiTarget`). */
type PartRef = (part: string) => (el: Element | null) => void;

function History({ turns, partRef }: { turns: Turn[]; partRef: PartRef }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const live = useLiveRead(() => getConversation(), [conversationChanged]);
  const { older, earlier } = historyPages.use(HISTORY, live.value ? live.value.earlier : undefined);
  const latest = live.value?.items ?? [];
  const newestIds = new Set(latest.map((i) => i.id));
  const items = [...older.filter((i) => !newestIds.has(i.id)), ...latest];
  const rows = historyRows(turns, items);
  const newest = rows[rows.length - 1];
  // Rolling: the newest turn is in view whenever one arrives.
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [newest?.key, newest?.turn?.sentences.length, newest?.item?.message?.status]);
  const history = partRef("history");
  return (
    <div className="conversation-history" aria-label="History"
         ref={(el) => { ref.current = el; history(el); }}>
      {earlier != null && <button type="button" className="history-earlier" ref={partRef("earlier")}
                                  {...bindingProps(bind("conversation.earlier", {}))}>Earlier</button>}
      {rows.map((r) => (r.item ? <RecordItem key={r.key} item={r.item} answer={r.answer}
                                             restorable={r.restorable} place={{ region: "main", view: "main.conversation", kind: "conversationRecord", key: r.key }} />
        : r.turn!.display
          ? <SourcesRow key={r.key} turn={r.turn!} />
          : <HistoryTurn key={r.key} turn={r.turn!} />))}
    </div>
  );
}

/** What the brain showed instead of saying it, as a history row. */
function SourcesRow({ turn }: { turn: Turn }) {
  const key = turnKey(turn);
  const ui = useUiTarget({ ref: conversationRef("sources", key), label: turn.display!.title, socket: CENTRE_SOCKET });
  return (
    <div className="history-turn jarvis kind-sources" ref={ui.whole}>
      <SourcesChip display={turn.display!} recordId={key} partRef={ui.part} />
    </div>
  );
}

/** An agent's name, opening its thread. */
export function AgentLink({ item, partRef }: { item: ConversationItem; partRef?: PartRef }) {
  if (!item.agent_name) return null;
  const ref = partRef?.("agent");
  if (!item.agent_session_id) return <span className="record-agent" ref={ref}>{item.agent_name}</span>;
  return (
    <button type="button" className="record-agent" ref={ref}
            {...bindingProps(bind("thread.open", { sessionId: item.agent_session_id }))}>
      {item.agent_name}
    </button>
  );
}

/** What a record is called when the brain is told what is on the page:
 *  its kind and its agent, never its words. */
function recordLabel(item: ConversationItem): string {
  const agent = item.agent_name ? ` ${item.agent_name}` : " an agent";
  switch (item.kind) {
    case "to_agent": return `Message to${agent}`;
    case "answer": return `Answer from${agent}`;
    case "from_agent": return `Message from${agent}`;
    case "notice": return "Notice";
    case "jarvis": return "JARVIS's words";
    default: return "Your words";
  }
}

/** Where a record is shown: the history's or a thread's presentation of it. */
export type RecordPlace = TargetRef & { kind: "conversationRecord" | "threadRecord"; key: string };

/** A record's open state and the control that opens and closes it. */
function useOpener(recordId: string, opens: boolean) {
  const open = useRecordOpen(recordId, opens);
  return { open, toggle: bindingProps(bind("conversation.record.expand", { recordId, open: !open })) };
}

/** One item from the record: what was said before the page opened, a
 *  message to an agent with its live status (opening to the answer), a
 *  message from an agent, or a notice. Drawn as text, and registered where
 *  it is shown (`place`) with its parts. */
export function RecordItem({ item, answer, restorable, place }: {
  item: ConversationItem; answer?: ConversationItem; restorable?: boolean; place: RecordPlace }) {
  const recordId = `r${item.id}`;
  const { open, toggle } = useOpener(recordId, item.kind === "to_agent" && !!answer);
  const [refused, setRefused] = useState<string | null>(null);
  const ui = useUiTarget({ ref: place, label: recordLabel(item), socket: CENTRE_SOCKET });
  const text = ui.part("text");
  if (item.kind === "to_agent") {
    const status = item.message?.status ?? "staged";
    return (
      <div className={`history-turn record-message status-${status}`} ref={ui.whole}>
        <div className="record-head">
          <span className="record-arrow" aria-hidden="true">→</span><AgentLink item={item} partRef={ui.part} />
          <button type="button" className={`record-status status-${status}`} ref={ui.part("expand")}
                  disabled={!answer} {...toggle}
                  title={answer ? (open ? "Hide the answer" : "Show the answer") : undefined}>
            {STATUS_WORDS[status] ?? status}{answer ? (open ? " ▴" : " ▾") : ""}
          </button>
        </div>
        <p className="record-text" ref={text}>{item.text}</p>
        {open && answer && <p className="record-answer" ref={ui.part("answer")}>{answer.text}</p>}
      </div>
    );
  }
  if (item.kind === "answer" || item.kind === "from_agent") {
    return (
      <div className={`history-turn record-from kind-${item.kind}`} ref={ui.whole}>
        <div className="record-head">
          <span className="record-arrow" aria-hidden="true">←</span><AgentLink item={item} partRef={ui.part} />
          <span className="record-label">{item.kind === "answer" ? "Answer" : "Message"}</span>
        </div>
        <p className="record-text" ref={text}>{item.text}</p>
      </div>
    );
  }
  if (item.kind === "notice" && item.detail?.capture_event === "cleared") {
    // A capture closed without being sent: Restore brings it back, unread.
    return (
      <div className="history-turn record-notice record-cleared" ref={ui.whole}>
        <span ref={text}>{item.text}</span>
        {restorable && <button type="button" className="record-restore" ref={ui.part("restore")}
                               onClick={async () => setRefused(await restoreCapture(item))}>
          Restore</button>}
        {refused && <span className="read-failure">{refused}</span>}
      </div>
    );
  }
  const whole = (el: Element | null) => { ui.whole(el); text(el); };
  if (item.kind === "notice") return <p className="history-turn record-notice" ref={whole}>{item.text}</p>;
  const speaker = item.kind === "jarvis" ? "jarvis" : "user";
  return <p className={`history-turn ${speaker} record-said kind-${item.kind}`} ref={whole}>{item.text}</p>;
}

/** A captured turn takes its capture kind's look in the history. */
function kindClass(turn: Turn): string {
  const kind = turn.capture ? captureKind(turn.capture) : null;
  return kind ? ` kind-${kind}` : "";
}

function HistoryTurn({ turn }: { turn: Turn }) {
  const ref = useRef<HTMLParagraphElement | null>(null);
  const [long, setLong] = useState(false);
  const key = turnKey(turn);
  const { open, toggle } = useOpener(key, long);
  const ui = useUiTarget({ ref: conversationRef("conversationRecord", key),
                           label: turn.speaker === "jarvis" ? "JARVIS's words" : "Your words", socket: CENTRE_SOCKET });
  // Measured unclamped: more than LONG_LINES lines makes it a long turn.
  useLayoutEffect(() => {
    const p = ref.current;
    if (!p || long) return;
    const line = parseFloat(getComputedStyle(p).lineHeight) || 20;
    if (p.scrollHeight > LONG_LINES * line + 1) setLong(true);
  }, [turn.sentences, long]);
  const clamped = long && !open;
  const text = ui.part("text");
  const expand = ui.part("expand");
  return (
    <p ref={(el) => { ref.current = el; ui.whole(el); text(el); if (long) expand(el); }}
       className={`history-turn ${turn.speaker}${clamped ? " clamped" : ""}${
         long ? " long" : ""}${kindClass(turn)}`}
       {...(long ? toggle : {})}
       title={long ? (open ? "Show less" : "Show all") : undefined}>
      <TurnWords turn={turn} speaking={null} partRef={ui.part} />
    </p>
  );
}

function SpeakingBox({ turn, word, partRef }: { turn: Turn | null;
                                                word: [number, number, number] | null;
                                                partRef: (el: Element | null) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
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
    <div className="conversation-speaking" aria-label="JARVIS is saying"
         ref={(el) => { ref.current = el; partRef(el); }}>
      {turn && <p className="caption-turn jarvis"><TurnWords turn={turn} speaking={word} /></p>}
    </div>
  );
}

function HearingBox({ turn, partRef }: { turn: Turn | null; partRef: (el: Element | null) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
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
    <div className="conversation-hearing" aria-label="You are saying"
         ref={(el) => { ref.current = el; partRef(el); }}
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
