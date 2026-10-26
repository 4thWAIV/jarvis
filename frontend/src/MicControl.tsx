/**
 * The microphone control, directly below the orb: the level meter, mute, and
 * the health dot while voice input has a problem (loading, blocked, busy in
 * another tab, interrupted, unavailable, or the recogniser falling behind).
 * The words are in the dot's title and in the Diagnostics view's Messages,
 * which clicking the dot opens; nothing is written under the orb.
 */

import { useSyncExternalStore } from "react";
import { inputStatus } from "./mic";
import { MicMeter } from "./MicMeter";
import { MuteButton } from "./MuteButton";
import { health, HEALTH_WORDS, openDiagnostics } from "./voiceHealth";
import type { InputStatus } from "./voiceInput";

const QUIET_STATES = new Set(["listening", "paused", "muted"]);
const LOST_STATES = new Set(["interrupted", "unavailable", "permission"]);

/** What the dot says, and whether words were lost, or null when all is well. */
export function inputProblem(status: InputStatus, h: { state: string; cause: string } | null):
    { text: string; bad: boolean } | null {
  if (!QUIET_STATES.has(status.state) && status.message) {
    return { text: status.message, bad: LOST_STATES.has(status.state) };
  }
  if (h && h.state !== "healthy") {
    const words = HEALTH_WORDS[h.state as keyof typeof HEALTH_WORDS] ?? h.state;
    return { text: h.cause ? `${words}: ${h.cause}` : words, bad: h.state === "no-frames" };
  }
  return null;
}

function HealthDot() {
  const status = useSyncExternalStore(inputStatus.subscribe, inputStatus.get);
  const h = useSyncExternalStore(health.subscribe, health.get);
  const problem = inputProblem(status, h);
  if (!problem) return null;
  return (
    <button id="health-dot" type="button" className={`health-dot${problem.bad ? " bad" : ""}`}
            data-state={status.state} title={problem.text} aria-label={problem.text}
            onClick={() => openDiagnostics.set(true)} />
  );
}

export function MicControl() {
  return (
    <div id="mic-control">
      <MicMeter />
      <MuteButton />
      <HealthDot />
    </div>
  );
}
