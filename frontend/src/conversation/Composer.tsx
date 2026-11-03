import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { sendTyped, voiceConnected } from "../voiceStatus";

const DRAFT_KEY = "jarvis.conversation.draft";
function readDraft() { try { return sessionStorage.getItem(DRAFT_KEY) ?? ""; } catch { return ""; } }
function keepDraft(text: string) {
  try { text ? sessionStorage.setItem(DRAFT_KEY, text) : sessionStorage.removeItem(DRAFT_KEY); } catch { /* storage is optional */ }
}

export function Composer() {
  const input = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState(readDraft);
  const [error, setError] = useState("");
  const connected = useSyncExternalStore(voiceConnected.subscribe, voiceConnected.get);
  useLayoutEffect(() => {
    const field = input.current;
    if (!field) return;
    field.style.height = "0px";
    field.style.height = `${Math.min(field.scrollHeight, Math.min(180, window.innerHeight * 0.25))}px`;
  }, [draft]);
  function send() {
    const text = draft.trim();
    if (!text) return;
    if (!sendTyped.current(text)) {
      setError("Not connected. Your message has not been sent. Your draft is kept here; try again after reconnecting.");
      return;
    }
    setDraft(""); keepDraft(""); setError(""); input.current?.focus();
  }
  return <form className="chat-composer" onSubmit={e => { e.preventDefault(); send(); }}>
    <label htmlFor="conversation-draft">Message JARVIS</label>
    <textarea id="conversation-draft" ref={input} className="hearing-input" rows={1}
      aria-label="Message JARVIS" aria-describedby="composer-help" placeholder="Ask a question or share an idea…"
      value={draft} onChange={e => { setDraft(e.target.value); keepDraft(e.target.value); setError(""); }}
      onKeyDown={e => {
        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
          e.preventDefault(); send();
        }
      }} />
    {error && <p className="composer-error" role="alert">{error}</p>}
    <div className="composer-footer">
      <span id="composer-help">{connected ? <>Enter to send <span aria-hidden="true">·</span> Shift + Enter for a new line</> : "Reconnecting… your draft is safe"}</span>
      <button type="submit" className="composer-send" disabled={!draft.trim()} aria-label="Send message">
        Send <span aria-hidden="true">↑</span>
      </button>
    </div>
  </form>;
}
