import { useState } from "react";
import { openThread, restoreCapture, STATUS_WORDS, type ConversationItem } from "../conversation";
import { ChatMessage } from "./ChatMessage";

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
  return <ChatMessage speaker={speaker} at={item.at} text={item.text} />;
}

