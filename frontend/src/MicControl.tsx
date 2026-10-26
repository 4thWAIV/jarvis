/**
 * The microphone control, directly below the orb: the level meter, then mute,
 * then what voice input is doing whenever it is not simply listening (loading,
 * blocked, busy in another tab, interrupted, unavailable). That line stays
 * until the problem is resolved; it is not a toast.
 */

import { useSyncExternalStore } from "react";
import { inputStatus } from "./mic";
import { MicMeter } from "./MicMeter";
import { MuteButton } from "./MuteButton";

const QUIET_STATES = new Set(["listening", "paused", "muted"]);

function InputStatusLine() {
  const status = useSyncExternalStore(inputStatus.subscribe, inputStatus.get);
  if (QUIET_STATES.has(status.state) || !status.message) return null;
  return (
    <div id="input-status" className={`input-status ${status.state}`} role="status">
      {status.message}
    </div>
  );
}

export function MicControl() {
  return (
    <div id="mic-control">
      <MicMeter />
      <MuteButton />
      <InputStatusLine />
    </div>
  );
}
