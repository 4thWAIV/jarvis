import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useT } from "../locale";
import { MicControl } from "../MicControl";
import { StopControl } from "../StatusControl";
import { createStore } from "../store";
import { sendTyped } from "../voiceStatus";
import { clearTypedRefusal, refuseTyped } from "./controlState";

const DRAFT_KEY = "jarvis.conversation.draft";
function readDraft() { try { return sessionStorage.getItem(DRAFT_KEY) ?? ""; } catch { return ""; } }
function keepDraft(text: string) {
  try { text ? sessionStorage.setItem(DRAFT_KEY, text) : sessionStorage.removeItem(DRAFT_KEY); } catch { /* storage is optional */ }
}

// Words offered as an unsent draft from outside the composer (voice input's
// kept words that could not be finished): added to what is there, never sent.
const offered = createStore<{ text: string } | null>(null);
export function offerDraft(text: string): void {
  const words = text.trim();
  if (words) offered.set({ text: words });
}

/** The message input: its heading row (the conversation control, which
 *  also says when a typed message could not be sent), the text box, the
 *  help line, and at its lower right Stop (in its kept slot), Send and the
 *  microphone (MicControl). */
export function Composer({ partRef, heading }: { partRef: (el: Element | null) => void; heading: ReactNode }) {
  const t = useT();
  const input = useRef<HTMLTextAreaElement>(null);
  const [draft, setDraft] = useState(readDraft);
  useEffect(() => {
    // Offered while the composer was not on show, or now.
    const take = () => {
      const words = offered.get();
      if (!words) return;
      offered.set(null);
      setDraft(now => { const next = now.trim() ? `${now.trimEnd()} ${words.text}` : words.text; keepDraft(next); return next; });
    };
    take();
    return offered.subscribe(take);
  }, []);
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
      refuseTyped();
      return;
    }
    setDraft(""); keepDraft(""); clearTypedRefusal(); input.current?.focus();
  }
  return <form className="chat-composer" data-ui-action="domain" onSubmit={e => { e.preventDefault(); send(); }}>
    {heading}
    <textarea id="conversation-draft" ref={el => { input.current = el; partRef(el); }}
      data-ui-action="input" className="hearing-input" rows={1}
      aria-label={t("main.conversation.messageJarvis")} aria-describedby="composer-help"
      placeholder={t("main.conversation.draftPlaceholder")}
      value={draft} onChange={e => { setDraft(e.target.value); keepDraft(e.target.value); clearTypedRefusal(); }}
      onKeyDown={e => {
        if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
          e.preventDefault(); send();
        }
      }} />
    <div className="composer-footer">
      <span id="composer-help">{t("main.conversation.composerHelp")}</span>
      <StopControl />
      <button type="submit" className="composer-send" disabled={!draft.trim()} aria-label={t("main.conversation.sendMessage")}>
        <span className="composer-send-word">{t("main.conversation.send")}</span> <span aria-hidden="true">↑</span>
      </button>
      <MicControl />
    </div>
  </form>;
}
