/** Steady: this browser's motion preference, kept in localStorage like the
 *  quiet microphone and the theme (motion, like reduced motion itself, is a
 *  property of the screen it plays on). Steady sets `data-motion="steady"`
 *  on the document, which keeps every look and drops the glow's travel, the
 *  flow and the arrival flash (nodeActivity.tsx, style.css). Settings, You
 *  sets it; the brain's set_motion asks every open page to (a `motion`
 *  frame on the voice socket), only as he asks. Read before anything draws,
 *  and followed across this browser's tabs by the `storage` event. */
import { createStore } from "./store";

export type Motion = "full" | "steady";
export const MOTION_KEY = "jarvis.motion";
const validMotion = (value: string | null | undefined): Motion => value === "steady" ? "steady" : "full";

function saved(): Motion {
  try { return validMotion(localStorage.getItem(MOTION_KEY)); } catch { return "full"; }
}

export const motion = createStore<Motion>(saved());

function apply(value: Motion): void {
  if (value === "steady") document.documentElement.dataset.motion = "steady";
  else delete document.documentElement.dataset.motion;
  motion.set(value);
}

apply(motion.get());

/** Steady or full motion on this page, saved for this browser. */
export function setMotion(value: Motion): void {
  const next = validMotion(value);
  apply(next);
  try {
    if (next === "steady") localStorage.setItem(MOTION_KEY, next);
    else localStorage.removeItem(MOTION_KEY);
  } catch { /* The page still holds still when storage is unavailable. */ }
}

window.addEventListener("storage", (event) => {
  if (event.key === MOTION_KEY || event.key === null) apply(saved());
});
