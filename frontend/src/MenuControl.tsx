/**
 * The ⋮ menu in the top right: Settings (Settings.tsx), Check for updates
 * (Settings at Updates) and Restart Server. It closes on any click elsewhere.
 *
 * Opening and closing it is the registered command `menu.toggle`, run the
 * same by his click and by the brain (uiRegistry.ts); Settings and Check for
 * updates run `settings.open`, which closes the menu. Restart Server stays
 * the server's own action and is never offered to the brain. The menu
 * button and the open menu are targets the brain can point at, hung from
 * the right rail.
 */

import { useEffect, useSyncExternalStore } from "react";
import { createStore } from "./store";
import { bind, bindingProps, type ReadStore } from "./uiNavigation";
import { byHand, registerCommand, useUiTarget } from "./uiRegistry";
import { notice } from "./voiceHealth";

/** @routeState Whether the menu is open: only this module changes it. */
const open = createStore(false);
export const menuOpen: ReadStore<boolean> = open;

/** Close the menu (Settings opening closes it): true if it was open. */
export function closeMenu(): boolean {
  const was = open.get();
  open.set(false);
  return was;
}

registerCommand("menu.toggle", {
  availability: () => ({ available: true }),
  execute({ open: wanted }) {
    const opened = wanted && !open.get();
    open.set(wanted);
    return { status: "completed", effects: opened ? ["menu.opened"] : [] };
  },
});

// How long a restart is given before the page reloads itself.
const RELOAD_AFTER_MS = 4000;

/** The server is restarting: say so, and reload the page once it is back.
 *  The menu's Restart Server and Updates' Update and restart both end here. */
export function reloadAfterRestart(said: string): void {
  notice("Status", said);
  setTimeout(() => window.location.reload(), RELOAD_AFTER_MS);
}

const SOCKET = { rail: "rail.right", side: "right" } as const;

export function MenuControl() {
  const shown = useSyncExternalStore(open.subscribe, open.get);
  const button = useUiTarget({ ref: { region: "overlay", view: "overlay.controls", kind: "menu" },
                               label: "Menu button", socket: SOCKET });
  const menu = useUiTarget(shown ? { ref: { region: "overlay", view: "overlay.menu", kind: "menu" },
                                     label: "Menu", socket: SOCKET } : null);

  useEffect(() => {
    if (!shown) return;
    const close = () => byHand("menu.toggle", { open: false });
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [shown]);

  const restart = async () => {
    closeMenu();
    try {
      await fetch("/api/restart", { method: "POST" });
      reloadAfterRestart("Restarting the server.");
    } catch {
      notice("Status", "The restart failed: the server did not answer.");
    }
  };

  // The click that opens it must not reach the document's listener that
  // closes it.
  const toggle = bindingProps(bind("menu.toggle", { open: !shown }));
  return (
    <>
      <div id="controls">
        {/* The button is the whole object: the box around it has no edge
            of its own for a socket. */}
        <button id="btn-menu" className="control-button" title="Menu" aria-label="Menu"
                aria-expanded={shown} ref={(el) => { button.whole(el); button.part("toggle")(el); }}
                {...toggle} onClick={(e) => { e.stopPropagation(); toggle.onClick(); }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="1" /><circle cx="12" cy="5" r="1" /><circle cx="12" cy="19" r="1" />
          </svg>
        </button>
      </div>
      {shown && (
        <div id="menu-dropdown" ref={menu.whole} data-ui-action="plumbing"
             onClick={(e) => e.stopPropagation()}>
          <button ref={menu.part("settings")} {...bindingProps(bind("settings.open", {}))}>Settings</button>
          <button ref={menu.part("updates")} {...bindingProps(bind("settings.open", { section: "updates" }))}>
            Check for updates</button>
          <button ref={menu.part("restart")} data-ui-action="restart" onClick={restart}>Restart Server</button>
        </div>
      )}
    </>
  );
}
