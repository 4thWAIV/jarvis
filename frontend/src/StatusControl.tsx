/**
 * What JARVIS is doing. Directly below the orb, the status line while he is
 * listening or thinking (StatusControl). At the Conversation's message
 * input, immediately left of Send, Stop while he is speaking (StopControl):
 * its slot is kept while it is hidden, so Send and the microphone never
 * move. Each is a target the brain can point at (the text; Stop while it is
 * there); Stop itself is hushing him, never navigation.
 */

import { useSyncExternalStore } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
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
    </div>
  );
}

/** Stop, in a slot that keeps its width whether or not Stop is shown: the
 *  button is always laid out, and `visibility: hidden` (not seen, focused,
 *  read or clicked) while he is not speaking. */
export function StopControl() {
  const t = useT();
  const speaking = useSyncExternalStore(voiceState.subscribe, voiceState.get) === "speaking";
  const ui = useUiTarget({ ref: { region: "main", view: "main.conversation", kind: "status" },
                           label: t("targets.status"), socket: CENTRE_SOCKET });
  return (
    <span id="stop-slot" ref={ui.whole}>
      <button id="hush" type="button" title={t("left.status.stopTitle")} data-ui-action="speech"
              ref={speaking ? ui.part("stop") : undefined} data-shown={speaking}
              onClick={() => stopSpeaking.current()}>
        {t("left.status.stop")}
      </button>
    </span>
  );
}
