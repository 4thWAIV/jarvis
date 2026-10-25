/**
 * One agent's thread: everything sent to it and from it, on record, oldest
 * at the top (conversation.ts). The centre area's Thread view, opened by
 * clicking an agent's name in the Conversation history; closing it goes back
 * to Conversation. Each message shows its status as it is now and opens to
 * the agent's answer, so he can see where things stand and pick it up.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { historyRows, RecordItem } from "./ConversationView";
import { conversationChanged, getThread, openThread, type ConversationItem } from "./conversation";
import { useLiveRead } from "./liveRead";

export function AgentThread({ sessionId, name }: { sessionId: string; name: string | null }) {
  const live = useLiveRead(() => getThread(sessionId), [conversationChanged]);
  const [older, setOlder] = useState<ConversationItem[]>([]);
  const [earlier, setEarlier] = useState<number | null | undefined>(undefined);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (earlier === undefined && live.value) setEarlier(live.value.earlier);
  }, [live.value, earlier]);
  const latest = live.value?.items ?? [];
  const ids = new Set(latest.map((i) => i.id));
  // The whole thread is the record's, live or not: every item is shown.
  const items = [...older.filter((i) => !ids.has(i.id)), ...latest];
  const rows = historyRows([], items, true);
  const agent = live.value?.agent;
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [latest.length]);
  async function loadEarlier() {
    if (earlier == null) return;
    const page = await getThread(sessionId, earlier).catch(() => null);
    if (!page) return;
    setOlder((o) => [...page.items, ...o]);
    setEarlier(page.items.length ? page.earlier : null);
  }
  return (
    <div id="agent-thread">
      <div className="thread-header">
        <h3>{agent?.name ?? name ?? "Agent"}</h3>
        {agent && <span className={`thread-state state-${agent.state}`}>{agent.state.replace("_", " ")}</span>}
        <button type="button" className="thread-close" aria-label="Close"
                onClick={() => openThread.set(null)}>×</button>
      </div>
      {live.error && <p className="read-failure">Couldn't read the thread ({live.error}); trying again.</p>}
      <div className="thread-items" ref={ref}>
        {earlier != null && <button type="button" className="history-earlier" onClick={loadEarlier}>
          Earlier</button>}
        {live.value && rows.length === 0 && <p className="workflow-empty">Nothing to or from this agent yet</p>}
        {rows.map((r) => <RecordItem key={r.key} item={r.item!} answer={r.answer}
                                     restorable={r.restorable} />)}
      </div>
    </div>
  );
}
