import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { turnKey, type Turn } from "../captions";
import { type ConversationItem, type ConversationPage } from "../conversation";
import { useT } from "../locale";
import { bind, bindingProps } from "../uiNavigation";
import { dispatch } from "../uiRegistry";
import { conversationJump } from "../voiceStatus";

export type ConversationRow = { at: number; key: string; turn?: Turn; item?: ConversationItem;
  answer?: ConversationItem; restorable?: boolean };
const words = (s: string) => s.replace(/\s+/g, " ").trim();

/** Durable records are authoritative. Live turns bridge only the interval
 * before their record arrives; never discard new records by page-open time. */
export function historyRows(turns: Turn[], items: ConversationItem[], _everything = false): ConversationRow[] {
  const matched = new Set<number>();
  const pending = turns.filter(t => {
    if (t.display) return true;
    const record = items.find(i => !matched.has(i.id) && (
      (t.speaker === "jarvis" && i.kind === "jarvis" && t.utt != null && i.detail?.utt === t.utt
        && (i.detail.reply_started_at != null ? i.detail.reply_started_at === t.at : Math.abs(i.at - (t.at ?? 0)) < 30))
      || (t.speaker === "user" && (i.kind === "heard" || i.kind === "typed") && i.detail?.client_turn_id === t.id && Math.abs(i.at - (t.at ?? 0)) < 30
        && words(i.text) === words(t.sentences.join(" ")))
      || (t.capture && i.kind === "capture" && i.capture_id === t.capture.id)
      || ((t.speaker === "jarvis" ? i.kind === "jarvis" : i.kind === (t.typed ? "typed" : "heard"))
        && Math.abs(i.at - (t.at ?? 0)) < 30 && words(i.text) === words(t.text ?? t.sentences.join(" ")))
    ));
    if (record) matched.add(record.id);
    return !record;
  });
  const asked = new Set(items.filter(i => i.kind === "to_agent").map(i => i.message_id));
  const answers = new Map(items.filter(i => i.kind === "answer" && i.message_id != null).map(i => [i.message_id, i]));
  const rows: ConversationRow[] = [
    ...pending.map(turn => ({ at: turn.at ?? 0, key: turnKey(turn), turn })),
    ...items.filter(i => !(i.kind === "answer" && asked.has(i.message_id)))
      .map(item => ({ at: item.at, key: `r${item.id}`, item,
        answer: item.kind === "to_agent" ? answers.get(item.message_id) : undefined,
        restorable: item.detail?.capture_event === "cleared" && !items.some(j => j.id > item.id
          && j.capture_id === item.capture_id && j.detail?.capture_event === "restored") })),
  ];
  return rows.sort((a, b) => a.at - b.at);
}

/** Whether the list is laid out: a list kept hidden behind another centre
 *  view has no box, and scrolling it or deciding it follows would lose
 *  where it was left. */
const shown = (box: HTMLElement | null): box is HTMLElement => !!box && box.getClientRects().length > 0;

function merge(a: ConversationItem[], b: ConversationItem[]) {
  return [...new Map([...a, ...b].map(i => [i.id, i])).values()].sort((a, b) => a.id - b.id);
}

export function MessageList({ turns, older, earlier, live, partRef, renderRow }: {
  turns: Turn[];
  older: ConversationItem[];
  earlier: number | null;
  live: { value: ConversationPage | null; error: string | null; refresh: () => void };
  partRef: (part: string) => (el: Element | null) => void;
  renderRow: (row: ConversationRow) => ReactNode;
}) {
  const t = useT();
  const jumpSignal = useSyncExternalStore(conversationJump.subscribe, conversationJump.get);
  const [retained, setRetained] = useState<ConversationItem[]>([]);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [earlierError, setEarlierError] = useState(false);
  const [following, setFollowing] = useState(true);
  const follow = useRef(true);
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const items = merge(merge(older, retained), live.value?.items ?? []);
  const rows = historyRows(turns, items);
  useEffect(() => {
    if (live.value) setRetained(old => merge(old, live.value!.items));
  }, [live.value]);
  function jump() {
    follow.current = true;
    setFollowing(true);
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }
  useLayoutEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [items.length, turns]);
  useEffect(() => { if (jumpSignal) jump(); }, [jumpSignal]);
  useEffect(() => {
    const resize = new ResizeObserver(() => {
      if (follow.current && shown(scroll.current)) scroll.current.scrollTop = scroll.current.scrollHeight;
    });
    if (scroll.current) resize.observe(scroll.current);
    if (content.current) resize.observe(content.current);
    return () => resize.disconnect();
  }, []);
  async function loadEarlier() {
    follow.current = false;
    setFollowing(false);
    setLoadingEarlier(true);
    setEarlierError(false);
    try {
      const outcome = await dispatch("conversation.earlier", {}, { actor: "human" });
      setEarlierError(outcome.status !== "completed");
    } finally {
      setLoadingEarlier(false);
    }
  }
  const history = partRef("history");
  return <div className="conversation-thread">
    {live.error && <div className="conversation-read-error" role="status">
      <span>{t("main.conversation.historyUnavailable")}</span>
      <button type="button" data-ui-action="refresh" onClick={live.refresh}>{t("main.conversation.retry")}</button>
    </div>}
    <div className="conversation-history" role="log" aria-live="off"
      aria-label={t("main.conversation.messageHistory")} data-ui-action="scroll"
      ref={el => { scroll.current = el; history(el); }}
      onScroll={() => {
        const box = scroll.current!;
        // Hidden behind another centre view it measures nothing; whether it
        // follows stays as it was, so it comes back exactly as it was left.
        if (!shown(box)) return;
        follow.current = box.scrollHeight - box.scrollTop - box.clientHeight < 64;
        setFollowing(follow.current);
      }}>
      <div className="conversation-messages" ref={content}>
        {earlier != null && <div className="conversation-earlier"><button type="button" className="history-earlier"
          ref={partRef("earlier")} disabled={loadingEarlier}
          {...bindingProps(bind("conversation.earlier", {}))} onClick={loadEarlier}>
          {t(loadingEarlier ? "main.conversation.loading" : "main.conversation.loadEarlier")}</button></div>}
        {earlierError && <p className="read-failure" role="alert">{t("main.conversation.earlierFailed")}</p>}
        {!live.value && !live.error && rows.length === 0 && <p className="conversation-empty" role="status">
          {t("main.conversation.loading")}</p>}
        {live.value && rows.length === 0 && <div className="conversation-empty"><span className="empty-mark" aria-hidden="true">J</span>
          <h2>{t("main.conversation.emptyHeading")}</h2><p>{t("main.conversation.emptyHint")}</p></div>}
        {rows.map(renderRow)}
      </div>
    </div>
    {!following && <button type="button" className="conversation-jump" data-ui-action="scroll" onClick={jump}>
      {t("main.conversation.jumpLatest")} <span aria-hidden="true">↓</span></button>}
  </div>;
}
