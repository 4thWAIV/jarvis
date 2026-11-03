import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { type Turn } from "../captions";
import { captureKind } from "../capture";
import { TurnWords } from "../CaptionBox";
import { conversationChanged, getConversation, type ConversationItem } from "../conversation";
import { useLiveRead } from "../liveRead";
import { conversationJump } from "../voiceStatus";
import { ChatMessage } from "./ChatMessage";
import { RecordItem } from "./RecordItem";

type Row = { at: number; key: string; turn?: Turn; item?: ConversationItem;
  answer?: ConversationItem; restorable?: boolean };
const words = (s: string) => s.replace(/\s+/g, " ").trim();

/** Durable records are authoritative. Live turns bridge only the interval
 * before their record arrives; never discard new records by page-open time. */
export function historyRows(turns: Turn[], items: ConversationItem[], _everything = false): Row[] {
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
  const rows: Row[] = [
    ...pending.map(turn => ({ at: turn.at ?? 0, key: `t${turn.id}`, turn })),
    ...items.filter(i => !(i.kind === "answer" && asked.has(i.message_id)))
      .map(item => ({ at: item.at, key: `r${item.id}`, item,
        answer: item.kind === "to_agent" ? answers.get(item.message_id) : undefined,
        restorable: item.detail?.capture_event === "cleared" && !items.some(j => j.id > item.id
          && j.capture_id === item.capture_id && j.detail?.capture_event === "restored") })),
  ];
  return rows.sort((a, b) => a.at - b.at);
}

function merge(a: ConversationItem[], b: ConversationItem[]) {
  return [...new Map([...a, ...b].map(i => [i.id, i])).values()].sort((a, b) => a.id - b.id);
}

export function MessageList({ turns }: { turns: Turn[] }) {
  const live = useLiveRead(() => getConversation(), [conversationChanged]);
  const jumpSignal = useSyncExternalStore(conversationJump.subscribe, conversationJump.get);
  const [retained, setRetained] = useState<ConversationItem[]>([]);
  const [earlier, setEarlier] = useState<number | null | undefined>();
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [earlierError, setEarlierError] = useState("");
  const [following, setFollowing] = useState(true);
  const follow = useRef(true);
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  const items = merge(retained, live.value?.items ?? []);
  const rows = historyRows(turns, items);
  useEffect(() => {
    if (!live.value) return;
    setRetained(old => merge(old, live.value!.items));
    setEarlier(old => old === undefined ? live.value!.earlier : old);
  }, [live.value]);
  function jump() {
    follow.current = true; setFollowing(true);
    if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }
  useLayoutEffect(() => {
    const box = scroll.current;
    if (!box) return;
    if (prepend.current) {
      box.scrollTop = prepend.current.top + box.scrollHeight - prepend.current.height;
      prepend.current = null;
    } else if (follow.current) box.scrollTop = box.scrollHeight;
  }, [items.length, turns, loadingEarlier]);
  useEffect(() => { if (jumpSignal) jump(); }, [jumpSignal]);
  useEffect(() => {
    const resize = new ResizeObserver(() => {
      if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
    });
    if (scroll.current) resize.observe(scroll.current);
    if (content.current) resize.observe(content.current);
    return () => resize.disconnect();
  }, []);
  async function loadEarlier() {
    if (earlier == null || loadingEarlier) return;
    setLoadingEarlier(true); setEarlierError("");
    try {
      const page = await getConversation(earlier);
      if (scroll.current) prepend.current = { height: scroll.current.scrollHeight, top: scroll.current.scrollTop };
      follow.current = false; setFollowing(false);
      setRetained(old => merge(page.items, old));
      setEarlier(page.items.length ? page.earlier : null);
    } catch { setEarlierError("Couldn’t load earlier messages. Try again."); }
    finally { setLoadingEarlier(false); }
  }
  return <div className="conversation-thread">
    {live.error && <div className="conversation-read-error" role="status">
      <span>History is unavailable. Messages shown here are kept while we reconnect.</span>
      <button type="button" onClick={live.refresh}>Retry</button>
    </div>}
    <div className="conversation-history" ref={scroll} role="log" aria-live="off" aria-label="Conversation messages"
      onScroll={() => {
        const box = scroll.current!;
        follow.current = box.scrollHeight - box.scrollTop - box.clientHeight < 64;
        setFollowing(follow.current);
      }}>
      <div className="conversation-messages" ref={content}>
        {earlier != null && <div className="conversation-earlier"><button type="button" className="history-earlier"
          disabled={loadingEarlier} onClick={loadEarlier}>{loadingEarlier ? "Loading…" : "Load earlier messages"}</button></div>}
        {earlierError && <p className="read-failure" role="alert">{earlierError}</p>}
        {!live.value && !live.error && rows.length === 0 && <p className="conversation-empty" role="status">Loading conversation…</p>}
        {live.value && rows.length === 0 && <div className="conversation-empty"><span className="empty-mark" aria-hidden="true">J</span>
          <h2>What’s on your mind?</h2><p>Speak to JARVIS or start a conversation below.</p></div>}
        {rows.map(r => r.item ? <RecordItem key={r.key} item={r.item} answer={r.answer} restorable={r.restorable} />
          : r.turn!.display ? <div key={r.key} className="chat-sources"><TurnWords turn={r.turn!} speaking={null} /></div>
          : <ChatMessage key={r.key} speaker={r.turn!.speaker} at={r.turn!.at}
              text={r.turn!.text ?? r.turn!.sentences.join(" ")} kind={r.turn!.capture ? `kind-${captureKind(r.turn!.capture)}` : ""} pending={r.turn!.complete === false && !r.turn!.interrupted} interrupted={r.turn!.interrupted}>
              {r.turn!.capture ? <TurnWords turn={r.turn!} speaking={null} /> : undefined}
            </ChatMessage>)}
      </div>
    </div>
    {!following && <button type="button" className="conversation-jump" onClick={jump}>Jump to latest <span aria-hidden="true">↓</span></button>}
  </div>;
}
