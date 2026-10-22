/**
 * The ⋮ menu in the top right: Settings (Settings.tsx) and Restart Server.
 * It closes on any click elsewhere.
 */

import { useEffect, useState } from "react";
import { openSettings } from "./Settings";
import { statusText } from "./voiceStatus";

// How long a restart is given before the page reloads itself.
const RELOAD_AFTER_MS = 4000;

export function MenuControl() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [open]);

  const restart = async () => {
    setOpen(false);
    statusText.set("restarting...");
    try {
      await fetch("/api/restart", { method: "POST" });
      setTimeout(() => window.location.reload(), RELOAD_AFTER_MS);
    } catch {
      statusText.set("restart failed");
    }
  };

  return (
    <>
      <div id="controls">
        <button id="btn-menu" className="control-button" title="Menu" aria-label="Menu"
                aria-expanded={open}
                onClick={(e) => { e.stopPropagation(); setOpen(!open); }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="12" cy="12" r="1" /><circle cx="12" cy="5" r="1" /><circle cx="12" cy="19" r="1" />
          </svg>
        </button>
      </div>
      {open && (
        <div id="menu-dropdown" onClick={(e) => e.stopPropagation()}>
          <button onClick={() => { setOpen(false); openSettings(); }}>Settings</button>
          <button onClick={restart}>Restart Server</button>
        </div>
      )}
    </>
  );
}
