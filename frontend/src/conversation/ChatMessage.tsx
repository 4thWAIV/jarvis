import { useState, type ReactNode } from "react";
import { ReplyText } from "./ReplyText";

/** Full text stays selectable and in normal document flow, at every length. */
export function ChatMessage({ speaker, at, text, children, pending = false, interrupted = false, kind = "" }: {
  speaker: "user" | "jarvis"; at?: number; text?: string; children?: ReactNode; pending?: boolean; interrupted?: boolean; kind?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const date = at ? new Date(at * 1000) : null;
  return <article className={`chat-message ${speaker}`} aria-label={`${speaker === "jarvis" ? "JARVIS" : "You"} message`}>
    <div className="chat-avatar" aria-hidden="true">{speaker === "jarvis" ? "J" : "Y"}</div>
    <div className="chat-message-main">
      <header className="chat-message-meta">
        <span className="chat-author">{speaker === "jarvis" ? "JARVIS" : "You"}</span>
        {date && <time dateTime={date.toISOString()}>{date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>}
        {interrupted && <span className="chat-streaming">Connection lost · partial reply</span>}
        {pending && <span className="chat-streaming">Responding</span>}
        {text && <button className="chat-copy" type="button" aria-label="Copy message" onClick={async () => {
          try { await navigator.clipboard.writeText(text); setCopied(true); setCopyError(false); }
          catch { setCopyError(true); }
        }} onBlur={() => setCopied(false)}>{copied ? "Copied" : "Copy"}</button>}
      </header>
      <div className={`history-turn chat-message-body ${speaker} ${kind}`}>{children ?? (speaker === "jarvis" && text ? <ReplyText text={text} /> : text)}</div>
      {copyError && <p className="chat-copy-error" role="status">Couldn’t copy. Select the text to copy it.</p>}
    </div>
  </article>;
}
