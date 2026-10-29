/**
 * The page's one /ws/sessions socket, opened once for the page's life
 * (jarvis.ts), so its hints arrive whichever view is showing: the agents
 * list sits in a right-panel tab that is not always open.
 *
 *   - "agents" and "snapshot" (every (re)connect): the agents list reads
 *     /api/agents again (`agentsChanged`);
 *   - "workflows": the workflow views read again (workflows.ts);
 *   - "voice": the voice record's views read again (voiceHealth.ts);
 *   - "subsystems": a subsystem's state moved; the Diagnostics tab's list
 *     reads again (subsystems.ts), which also hears whether this socket is
 *     open, since the page's own connection is one of its items;
 *   - "network": the team server's network snapshot was stored again; the
 *     Network panel and the Team Server tile read it again (network.ts);
 *   - "agent_message": JARVIS sent an agent a message; the ray is drawn to
 *     it (RayLane.tsx) and the list brings it into view (`agentMessage`).
 */

import { agentMessage, agentsChanged } from "./agents";
import { networkChanged } from "./network";
import { sessionsConnected, subsystemsChanged } from "./subsystems";
import { countSessionMessage, voiceChanged } from "./voiceHealth";
import { workflowsChanged } from "./workflows";
import { createSocket } from "./ws";

const bump = (store: { get(): number; set(v: number): void }) => store.set(store.get() + 1);

export function startSessionsSocket(): void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = createSocket(`${proto}://${location.host}/ws/sessions`);
  socket.onOpen(() => sessionsConnected.set(true));
  socket.onClose(() => sessionsConnected.set(false));
  socket.onMessage((msg) => {
    countSessionMessage();
    const snapshot = msg.type === "snapshot";
    if (msg.type === "agents" || snapshot) bump(agentsChanged);
    if (msg.type === "voice" || snapshot) bump(voiceChanged);
    if (msg.type === "subsystems" || snapshot) bump(subsystemsChanged);
    // A (re)connect may have missed a workflows hint, so it reads again too.
    if (msg.type === "workflows" || snapshot) bump(workflowsChanged);
    if (msg.type === "network" || snapshot) bump(networkChanged);
    if (msg.type === "agent_message" && typeof msg.session_id === "string") {
      agentMessage.set({ sessionId: msg.session_id, n: (agentMessage.get()?.n ?? 0) + 1 });
    }
  });
}
