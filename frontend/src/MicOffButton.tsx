/**
 * Turns the microphone off and on: off releases it, so the browser's
 * recording indicator goes out, and on asks for it again, which takes longer
 * than unmuting. See `micOff()` in voiceInput.ts.
 */

import { useSyncExternalStore } from "react";
import { useT } from "./locale";
import { micOff } from "./mic";

export function MicOffButton({ partRef }: { partRef?: (el: Element | null) => void } = {}) {
  const t = useT();
  const off = useSyncExternalStore(micOff.subscribe, micOff.get);
  return (
    <button
      id="btn-mic-off"
      ref={partRef}
      data-ui-action="microphone"
      className={`control-button small${off ? " muted" : ""}`}
      title={t(off ? "left.mic.turnOn" : "left.mic.turnOff")}
      onClick={(e) => {
        e.stopPropagation();
        micOff.set(!micOff.get());
      }}
    >
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth={off ? 2.2 : 1.5} strokeLinecap="round">
        <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
        <line x1="12" y1="2" x2="12" y2="12" />
      </svg>
    </button>
  );
}
