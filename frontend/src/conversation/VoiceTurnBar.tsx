/**
 * What he has said aloud and not yet sent, inside the message surface: Send now
 * while it is being gathered, and, for a turn the speech recogniser could
 * not finish, Retry (wait for it again), Send now (as it was heard) and
 * Keep as draft (into the message box, unsent). While he is quiet in a
 * gathered turn its label says the pause cue's stage (tentative, deciding,
 * then likely end), the visual twin of the cue's sound. Each is a frame to the
 * server, the same call as the brain's voice_turn; nothing is sent here
 * on its own.
 */

import { useSyncExternalStore } from "react";
import { useT } from "../locale";
import { turnAction, voiceTurn, type VoiceTurn } from "../voiceStatus";

export function voiceTurnLabel(turn: VoiceTurn) {
  return turn.state === "stalled" ? "main.conversation.turnStalled"
    : turn.pause ? (`main.conversation.turnPause.${turn.pause}` as const) : "main.conversation.turnGathering";
}

export function VoiceTurnBar({ partRef, labelInComposer = false }: { labelInComposer?: boolean; partRef: (el: Element | null) => void }) {
  const t = useT();
  const turn = useSyncExternalStore(voiceTurn.subscribe, voiceTurn.get);
  if (!turn || !turn.text.trim()) return null;
  const stalled = turn.state === "stalled";
  return <div id="voice-turn" className={`chat-playback chat-voice-turn${stalled ? " stalled" : ""}`} role="group"
              ref={partRef} aria-label={t("main.conversation.turn")} data-pause={turn.pause ?? undefined}>
    <div className="playback-label" role="status">
      {!labelInComposer && <strong>{t(voiceTurnLabel(turn))}</strong>}
      <span>{turn.text}</span>
    </div>
    {stalled && <button type="button" id="voice-turn-retry" data-ui-action="domain"
                        onClick={() => turnAction.current("retry")} title={t("main.conversation.turnRetryTitle")}>
      {t("main.conversation.turnRetry")}
    </button>}
    <button type="button" id="voice-turn-send" data-ui-action="domain"
            onClick={() => turnAction.current("send_now")}
            title={t(stalled ? "main.conversation.turnSendStalledTitle" : "main.conversation.turnSendTitle")}>
      {t("main.conversation.turnSend")}
    </button>
    {stalled && <button type="button" id="voice-turn-draft" data-ui-action="domain"
                        onClick={() => turnAction.current("keep_as_draft")}
                        title={t("main.conversation.turnDraftTitle")}>
      {t("main.conversation.turnDraft")}
    </button>}
  </div>;
}
