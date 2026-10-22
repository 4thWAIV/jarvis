/**
 * Turns the microphone off and on. Only the microphone: see `mute()` in voice.ts.
 */

import { useSyncExternalStore } from "react";
import { micMuted } from "./mic";

export function MuteButton() {
  const muted = useSyncExternalStore(micMuted.subscribe, micMuted.get);
  return (
    <button
      id="btn-mute"
      className={`control-button small${muted ? " muted" : ""}`}
      title="Mute/Unmute"
      onClick={(e) => {
        e.stopPropagation();
        micMuted.set(!micMuted.get());
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
