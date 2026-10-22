/**
 * The microphone control, directly below the orb: the level meter, then mute.
 */

import { MicMeter } from "./MicMeter";
import { MuteButton } from "./MuteButton";

export function MicControl() {
  return (
    <div id="mic-control">
      <MicMeter />
      <MuteButton />
    </div>
  );
}
