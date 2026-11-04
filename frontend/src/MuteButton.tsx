/**
 * Mutes and unmutes: nothing is sent while muted, but the microphone stays
 * open so unmuting is quick. The choice is kept across a reload (mic.ts),
 * where it is restored without opening a microphone; unmuting is a
 * deliberate start of listening. See `setQuiet` in voiceInput.ts;
 * MicOffButton releases the microphone itself.
 */

import { useSyncExternalStore } from "react";
import { useT } from "./locale";
import { chooseQuiet, quiet } from "./mic";

export function MuteButton({ partRef }: { partRef?: (el: Element | null) => void } = {}) {
  const t = useT();
  const muted = useSyncExternalStore(quiet.subscribe, quiet.get) === "muted";
  return (
    <button
      id="btn-mute"
      ref={partRef}
      data-ui-action="microphone"
      className={`control-button small${muted ? " muted" : ""}`}
      title={t("left.mic.mute")}
      aria-label={t("left.mic.mute")}
      aria-pressed={muted}
      onClick={(e) => {
        e.stopPropagation();
        chooseQuiet(muted ? "none" : "muted");
      }}
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth={muted ? 2.2 : 1.5} strokeLinecap="round">
        <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" y1="19" x2="12" y2="23" />
        {/* Muted: a slash through the microphone. */}
        {muted && <line className="mute-slash" x1="3" y1="2" x2="21" y2="22" />}
      </svg>
    </button>
  );
}
