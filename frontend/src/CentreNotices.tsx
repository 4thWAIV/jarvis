/**
 * The notices at the top of the centre area, under its tabs, one above the
 * other: the sign-in reminder (TeamSignInReminder.tsx) and updates waiting
 * (UpdatesNotice.tsx). The stack sets --centre-notice-height to its own
 * height while anything is in it, so every centre view starts below it and
 * nothing is covered, the conversation's input included.
 */

import { useLayoutEffect, useRef } from "react";
import { TeamSignInReminder } from "./TeamSignInReminder";
import { UpdatesNotice } from "./UpdatesNotice";

// The space kept between the notices and the view below them.
const GAP_PX = 8;

export function CentreNotices() {
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const root = document.documentElement.style;
    const fit = () => {
      if (el.offsetHeight > 0) root.setProperty("--centre-notice-height", `${el.offsetHeight + GAP_PX}px`);
      else root.removeProperty("--centre-notice-height");
    };
    fit();
    const watch = new ResizeObserver(fit);
    watch.observe(el);
    return () => { watch.disconnect(); root.removeProperty("--centre-notice-height"); };
  }, []);
  return (
    <div id="centre-notices" ref={box}>
      <TeamSignInReminder />
      <UpdatesNotice />
    </div>
  );
}
