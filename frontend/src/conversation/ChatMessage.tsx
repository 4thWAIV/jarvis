import { useState, type ReactNode } from "react";
import { useT } from "../locale";
import { ReplyText } from "./ReplyText";

/** Full text stays selectable and in normal document flow, at every length. */
export function ChatMessage({ speaker, at, text, children, pending = false, interrupted = false, kind = "", wholeRef, textRef }: {
  wholeRef?: (el: Element | null) => void; textRef?: (el: Element | null) => void;
  speaker: "user" | "jarvis"; at?: number; text?: string; children?: ReactNode; pending?: boolean; interrupted?: boolean; kind?: string;
}) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const date = at ? new Date(at * 1000) : null;
  return <article className={`chat-message ${speaker}`} ref={wholeRef}
    aria-label={t(speaker === "jarvis" ? "main.conversation.jarvisMessage" : "main.conversation.userMessage")}>
    <div className="chat-avatar" aria-hidden="true">{speaker === "jarvis" ? "J" : "Y"}</div>
    <div className="chat-message-main">
      <header className="chat-message-meta">
        <span className="chat-author">{speaker === "jarvis" ? "JARVIS" : t("main.conversation.you")}</span>
        {date && <time dateTime={date.toISOString()}>{date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time>}
        {interrupted && <span className="chat-streaming">{t("main.conversation.interrupted")}</span>}
        {pending && <span className="chat-streaming">{t("main.conversation.responding")}</span>}
        {text && <button className="chat-copy" type="button" data-ui-action="domain" aria-label={t("main.conversation.copyMessage")} onClick={async () => {
          try { await navigator.clipboard.writeText(text); setCopied(true); setCopyError(false); }
          catch { setCopyError(true); }
        }} onBlur={() => setCopied(false)}>{t(copied ? "main.conversation.copied" : "main.conversation.copy")}</button>}
      </header>
      <div className={`history-turn chat-message-body ${speaker} ${kind}`} ref={textRef}>{children ?? (speaker === "jarvis" && text ? <ReplyText text={text} /> : text)}</div>
      {copyError && <p className="chat-copy-error" role="status">{t("main.conversation.copyFailed")}</p>}
    </div>
  </article>;
}
