/**
 * The speaker: mutes and unmutes JARVIS's speech, beside the microphone's
 * controls (MicControl.tsx). Muted, JARVIS still answers in text but says
 * nothing: the server makes no audio for it, and what is sounding stops at
 * once (voice_control.py, `speech_muted`; the same change as the brain's
 * `mute_speech`, which can only mute). Its look is the mute and what JARVIS
 * is doing: red and crossed while muted, lit with pulsing waves while
 * speaking (still under reduced motion), grey while silent, a small square
 * for a moment after Stop. Stop stays its own control.
 */

import { setSpeechMuted, useSaying } from "./audioState";
import { useT } from "./locale";

export function SpeakerButton({ partRef }: { partRef: (el: Element | null) => void }) {
  const t = useT();
  const saying = useSaying();
  const muted = saying === "muted";
  const label = t(muted ? "left.audio.speaker.unmute" : "left.audio.speaker.mute");
  return (
    <button id="btn-speaker" type="button" ref={partRef} data-ui-action="speech" data-saying={saying}
            className={`control-button small audio-speaker ${saying}${muted ? " muted" : ""}`}
            title={label} aria-label={label} aria-pressed={muted}
            onClick={(e) => { e.stopPropagation(); setSpeechMuted(!muted); }}>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth={muted ? 2.2 : 1.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path className="speaker-body" d="M11 5 6 9H2v6h4l5 4V5z" />
        {saying === "speaking" && <>
          <path className="speaker-wave first" d="M15.54 8.46a5 5 0 0 1 0 7.07" />
          <path className="speaker-wave second" d="M19.07 4.93a10 10 0 0 1 0 14.14" />
        </>}
        {saying === "stopped" && <rect x="15" y="9" width="6" height="6" rx="1" fill="currentColor" stroke="none" />}
        {muted && <><line x1="15" y1="9" x2="21" y2="15" /><line x1="21" y1="9" x2="15" y2="15" /></>}
      </svg>
    </button>
  );
}
