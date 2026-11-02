/**
 * What JARVIS is doing, directly below the orb: the status line while he is
 * listening or thinking, and Stop while he is speaking (Escape does the same).
 * A target the brain can point at (the text, and Stop while it is there);
 * Stop itself is hushing him, never navigation.
 */

import { useSyncExternalStore } from "react";
import { useT } from "./locale";
import type { OrbState } from "./orb";
import { useUiTarget } from "./uiRegistry";
import { stopSpeaking, voiceState } from "./voiceStatus";

// The status line's words by state; the others show none (while compacting
// the notice carries the words and the orb the state).
const STATUS_WORDS: Partial<Record<OrbState, "left.status.listening" | "left.status.thinking">> = {
  listening: "left.status.listening",
  thinking: "left.status.thinking",
};

export function StatusControl() {
  const t = useT();
  const state = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  const words = STATUS_WORDS[state];
  const text = words ? t(words) : "";
  const ui = useUiTarget({ ref: { region: "left", view: "left.fixed", kind: "status" }, label: t("targets.status"),
                           socket: { rail: "rail.left", side: "left" } });
  return (
    <div id="status-control" ref={ui.whole}>
      {text && <div id="status-text" ref={ui.part("text")}>{text}</div>}
      {state === "speaking" && (
        <button id="hush" type="button" title={t("left.status.stopTitle")} ref={ui.part("stop")}
                data-ui-action="speech"
                onClick={() => stopSpeaking.current()}>
          {t("left.status.stop")}
        </button>
      )}
    </div>
  );
}
