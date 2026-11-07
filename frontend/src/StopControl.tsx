/**
 * Stop, at the Conversation's message input immediately left of Send (or
 * in the takeover bar while something has taken over the page; drawn where
 * it is by MicPortal, MicControl.tsx),
 * while JARVIS is speaking (Escape skips the rest of a reading instead).
 * Absent, its slot is not drawn and takes no room; the group it sits in is
 * anchored at the right, so only what is left of it (the microphone's
 * health dot) moves, and Send and the microphone never do. A target the
 * brain can point at while it is there; Stop itself is hushing him, never
 * navigation.
 */

import { useSyncExternalStore } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { useT } from "./locale";
import { useUiTarget } from "./uiRegistry";
import { stopSpeaking, voiceState } from "./voiceStatus";

export function StopControl({ view }: { view: "main.conversation" | "main.takeover" }) {
  const t = useT();
  const speaking = useSyncExternalStore(voiceState.subscribe, voiceState.get) === "speaking";
  const ui = useUiTarget({ ref: { region: "main", view, kind: "status" },
                           label: t("targets.status"), socket: CENTRE_SOCKET });
  return (
    <span id="stop-slot" ref={ui.whole} data-shown={speaking}>
      <button id="hush" type="button" title={t("left.status.stopTitle")} data-ui-action="speech"
              ref={speaking ? ui.part("stop") : undefined}
              onClick={() => stopSpeaking.current()}>
        {t("left.status.stop")}
      </button>
    </span>
  );
}
