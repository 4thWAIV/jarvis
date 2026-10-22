/**
 * The agents list: every running Claude Code session, by name, and whether
 * the user has activated it (server: run_store.sync_agents, /api/agents).
 */

import { createStore } from "./store";

/** The latest message JARVIS sent to an agent, for the ray (RayLane.tsx).
 *  `n` counts messages, so the same agent twice draws twice. */
export const agentMessage = createStore<{ sessionId: string; n: number } | null>(null);

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
