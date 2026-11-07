/**
 * The headset, in the power button's place: turns the whole audio system
 * off and on. Off is Mic off (the microphone released, so the browser's
 * recording indicator goes out; kept across a reload, mic.ts) and JARVIS's
 * speech muted (voice_control.py, per user); on is the microphone on again,
 * a deliberate start of listening, and speech unmuted (audioState.ts,
 * `setAllAudio`). It is pressed while the microphone is off.
 *
 * It is drawn as the headset (AudioHeadset.tsx), the whole audio subsystem
 * at a glance: what JARVIS hears on the boom, what it says on the ear cups,
 * red while any of it is muted or off.
 */

import { useSyncExternalStore } from "react";
import { HeadsetGlyph } from "./AudioHeadset";
import { setAllAudio, useHearing, useSaying } from "./audioState";
import { useT } from "./locale";
import { quiet } from "./mic";

export function MicOffButton({ partRef }: { partRef?: (el: Element | null) => void } = {}) {
  const t = useT();
  const off = useSyncExternalStore(quiet.subscribe, quiet.get) === "off";
  const { look } = useHearing();
  const saying = useSaying();
  const allOff = off && saying === "muted";
  const red = look === "muted" || look === "off" || saying === "muted";
  const label = t(off ? "left.audio.allOn" : "left.audio.allOff");
  return (
    <button
      id="btn-mic-off"
      type="button"
      ref={partRef}
      data-ui-action="microphone"
      data-hearing={look}
      data-saying={saying}
      data-all-off={allOff}
      className={`control-button small audio-headset${red ? " muted" : ""}`}
      title={label}
      aria-label={label}
      aria-pressed={off}
      onClick={(e) => {
        e.stopPropagation();
        setAllAudio(off);
      }}
    >
      <HeadsetGlyph hearing={look} saying={saying} allOff={allOff} />
    </button>
  );
}
