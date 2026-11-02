/**
 * The sign-in reminder at the top of the centre area, under the view tabs:
 * shown while this JARVIS is not signed in to a team server (network.ts's
 * needsSignIn, the same test that makes the Team Server tile say Sign in),
 * and gone by itself once it is. JARVIS works as before without one; this
 * only reminds.
 *
 * Sign in takes Settings, Team's own path (teamSignIn.ts) with the address
 * last used, then opens Settings at Team, where the code to check against
 * the approval page is shown; part way through signing in, or with no
 * address saved, it only opens Settings at Team. Later hides it until the page is loaded
 * again or its connection to JARVIS starts again (a JARVIS restart); never
 * for good.
 *
 * While shown it sets --centre-notice-height to its own height, so every
 * centre view starts below it and it covers nothing, the conversation's
 * input included.
 *
 * Later is the registered command `reminder.later`, the same for his click
 * and the brain's request; Sign in stays the browser approval flow and is
 * never the brain's. The reminder is a target hung from the main rail, with
 * its text and both buttons as parts.
 */

import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { reconnected } from "./liveRead";
import { useT } from "./locale";
import { needsSignIn, network } from "./network";
import { createStore } from "./store";
import { ServerIcon } from "./TeamServerTile";
import { signInAgain } from "./teamSignIn";
import { bind, bindingProps } from "./uiNavigation";
import { byHand, registerCommand, useUiTarget } from "./uiRegistry";

// The space kept between the reminder and the view below it.
const GAP_PX = 8;

/** @routeState Later, until the page or its connection to JARVIS starts again. */
const later = createStore(false);
reconnected.subscribe(() => later.set(false));

registerCommand("reminder.later", {
  availability: () => needsSignIn(network.get().value) && !later.get()
    ? { available: true } : { available: false, reason: "no-entity" },
  execute() {
    later.set(true);
    return { status: "completed", effects: [] };
  },
});

export function TeamSignInReminder() {
  const t = useT();
  const live = useSyncExternalStore(network.subscribe, network.get);
  const hidden = useSyncExternalStore(later.subscribe, later.get);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const shown = needsSignIn(live.value) && !hidden;
  const ui = useUiTarget(shown ? { ref: { region: "main", view: "main.notice", kind: "teamReminder" },
                                   label: t("targets.signInReminder"), socket: { rail: "rail.main", side: "right" } }
                               : null);

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
    try {
      await signInAgain();
      byHand("settings.open", { section: "team" });   // the code to check, or the address to give
    } catch (e) { setProblem((e as Error).message); }
    setBusy(false);
  };
  return (
    <div id="team-reminder" role="status" ref={(el) => { box.current = el; ui.whole(el); }}>
      <ServerIcon />
      <p ref={ui.part("text")}>{t("left.reminder.text")}</p>
      <button type="button" className="settings-btn primary" disabled={busy} data-ui-action="sign-in"
              ref={ui.part("signIn")} onClick={start}>
        {t("left.reminder.signIn")}</button>
      <button type="button" className="settings-btn" ref={ui.part("later")}
              {...bindingProps(bind("reminder.later", {}))}>{t("left.reminder.later")}</button>
      {/* The backend's own words, as they came: original detail. */}
      {problem && <p className="team-reminder-problem" data-original-detail="">{problem}</p>}
    </div>
  );
}
