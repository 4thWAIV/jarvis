/**
 * Each of JARVIS's major parts, as the Diagnostics tab lists them: read from
 * /api/diagnostics/subsystems (subsystem_diagnostics.py), read again on every
 * "subsystems" hint and every (re)connect snapshot on /ws/sessions, and on
 * every (re)connect of the voice socket (liveRead).
 *
 * The server judges every part but one: whether this page can reach JARVIS,
 * which only the page can see. It is down while the record cannot be read or
 * the /ws/sessions socket is closed (no hints arrive), and is added to the
 * server's list here (`withPage`).
 */

import { liveRead } from "./liveRead";
import { createStore } from "./store";
import { bind, detailSlot, registerClose, routeStore } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";
import { diagnosticsSlot } from "./voiceHealth";

export type SubsystemState = "ok" | "degraded" | "down" | "idle" | "unknown";

export interface Check { id: string; name: string; state: SubsystemState; detail: string }

/** One message to an agent, as the communications item lists them. */
export interface SentMessage {
  id: number; agentName: string; status: string; outcome: "reached" | "lost" | null;
  at: number; failure: string | null;
}

export interface Subsystem {
  id: string;
  name: string;
  state: SubsystemState;
  summary: string;
  checks: Check[];
  messages?: SentMessage[];
}

export const STATE_WORDS: Record<SubsystemState, string> = {
  ok: "Working", degraded: "Struggling", down: "Down", idle: "Idle", unknown: "No reading",
};

/** Moves on every "subsystems" hint on /ws/sessions. */
export const subsystemsChanged = createStore(0);

/** Whether /ws/sessions is open: null until it first opens or fails. */
export const sessionsConnected = createStore<boolean | null>(null);

async function getSubsystems(): Promise<{ now: number; subsystems: Subsystem[] }> {
  const res = await fetch("/api/diagnostics/subsystems");
  if (!res.ok) throw new Error(`GET /api/diagnostics/subsystems: ${res.status}`);
  return res.json();
}

export const subsystems = liveRead(getSubsystems, [subsystemsChanged]);

/** The page's own connection to JARVIS, and its graphics (orb.ts), as one
 *  more subsystem. */
export function pageSubsystem(readError: string | null, socket: boolean | null,
                              graphics: string | null = null): Subsystem {
  const checks: Check[] = [
    { id: "read", name: "Reading JARVIS", state: readError ? "down" : "ok",
      detail: readError ? `Can't read JARVIS (${readError})` : "Reading" },
    { id: "socket", name: "Live updates", state: socket === null ? "unknown" : socket ? "ok" : "down",
      detail: socket === null ? "Connecting" : socket ? "Connected" : "Disconnected; reconnecting" },
    { id: "graphics", name: "Graphics", state: graphics ? "degraded" : "ok",
      detail: graphics ?? "Drawing" },
  ];
  const down = checks.find((c) => c.state === "down");
  const degraded = checks.find((c) => c.state === "degraded");
  const state: SubsystemState = down ? "down" : checks.some((c) => c.state === "unknown") ? "unknown"
    : degraded ? "degraded" : "ok";
  return { id: "page", name: "This page", state,
           summary: (down ?? degraded)?.detail ?? "Connected to JARVIS", checks };
}

/** The server's list with this page's own subsystem at the end. */
export function withPage(list: Subsystem[] | undefined, readError: string | null,
                         socket: boolean | null, graphics: string | null = null): Subsystem[] {
  return [...(list ?? []), pageSubsystem(readError, socket, graphics)];
}

const subsystemSlot = detailSlot<string>("subsystem");

/** Voice input's row opens its own view (voice diagnostics) instead. */
export const VOICE_INPUT = "voice_input";

/** The subsystem whose detail the centre area shows, or null. Voice input
 *  has its own view (openDiagnostics in voiceHealth.ts). Writing it runs
 *  `subsystem.open` or `subsystem.close`, as a person's navigation. */
export const openSubsystem = routeStore(subsystemSlot.value, (id: string | null) =>
  id === null ? bind("subsystem.close", {}) : bind("subsystem.open", { subsystemId: id }));

// Opening a subsystem's row: one detail of the two at a time, as the
// Diagnostics tab's rows always did. Voice input opens voice diagnostics
// and closes any subsystem; any other closes voice diagnostics.
registerCommand("subsystem.open", {
  availability: ({ subsystemId }) => {
    // This page's own row has no detail (its button is disabled).
    if (subsystemId === "page") return { available: false, reason: "no-entity" };
    const now = subsystems.get().value;
    if (!now) return { available: false, reason: "not-loaded" };
    return now.subsystems.some((s) => s.id === subsystemId)
      ? { available: true } : { available: false, reason: "no-entity" };
  },
  execute({ subsystemId }) {
    const effects = subsystemId === VOICE_INPUT
      ? [...subsystemSlot.close(), ...diagnosticsSlot.show(true)]
      : [...diagnosticsSlot.close(), ...subsystemSlot.show(subsystemId)];
    return { status: "completed", effects: [...new Set(effects)] };
  },
});

registerClose("subsystem.close", subsystemSlot);
