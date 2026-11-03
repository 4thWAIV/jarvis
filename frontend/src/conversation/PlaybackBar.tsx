import { useSyncExternalStore } from "react";
import { voiceState, stopSpeaking } from "../voiceStatus";

export function PlaybackBar() {
  const state = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  if (state !== "speaking" && state !== "thinking") return null;
  const speaking = state === "speaking";
  return <div className="chat-playback" role="group" aria-label="Reply playback">
    <span className={`playback-indicator ${speaking ? "playing" : "waiting"}`} aria-hidden="true"><i /><i /><i /></span>
    <div className="playback-label" role="status">
      <strong>{speaking ? "JARVIS is speaking" : "JARVIS is thinking"}</strong>
      <span>{speaking ? "Follow the reply above at your own pace" : "Your reply will appear above"}</span>
    </div>
    {speaking && <button type="button" onClick={() => stopSpeaking.current()} title="Stop reading aloud (Escape)">
      Skip to end <span aria-hidden="true">»</span>
    </button>}
  </div>;
}
