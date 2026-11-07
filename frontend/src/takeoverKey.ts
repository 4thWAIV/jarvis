/**
 * Option+Shift+T: the takeover key. Anywhere on JARVIS's own page but a
 * text field, it takes over with the workflow or page package the centre
 * shows, or restores the normal layout while something has taken over. It
 * runs the same registered commands as Take over and Restore layout
 * (uiNavigation.ts), as a person's navigation. Keys pressed inside a
 * package's frame never reach this page.
 */

import { commandAvailability, byHand } from "./uiRegistry";
import { layout } from "./uiNavigation";

/** What a key press would do: the command it runs, or null. Read by the
 *  physical key (Option turns T into another letter on a Mac). */
export function takeoverKeyCommand(e: {
  altKey: boolean; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; code: string; target: EventTarget | null;
}): "layout.takeover" | "layout.restore" | null {
  if (!e.altKey || !e.shiftKey || e.ctrlKey || e.metaKey || e.code !== "KeyT") return null;
  if (typing(e.target)) return null;
  if (layout.get().mode === "takeover") return "layout.restore";
  return commandAvailability("layout.takeover", {}).available ? "layout.takeover" : null;
}

/** Whether the key went to something he types into. */
function typing(target: EventTarget | null): boolean {
  const el = target as { tagName?: string; isContentEditable?: boolean } | null;
  if (!el || typeof el.tagName !== "string") return false;
  return el.isContentEditable === true || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName.toUpperCase());
}

/** Listen for the key on this page; returns the way to stop. */
export function startTakeoverKey(): () => void {
  const press = (e: KeyboardEvent) => {
    const command = takeoverKeyCommand(e);
    if (!command) return;
    e.preventDefault();
    byHand(command, {});
  };
  document.addEventListener("keydown", press);
  return () => document.removeEventListener("keydown", press);
}
