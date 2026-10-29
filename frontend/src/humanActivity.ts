/**
 * The page's one human-activity collector: when a person last used this
 * JARVIS's page, reported to POST /api/team/activity (human_activity.py),
 * which the backend sends on to the team server in its next node update.
 *
 * Capture-phase listeners on the window see every trusted input before any
 * control handles it: pointer, touch, pen and mouse use, pointer movement
 * over the page, clicks (assistive-technology activation included), keys,
 * text input and composition, the wheel, changed settings and chosen or
 * dropped files. A bare `scroll` event is not listened to: a program can
 * scroll, and the wheel, touch and keys that scroll are already counted.
 * Synthetic events (`isTrusted` false), focus, page loads, timers and
 * anything the page does to itself never count.
 *
 * Only where it came from is sent: `typed` for typing into a text field,
 * `gui` for anything else. Never the key, the text, the position or the file.
 * At most one report every THROTTLE_MS: the first input after a quiet spell
 * is reported at once, later ones are folded into one report at the end of
 * the window, and one still waiting is sent as the page is hidden or left.
 */

export type PageSource = "gui" | "typed";

const ROUTE = "/api/team/activity";
export const THROTTLE_MS = 5000;

const EVENTS = [
  "pointerdown", "pointermove", "click", "keydown", "beforeinput", "input",
  "compositionstart", "compositionend", "wheel", "touchstart", "touchmove", "change", "drop",
];
const TYPING = new Set(["keydown", "beforeinput", "input", "compositionstart", "compositionend"]);

function isTextField(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || target instanceof HTMLTextAreaElement) return true;
  return target instanceof HTMLInputElement
    && !["button", "checkbox", "radio", "range", "file", "color", "submit", "reset", "image"].includes(target.type);
}

/** Start collecting; returns the function that stops it. */
export function startHumanActivity(): () => void {
  let pending: PageSource | null = null;
  let lastSent = -Infinity;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const send = (leaving: boolean) => {
    if (!pending) return;
    const body = JSON.stringify({ source: pending });
    pending = null;
    lastSent = performance.now();
    // keepalive lets a report made as the page goes still arrive.
    fetch(ROUTE, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: leaving })
      .catch(() => {});
  };

  const observe = (e: Event) => {
    if (!e.isTrusted) return;
    pending = TYPING.has(e.type) && isTextField(e.target) ? "typed" : "gui";
    const wait = lastSent + THROTTLE_MS - performance.now();
    if (wait <= 0) send(false);
    else if (!timer) timer = setTimeout(() => { timer = null; send(false); }, wait);
  };

  const hidden = () => {
    if (document.visibilityState === "hidden") send(true);
  };
  const leaving = () => send(true);
  const options = { capture: true, passive: true };
  EVENTS.forEach((type) => window.addEventListener(type, observe, options));
  document.addEventListener("visibilitychange", hidden);
  window.addEventListener("pagehide", leaving);
  return () => {
    EVENTS.forEach((type) => window.removeEventListener(type, observe, options));
    document.removeEventListener("visibilitychange", hidden);
    window.removeEventListener("pagehide", leaving);
    if (timer) clearTimeout(timer);
  };
}
