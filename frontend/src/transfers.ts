/**
 * Transfers: every message the rays draw, an agent's or a person's, as one
 * kind of thing (RayLane.tsx draws them all the same way).
 *
 *   - identity: `agent:<n>` for JARVIS's n-th message to an agent this page
 *     has seen (agents.ts's `agentMessage`), and the event's own id
 *     (`human:<uuid>`, the same on every node) for a person's message, so
 *     the two can never collide.
 *   - a person's transfer arrives as a `human_transfer` frame on
 *     /ws/sessions (`presentHumanTransfer`). It asks for a ray and nothing
 *     else: whether a message was received or read comes only from the
 *     Queue's records. Each event is drawn once in this page's life; one
 *     marked `replay` (re-sent to a page that has just reconnected) is only
 *     noted as seen, never drawn, so a reconnect replays no animation. A
 *     snapshot or a hint never draws anything.
 *   - motion: one policy for every ray (`transferMotion`). The quiet page
 *     draws none (as it always has); reduced motion draws a still route
 *     with no pulses, rings or orb movement.
 */

import { agentMessage } from "./agents";
import type { TransferEvent } from "./messagingContract";
import { network } from "./network";
import { createStore } from "./store";
import { agentEndpoint, queueEndpoint, type Endpoint } from "./transferEndpoints";
import { isQuiet } from "./voiceHealth";

export type Direction = "outgoing" | "incoming";

export interface Transfer {
  id: string;
  direction: Direction;
  end: Endpoint;
  /** Said by screen readers while the ray shows; empty for none. */
  words: string;
}

/** How many rays show at once; more are counted, not drawn. */
export const RAYS_SHOWN_MAX = 5;

// ── a person's transfers ─────────────────────────────────────────────────

/** How many event ids the page remembers having seen. */
const SEEN_MAX = 500;
const seen: string[] = [];
const seenSet = new Set<string>();

/** The latest person's transfer to draw. */
const humanTransfer = createStore<TransferEvent | null>(null);

/** A `human_transfer` frame's event: drawn if it is new and live. */
export function presentHumanTransfer(event: TransferEvent): void {
  if (!event || typeof event.event_id !== "string" || seenSet.has(event.event_id)) return;
  seenSet.add(event.event_id);
  seen.push(event.event_id);
  if (seen.length > SEEN_MAX) seenSet.delete(seen.shift()!);
  if (event.replay) return;
  humanTransfer.set(event);
}

/** Who a person is, as the page knows them: their login from the team's
 *  network snapshot; "a teammate" when it does not say. */
function personWords(githubId: number): string {
  const nodes = network.get().value?.snapshot?.nodes ?? [];
  return nodes.find((n) => n.owner_github_id === githubId)?.owner_login ?? "a teammate";
}

/** What a person's transfer is, in words: direction, and who. */
export function humanWords(event: TransferEvent): string {
  if (event.sender_github_id === event.recipient_github_id) {
    return event.direction === "outgoing" ? "Sending a message to yourself"
                                          : "Your message to yourself is arriving";
  }
  return event.direction === "outgoing"
    ? `Sending a message to ${personWords(event.recipient_github_id)}`
    : `A message from ${personWords(event.sender_github_id)} is arriving`;
}

function humanToTransfer(event: TransferEvent): Transfer {
  return { id: event.event_id, direction: event.direction,
           end: queueEndpoint(event.thread_id), words: humanWords(event) };
}

// ── every transfer ───────────────────────────────────────────────────────

/** Calls `listener` once for every transfer to draw, agents' and people's
 *  alike; each store calls its listeners once per value, so several sent at
 *  once each draw. */
export function onTransfer(listener: (transfer: Transfer) => void): () => void {
  const fromAgents = agentMessage.subscribe(() => {
    const message = agentMessage.get();
    if (message) listener({ id: `agent:${message.n}`, direction: "outgoing",
                            end: agentEndpoint(message.sessionId, message.n), words: "" });
  });
  const fromPeople = humanTransfer.subscribe(() => {
    const event = humanTransfer.get();
    if (event) listener(humanToTransfer(event));
  });
  return () => { fromAgents(); fromPeople(); };
}

export type Motion = "animate" | "still" | "none";

/** The one suppression policy for every ray. */
export function transferMotion(): Motion {
  if (isQuiet()) return "none";
  if (typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return "still";
  }
  return "animate";
}
