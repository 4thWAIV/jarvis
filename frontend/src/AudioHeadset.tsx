/**
 * The headset: the whole audio subsystem at a glance, drawn on the button
 * that turns all audio off and on (MicOffButton.tsx), in the power button's
 * place. Still in every state, never animated.
 *
 * The boom's tip is what JARVIS hears: filled while listening, dashed while
 * starting, hollow while not listening, slashed while muted or off. The ear
 * cups are what it says: filled while speaking, a bar between them once
 * stopped, crossed while his speech mute is on. All audio off (Mic off and
 * speech muted) is the power symbol. Problems stay on the health dot beside
 * Stop.
 */

import type { Hearing, Saying } from "./audioState";

const POWER = <><path d="M18.36 6.64a9 9 0 1 1-12.73 0" /><line x1="12" y1="2" x2="12" y2="12" /></>;

export function HeadsetGlyph({ hearing, saying, allOff }: { hearing: Hearing; saying: Saying; allOff: boolean }) {
  if (allOff) {
    return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2}
                strokeLinecap="round" aria-hidden="true">{POWER}</svg>;
  }
  const lit = saying === "speaking";
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path className="headset-band" d="M4.5 14V12a7.5 7.5 0 0 1 15 0v2" />
      <g className={`headset-cups${lit ? " lit" : ""}`}>
        <rect x="3" y="13" width="4" height="6.5" rx="1.5" />
        <rect x="17" y="13" width="4" height="6.5" rx="1.5" />
      </g>
      {saying === "stopped" && <line className="headset-stopped" x1="8.5" y1="16" x2="15.5" y2="16" strokeWidth={2} />}
      {saying === "muted" && <g strokeWidth={2}>
        <line x1="1.5" y1="12" x2="8.5" y2="20.5" /><line x1="15.5" y1="12" x2="22.5" y2="20.5" />
      </g>}
      <g className={`headset-boom ${hearing}`}>
        <path d="M5 19.5v.5a2.5 2.5 0 0 0 2.5 2.5h2.6" />
        <circle cx="12" cy="22.5" r="1.7" strokeDasharray={hearing === "starting" ? "1.6 1.4" : undefined} />
      </g>
      {(hearing === "muted" || hearing === "off") &&
        <line x1="9" y1="18.5" x2="15.5" y2="24" strokeWidth={2.2} />}
    </svg>
  );
}
