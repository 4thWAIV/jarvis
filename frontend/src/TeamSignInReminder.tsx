/**
 * The sign-in reminder at the top of the centre area, under the view tabs:
 * shown while this JARVIS is not signed in to a team server (network.ts's
 * needsSignIn, the same test that makes the Team Server tile say Sign in),
 * and gone by itself once it is. JARVIS works as before without one; this
 * only reminds.
 *
 * Sign in runs teamSignIn.ts (Settings, Team's flow with the saved address,
 * or Settings at Team without one). Later hides it until the page is loaded
 * again or its connection to JARVIS starts again (a JARVIS restart); never
 * for good.
 *
 * While shown it sets --centre-notice-height to its own height, so every
 * centre view starts below it and it covers nothing, the conversation's
 * input included.
 */

import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { reconnected } from "./liveRead";
import { needsSignIn, network } from "./network";
import { createStore } from "./store";
import { ServerIcon } from "./TeamServerTile";
import { signIn } from "./teamSignIn";

export const REMINDER_TEXT =
  "Sign in to your team server: messages between people and the Network view need it";

// The space kept between the reminder and the view below it.
const GAP_PX = 8;

// Later, until the page or its connection to JARVIS starts again.
const later = createStore(false);
reconnected.subscribe(() => later.set(false));

export function TeamSignInReminder() {
  const live = useSyncExternalStore(network.subscribe, network.get);
  const hidden = useSyncExternalStore(later.subscribe, later.get);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const shown = needsSignIn(live.value) && !hidden;

  useLayoutEffect(() => {
    const el = box.current;
    if (!shown || !el) return;
    const root = document.documentElement.style;
    const fit = () => root.setProperty("--centre-notice-height", `${el.offsetHeight + GAP_PX}px`);
    fit();
    const watch = new ResizeObserver(fit);
    watch.observe(el);
    return () => { watch.disconnect(); root.removeProperty("--centre-notice-height"); };
  }, [shown]);

  if (!shown) return null;
  const start = async () => {
    setBusy(true);
    setProblem(null);
    try { await signIn(); } catch (e) { setProblem((e as Error).message); }
    setBusy(false);
  };
  return (
    <div id="team-reminder" role="status" ref={box}>
      <ServerIcon />
      <p>{REMINDER_TEXT}</p>
      <button type="button" className="settings-btn primary" disabled={busy} onClick={start}>
        Sign in</button>
      <button type="button" className="settings-btn" onClick={() => later.set(true)}>Later</button>
      {problem && <p className="team-reminder-problem">{problem}</p>}
    </div>
  );
}
