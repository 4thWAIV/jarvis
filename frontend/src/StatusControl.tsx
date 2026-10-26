/**
 * What JARVIS is doing, directly below the orb: the status line while he is
 * listening or thinking, and Stop while he is speaking (Escape does the same).
 */

import { useSyncExternalStore } from "react";
import { statusText, stopSpeaking, voiceState } from "./voiceStatus";

export function StatusControl() {
  const state = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  const text = useSyncExternalStore(statusText.subscribe, statusText.get);
  return (
    <div id="status-control">
      {text && <div id="status-text">{text}</div>}
      {state === "speaking" && (
        <button id="hush" type="button" title="Stop speaking (Esc)"
                onClick={() => stopSpeaking.current()}>
          Stop
        </button>
      )}
    </div>
  );
}
