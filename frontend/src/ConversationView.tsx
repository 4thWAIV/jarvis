import { useEffect, useState, useSyncExternalStore } from "react";
import { conversation, turnKey, useRecordOpen, type Turn } from "./captions";
import { TurnWords } from "./CaptionBox";
import { CaptureList } from "./CaptureBox";
import { captureKind } from "./capture";
import { conversationChanged, getConversation, restoreCapture,
         statusWords, type ConversationItem } from "./conversation";
import { useLiveRead } from "./liveRead";
import { formatDateTime, SaidText, useT, type Said, type Translate } from "./locale";
import { SourcesChip } from "./SourcesChip";
import { createStore } from "./store";
import type { AvailabilityReason, TargetRef } from "./uiContract";
import { bind, bindingProps } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, useUiTarget, type Availability, type CommandOutcome, type Socket } from "./uiRegistry";
import { MessageList, historyRows, type ConversationRow } from "./conversation/MessageList";
import { ChatMessage } from "./conversation/ChatMessage";
import { Composer } from "./conversation/Composer";
import { PlaybackBar } from "./conversation/PlaybackBar";
import { VoiceTurnBar } from "./conversation/VoiceTurnBar";
import "./conversation/conversation.css";

export { historyRows };

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

const conversationRef = <K extends "conversation" | "conversationRecord" | "sources">(kind: K, key?: string)
  : TargetRef & { view: "main.conversation"; kind: K } =>
  ({ region: "main", view: "main.conversation", kind, ...(key === undefined ? {} : { key }) });

/** A part's callback ref, from a registered target (`useUiTarget`). */
type PartRef = (part: string) => (el: Element | null) => void;

export function ConversationView() {
  const { turns } = useSyncExternalStore(conversation.subscribe, conversation.get);
  const hearing = [...turns].reverse().find(turn => turn.speaker === "user" && turn.open);
  const t = useT();
  const ui = useUiTarget({ ref: conversationRef("conversation"), label: t("tabs.main.conversation"), socket: CENTRE_SOCKET });
  return <section id="conversation" ref={ui.whole} aria-label={t("tabs.main.conversation")}>
    <History turns={turns.filter(turn => !turn.open)} partRef={ui.part} />
    <div className="conversation-dock">
      <div className="conversation-captures"><CaptureList view="main.conversation" /></div>
      <PlaybackBar partRef={ui.part("speaking")} />
      {hearing && <div className="conversation-interim" role="status">
        <span>{t("main.conversation.hearing")}</span><p>{hearing.sentences.join(" ")}</p>
      </div>}
      <VoiceTurnBar partRef={ui.part("turn")} />
      <Composer partRef={ui.part("input")} />
    </div>
  </section>;
}

function History({ turns, partRef }: { turns: Turn[]; partRef: PartRef }) {
  const live = useLiveRead(() => getConversation(), [conversationChanged]);
  const { older, earlier } = historyPages.use(HISTORY, live.value?.earlier);
  return <MessageList turns={turns} older={older} earlier={earlier} live={live}
    partRef={partRef} renderRow={renderHistoryRow} />;
}

function renderHistoryRow(row: ConversationRow) {
  if (row.item) return <RecordItem key={row.key} item={row.item} answer={row.answer}
    restorable={row.restorable} place={{ region: "main", view: "main.conversation", kind: "conversationRecord", key: row.key }} />;
  if (!row.turn) return null;
  return row.turn.display ? <SourcesRow key={row.key} turn={row.turn} />
    : <HistoryTurn key={row.key} turn={row.turn} />;
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
function recordLabel(t: Translate, item: ConversationItem): string {
  const agent = item.agent_name ?? t("main.record.anAgent");
  switch (item.kind) {
    case "to_agent": return t("main.record.messageTo", { agent });
    case "answer": return t("main.record.answerFrom", { agent });
    case "from_agent": return t("main.record.messageFrom", { agent });
    case "notice": return t("main.record.notice");
    case "jarvis": return t("main.record.jarvis");
    default: return t("main.record.yours");
  }
}

/** Where a record is shown: the history's or a thread's presentation of it. */
export type RecordPlace = TargetRef & { key: string } & (
  { view: "main.conversation"; kind: "conversationRecord" } | { view: "main.thread"; kind: "threadRecord" });

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
  const [refused, setRefused] = useState<Said | null>(null);
  const t = useT();
  const ui = useUiTarget({ ref: place, label: recordLabel(t, item), socket: CENTRE_SOCKET });
  const text = ui.part("text");
  if (item.kind === "to_agent") {
    const status = item.message?.status ?? "staged";
    return (
      <div className={`history-turn record-message status-${status}`} ref={ui.whole}>
        <div className="record-head">
          <span className="record-arrow" aria-hidden="true">→</span><AgentLink item={item} partRef={ui.part} />
          <button type="button" className={`record-status status-${status}`} ref={ui.part("expand")}
                  disabled={!answer} {...toggle}
                  title={answer ? (open ? t("main.conversation.hideAnswer") : t("main.conversation.showAnswer")) : undefined}>
            {statusWords(status)}{answer ? (open ? " ▴" : " ▾") : ""}
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
          <span className="record-label">{item.kind === "answer" ? t("main.conversation.answer") : t("main.conversation.message")}</span>
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
        {restorable && <button data-ui-action="domain" type="button" className="record-restore" ref={ui.part("restore")}
                               onClick={async () => setRefused(await restoreCapture(item))}>
          {t("main.conversation.restore")}</button>}
        {refused && <SaidText said={refused} className="read-failure" />}
      </div>
    );
  }
  const whole = (el: Element | null) => { ui.whole(el); text(el); };
  if (item.kind === "notice") return <p className="history-turn record-notice" ref={whole}>{item.text}</p>;
  const speaker = item.kind === "jarvis" ? "jarvis" : "user";
  return <ChatMessage speaker={speaker} at={item.at} text={item.text}
    kind={`record-said kind-${item.kind}`} wholeRef={ui.whole} textRef={text} />;
}

/** A captured turn takes its capture kind's look in the history. */
function kindClass(turn: Turn): string {
  const kind = turn.capture ? captureKind(turn.capture) : null;
  return kind ? ` kind-${kind}` : "";
}


const OUTCOME_NOTE = { failed: "main.conversation.replyFailed",
                       cancelled: "main.conversation.replyCancelled" } as const;
const READING_NOTE = { skipped: "main.conversation.readingSkipped",
                       stopped: "main.conversation.readingStopped",
                       failed: "main.conversation.readingFailed" } as const;

/** Why a reply failed, in the interface's language. */
function failureNote(failure: NonNullable<Turn["failure"]>, t: Translate): string {
  if (failure.reason === "rate_limited" && failure.resetsAt) {
    return t("main.conversation.replyReason.rate_limited_until",
             { time: formatDateTime(failure.resetsAt * 1000, { hour: "numeric", minute: "2-digit" }) });
  }
  return t(`main.conversation.replyReason.${failure.reason}`);
}

/** How a live JARVIS turn ended, when not complete and heard: a reply the
 *  provider did not finish, and a reading not heard in full. */
function turnNotes(turn: Turn, t: Translate): string[] {
  const notes: string[] = [];
  if (turn.complete && turn.outcome && turn.outcome !== "succeeded") notes.push(t(OUTCOME_NOTE[turn.outcome]));
  if (turn.complete && turn.failure) notes.push(failureNote(turn.failure, t));
  if (turn.reading) notes.push(t(READING_NOTE[turn.reading]));
  return notes;
}

function HistoryTurn({ turn }: { turn: Turn }) {
  const t = useT();
  const key = turnKey(turn);
  const ui = useUiTarget({ ref: conversationRef("conversationRecord", key),
    label: turn.speaker === "jarvis" ? t("main.record.jarvis") : t("main.record.yours"), socket: CENTRE_SOCKET });
  return <ChatMessage speaker={turn.speaker} at={turn.at}
    text={turn.text ?? turn.sentences.join(" ")} kind={kindClass(turn)}
    pending={turn.complete === false && !turn.interrupted} interrupted={turn.interrupted}
    notes={turnNotes(turn, t)}
    wholeRef={ui.whole} textRef={ui.part("text")}>
    {turn.capture ? <TurnWords turn={turn} speaking={null} partRef={ui.part} /> : undefined}
  </ChatMessage>;
}
