/**
 * What JARVIS is doing, directly below the orb: the status line while he is
 * listening or thinking, and Stop while he is speaking (Escape does the same).
 * A target the brain can point at (the text, and Stop while it is there);
 * Stop itself is hushing him, never navigation.
 */

import { useSyncExternalStore } from "react";
import { useUiTarget } from "./uiRegistry";
import { statusText, stopSpeaking, voiceState } from "./voiceStatus";

export function StatusControl() {
  const state = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  const text = useSyncExternalStore(statusText.subscribe, statusText.get);
  const ui = useUiTarget({ ref: { region: "left", view: "left.fixed", kind: "status" }, label: "Status",
                           socket: { rail: "rail.left", side: "left" } });
  return (
    <div id="status-control" ref={ui.whole}>
      {text && <div id="status-text" ref={ui.part("text")}>{text}</div>}
      {state === "speaking" && (
        <button id="hush" type="button" title="Stop speaking (Esc)" ref={ui.part("stop")}
                data-ui-action="speech"
                onClick={() => stopSpeaking.current()}>
          Stop
        </button>
      )}
    </div>
  );
}
