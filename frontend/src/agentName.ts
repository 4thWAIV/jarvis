/**
 * The name the page shows for an agent. It is derived in one place on the
 * server (`agent_name` in session_watch.py) and stored with the agent; the
 * page only ever reads it through here.
 */

import type { AgentRow } from "./agents";

export function agentName(agent: AgentRow): string {
  return agent.name;
}
