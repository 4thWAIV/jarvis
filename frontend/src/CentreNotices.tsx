/**
 * The notices at the top of the centre area: the sign-in reminder
 * (TeamSignInReminder.tsx) and updates waiting (UpdatesNotice.tsx).
 *
 * On Conversation they stack under its tabs, one above the other, and the
 * stack sets --centre-notice-height to its own height while anything is in
 * it, so the conversation starts below it and nothing is covered, its input
 * included.
 *
 * On every other view (a presentation he is reading: a page package, a
 * workflow, a file) each is one small chip at the right end of the tab row,
 * and they reserve no height: --centre-notice-height stays 0 and the stack
 * sets --centre-chip-room instead, the room the tab row keeps clear on its
 * right for them, so they cover neither a tab nor the view. Nothing opens
 * by itself there; the updates chip's list opens only when clicked, over
 * the view.
 *
 * Which view is shown is the route state's (uiNavigation.ts `mainView`).
 */

import { useLayoutEffect, useRef, useSyncExternalStore } from "react";
import { TeamSignInReminder } from "./TeamSignInReminder";
import { UpdatesNotice } from "./UpdatesNotice";
import { mainView } from "./uiNavigation";

// The space kept between the notices and the view below them, or between
// the chips and the last tab.
const GAP_PX = 8;

export function CentreNotices() {
  const view = useSyncExternalStore(mainView.subscribe, mainView.get);
  const compact = view !== "conversation";
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const root = document.documentElement.style;
    const fit = () => {
      if (compact) {
        root.removeProperty("--centre-notice-height");
        if (el.offsetWidth > 0 && el.childElementCount > 0) root.setProperty("--centre-chip-room", `${el.offsetWidth + GAP_PX}px`);
        else root.removeProperty("--centre-chip-room");
      } else {
        root.removeProperty("--centre-chip-room");
        if (el.offsetHeight > 0) root.setProperty("--centre-notice-height", `${el.offsetHeight + GAP_PX}px`);
        else root.removeProperty("--centre-notice-height");
      }
    };
    fit();
    // A resize is only reported at the next frame; a notice added or removed
    // is reported before any other script runs, so the room is given back at
    // once, as it was when the reminder sized itself.
    const watch = new ResizeObserver(fit);
    watch.observe(el);
    const children = new MutationObserver(fit);
    children.observe(el, { childList: true, subtree: true });
    return () => {
      watch.disconnect();
      children.disconnect();
      root.removeProperty("--centre-notice-height");
      root.removeProperty("--centre-chip-room");
    };
  }, [compact]);
  return (
    <div id="centre-notices" ref={box} className={compact ? "compact" : undefined}>
      <TeamSignInReminder compact={compact} />
      <UpdatesNotice compact={compact} />
    </div>
  );
}
