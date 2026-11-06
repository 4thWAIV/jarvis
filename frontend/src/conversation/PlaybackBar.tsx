import { useSyncExternalStore } from "react";
import { useT } from "../locale";
import { voiceState, skipReading } from "../voiceStatus";

export function PlaybackBar({ partRef, labelInComposer = false }: { labelInComposer?: boolean; partRef: (el: Element | null) => void }) {
  const t = useT();
  const state = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  if (state !== "speaking" && state !== "thinking") return null;
  const speaking = state === "speaking";
  return <div className="chat-playback" role="group" ref={partRef} aria-label={t("main.conversation.playback")}>
    <span className={`playback-indicator ${speaking ? "playing" : "waiting"}`} aria-hidden="true"><i /><i /><i /></span>
    <div className="playback-label" role="status">
      {!labelInComposer && <strong>{t(speaking ? "main.conversation.isSpeaking" : "main.conversation.isThinking")}</strong>}
      <span>{t(speaking ? "main.conversation.followReply" : "main.conversation.replyAbove")}</span>
    </div>
    {speaking && <button type="button" data-ui-action="speech" onClick={() => skipReading.current()} title={t("main.conversation.skipTitle")}>
      {t("main.conversation.skip")} <span aria-hidden="true">»</span>
    </button>}
  </div>;
}
