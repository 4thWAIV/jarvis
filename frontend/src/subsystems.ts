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
 *
 * Every name and line the server sends is its own English (for the brain)
 * and, beside it, a code and the values it names (`name_said`, `said`:
 * subsystem_diagnostics.py), which the page says in the interface's
 * language (`subsystemName`, `subsystemSummary`, `checkName`, `checkDetail`).
 * One with no code (a product's name, a figure, another program's words) is
 * shown as it came.
 */

import { liveRead } from "./liveRead";
import { t, type MessageKey } from "./locale";
import { rightEn } from "./messages/right.en";
import { createStore } from "./store";
import { bind, detailSlot, registerClose, routeStore } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";
import { diagnosticsSlot } from "./voiceHealth";
import { wordsOf } from "./workflows";

export type SubsystemState = "ok" | "degraded" | "down" | "idle" | "unknown";

/** One of JARVIS's own names or lines: its code and the values it names. */
export interface Said { code: string; values: Record<string, string | number> }

export interface Check {
  id: string; name: string; state: SubsystemState; detail: string;
  name_said?: Said | null; said?: Said | null;
}

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
  name_said?: Said | null;
  said?: Said | null;
}

export const STATE_WORDS = wordsOf<SubsystemState>(["ok", "degraded", "down", "idle", "unknown"],
  (s) => t(`right.subsystem.state.${s}`));

/** `said` in the language in force, from the catalog's
 *  `right.subsystem.<area>.<code>`, or `shown` (the server's English, or
 *  words no code stands for) when there is no code this page knows. */
function sayIn(area: "name" | "said", said: Said | null | undefined, shown: string): string {
  if (!said) return shown;
  const key = `right.subsystem.${area}.${said.code}`;
  if (!(key in rightEn)) return shown;
  return (t as (key: MessageKey, values: Said["values"]) => string)(key as MessageKey, said.values);
}

export const subsystemName = (s: Subsystem): string => sayIn("name", s.name_said, s.name);
export const checkName = (c: Check): string => sayIn("name", c.name_said, c.name);
export const checkDetail = (c: Check): string => sayIn("said", c.said, c.detail);

/** The row's one line; a failing check's is that check's name and detail. */
export function subsystemSummary(s: Subsystem): string {
  if (s.said?.code === "badCheck") {
    const bad = s.checks.find((c) => c.id === s.said!.values.check);
    if (bad) return `${checkName(bad)}: ${checkDetail(bad)}`;
  }
  return sayIn("said", s.said, s.summary);
}

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
  // Named and said by code, as the server's rows are, in the language in
  // force when it is made (each render); the graphics trouble is the
  // browser's own words, shown as they came.
  const said = (code: string, values: Said["values"] = {}): Said => ({ code, values });
  const check = (id: string, state: SubsystemState, line: Said | string): Check => {
    const c: Check = { id, name: id, state, detail: "", name_said: said(id),
                       said: typeof line === "string" ? null : line };
    return { ...c, name: checkName(c), detail: typeof line === "string" ? line : checkDetail(c) };
  };
  const checks: Check[] = [
    check("read", readError ? "down" : "ok",
          readError ? said("pageReadFailed", { error: readError }) : said("pageReading")),
    check("socket", socket === null ? "unknown" : socket ? "ok" : "down",
          said(socket === null ? "socketConnecting" : socket ? "connected" : "socketLost")),
    check("graphics", graphics ? "degraded" : "ok", graphics ?? said("graphicsOk")),
  ];
  const down = checks.find((c) => c.state === "down");
  const degraded = checks.find((c) => c.state === "degraded");
  const state: SubsystemState = down ? "down" : checks.some((c) => c.state === "unknown") ? "unknown"
    : degraded ? "degraded" : "ok";
  const bad = down ?? degraded;
  const line = bad ? (bad.said ?? null) : said("pageOk");
  const page: Subsystem = { id: "page", name: "", state, summary: bad?.detail ?? "", checks,
                            name_said: said("page"), said: line };
  return { ...page, name: subsystemName(page), summary: subsystemSummary(page) };
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
