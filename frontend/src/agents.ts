/**
 * The agents list: every running Claude Code session, by name, and whether
 * the user has activated it (server: run_store.sync_agents, /api/agents).
 */

import { createStore } from "./store";

/** The latest message JARVIS sent to an agent, for the ray (RayLane.tsx).
 *  `n` counts messages, so the same agent twice draws twice. */
export const agentMessage = createStore<{ sessionId: string; n: number } | null>(null);

/** How long a message's ray shows (matching the CSS fade). The list keeps
 *  the agent in view for as long. */
export const RAY_MS = 6000;

/** How long a pulse takes to run the whole ray (the CSS `ray-pulse`): the
 *  first one to leave the main orb reaches the agent this long after the ray
 *  appears, and that is when the agent's orb shows it received the message. */
export const RAY_TRAVEL_MS = 1400;

/** The latest message to reach an agent's orb, for that orb's receiving
 *  state (AgentOrb.tsx). `n` is the message's, as in agentMessage. */
export const agentReceipt = createStore<{
  sessionId: string; n: number; colour: string;
  direction: [number, number]; seconds: number;
} | null>(null);

/** Counts the agents list's commits to the page. Each one may have moved a
 *  row, and a ray re-measures on it before the page is painted, so it never
 *  shows ending at a row that now holds another agent. */
export const agentsLaidOut = createStore(0);

export interface AgentRow {
  session_id: string;
  name: string;
  active: boolean;
  /** The session's state as the session watcher derives it ("idle",
   *  "working", "needs_you", ...). */
  state: string;
  /** How many items its project's needs board holds. */
  needs: number;
  /** Who runs it, as a key of providers.ts's PROVIDERS ("anthropic"); null
   *  when the server could not tell. */
  provider: string | null;
}

export async function listAgents(): Promise<AgentRow[]> {
  const res = await fetch("/api/agents");
  if (!res.ok) throw new Error(`GET /api/agents: ${res.status}`);
  return ((await res.json()) as { agents: AgentRow[] }).agents;
}

export async function setAgentActive(sessionId: string, active: boolean): Promise<void> {
  const res = await fetch(`/api/agents/${encodeURIComponent(sessionId)}/active`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  });
  if (!res.ok) throw new Error(`POST active: ${res.status}`);
}
