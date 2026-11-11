/**
 * The page's one /ws/sessions socket, opened once for the page's life
 * (jarvis.ts), so its hints arrive whichever view is showing: the agents
 * list sits in a right-panel tab that is not always open.
 *
 *   - "agents" and "snapshot" (every (re)connect): the agents list reads
 *     /api/agents again (`agentsChanged`);
 *   - "workflows": the workflow views read again (workflows.ts);
 *   - "work_item", "work_item_history", "work_item_removed": one changed
 *     Work item, applied by id to the Work list and the open item
 *     (work.ts `applyWorkFrame`); every (re)connect reads the list again
 *     (`workResync`); "work" (an item's keys changed) re-reads only the
 *     open item's detail (`workChanged`);
 *   - "voice": the voice record's views read again (voiceHealth.ts);
 *   - "subsystems": a subsystem's state moved; the Diagnostics tab's list
 *     reads again (subsystems.ts), which also hears whether this socket is
 *     open, since the page's own connection is one of its items;
 *   - "network": the team server's network snapshot was stored again; the
 *     Network panel and the Team Server tile read it again (network.ts);
 *   - "folders": the folder catalog or what discovery found changed; both
 *     Folders views read it again (folders.ts);
 *   - "agent_message": JARVIS sent an agent a message; the ray is drawn to
 *     it (RayLane.tsx) and the list brings it into view (`agentMessage`);
 *   - "messages" (and every (re)connect): the queue reads
 *     /api/team/messages again (queue.ts);
 *   - "human_transfer": a person's message went or came; its ray is drawn
 *     once (transfers.ts), and a replayed one only reconciles;
 *   - "language" (and the language on every (re)connect's snapshot): the
 *     language in force changed; the interface follows it (locale.ts).
 *   - "packages" (and every (re)connect): a work package was unpacked; the
 *     list Open page is offered from reads again (packages.ts).
 *   - "voice_input_settings" (and every (re)connect): his two voice input
 *     settings changed; voice input reads them again (voiceSettings.ts).
 *   - "attention" (and every (re)connect): the attention record changed;
 *     updates waiting read again (attention.ts).
 *   - "projects" (and every (re)connect): a project record changed; the
 *     Projects list and the open project read again (projects.ts).
 *   - "inbox" (and every (re)connect): a message to him arrived or moved, or
 *     he answered one on a Work item; the Inbox, the Work rows' counts and
 *     an open item's Messages tab read again (messagesToHim.ts).
 */

import { agentMessage, agentsChanged } from "./agents";
import { attentionChanged } from "./attention";
import { foldersChanged } from "./folders";
import { INBOX_HINT, inboxChanged } from "./messagesToHim";
import { knowledgeChanged } from "./knowledge";
import { projectsChanged } from "./projects";
import { MESSAGES_HINT, TRANSFER_FRAME, type TransferEvent } from "./messagingContract";
import { reported as languageReported } from "./locale";
import { networkChanged } from "./network";
import { packagesChanged } from "./packages";
import { bundlesChanged } from "./bundles";
import { BUNDLES_HINT } from "./bundleContract";
import { messagesChanged } from "./queue";
import { sessionsConnected, subsystemsChanged } from "./subsystems";
import { presentHumanTransfer } from "./transfers";
import { countSessionMessage, voiceChanged } from "./voiceHealth";
import { applyWorkFrame, WORK_FRAMES, workChanged, workResync, type WorkFrame } from "./work";
import { workflowsChanged } from "./workflows";
import { createSocket } from "./ws";
import { VOICE_INPUT_SETTINGS_FRAME } from "./voiceControl";
import { voiceInputSettingsChanged } from "./voiceSettings";

const bump = (store: { get(): number; set(v: number): void }) => store.set(store.get() + 1);

export function startSessionsSocket(): void {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = createSocket(`${proto}://${location.host}/ws/sessions`);
  socket.onOpen(() => sessionsConnected.set(true));
  socket.onClose(() => sessionsConnected.set(false));
  socket.onMessage((msg) => {
    countSessionMessage();
    const snapshot = msg.type === "snapshot";
    if (msg.type === "language" || (snapshot && msg.language !== undefined)) languageReported(msg.language);
    if (msg.type === "agents" || snapshot) bump(agentsChanged);
    if (msg.type === "voice" || snapshot) bump(voiceChanged);
    if (msg.type === VOICE_INPUT_SETTINGS_FRAME || snapshot) voiceInputSettingsChanged();
    if (msg.type === "subsystems" || snapshot) bump(subsystemsChanged);
    // A (re)connect may have missed a workflows hint, so it reads again too.
    if (msg.type === "workflows" || snapshot) bump(workflowsChanged);
    if (snapshot) bump(workResync);
    // An item's keys changed: only the open item's detail reads again.
    if (msg.type === "work") bump(workChanged);
    if (WORK_FRAMES.includes(msg.type as WorkFrame["type"])) applyWorkFrame(msg as unknown as WorkFrame);
    if (msg.type === "network" || snapshot) bump(networkChanged);
    if (msg.type === "folders" || snapshot) bump(foldersChanged);
    if (msg.type === "knowledge" || snapshot) bump(knowledgeChanged);
    if (msg.type === "projects" || snapshot) bump(projectsChanged);
    if (msg.type === MESSAGES_HINT || snapshot) bump(messagesChanged);
    if (msg.type === "packages" || snapshot) bump(packagesChanged);
    if (msg.type === "attention" || snapshot) bump(attentionChanged);
    if (msg.type === INBOX_HINT || snapshot) bump(inboxChanged);
    if (msg.type === BUNDLES_HINT || snapshot) bump(bundlesChanged);
    if (msg.type === "agent_message" && typeof msg.session_id === "string") {
      agentMessage.set({ sessionId: msg.session_id, n: (agentMessage.get()?.n ?? 0) + 1 });
    }
    if (msg.type === TRANSFER_FRAME && msg.event) presentHumanTransfer(msg.event as TransferEvent);
  });
}
