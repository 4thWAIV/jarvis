/**
 * The rays: when JARVIS sends a message to an agent, a ray like a fibre-optic
 * cable carries it. It runs in right angles: up out of the main orb (upper
 * left), along the strip above the centre area's tabs, across to the lane at
 * the page's right edge, down the channel in the middle of that lane (the
 * same channel for every ray), and left into that agent's own orb at the
 * right end of its row in the right panel's Agents tab. When another tab is
 * open there, the ray ends at the Agents tab's icon instead, which stays lit
 * until the tab is opened. It is drawn behind the orbs, from the main
 * orb's centre, and the main orb spins up and fills with the ray's colour
 * from the centre outward as it goes. Pulses travel along it, and it fades out
 * after six seconds. When the first pulse reaches the agent, the agent's orb
 * fills with the ray's colour from its edge inward, leaning toward the ray,
 * and rings ripple out from it: the message arriving. Its colour belongs to the transmission, not the agent
 * (rayColours.ts): the first ray showing is the primary colour, a second at
 * the same time the next.
 */

import { useEffect, useState } from "react";
import { isQuiet } from "./voiceHealth";
import { mainOrb } from "./OrbControl";
import { takeRayColour } from "./rayColours";
import { agentMessage, agentReceipt, agentsLaidOut, agentsTabLit, RAY_MS,
         RAY_TRAVEL_MS } from "./agents";

const FIND_TRIES = 30;          // frames to wait for the row to come into view
const RECEIPT_RING_R = 10;      // px; a ring grows from about the agent orb's size
const RECEIPT_RING_GAP_MS = 350;   // between the two rings

interface Ray { n: number; sessionId: string; d: string; colour: string }

/** Where a ray leaves the orb: its centre. The ray is drawn behind the orb
 *  (lower in the page than the left column), so it comes out from inside. */
export function rayStart(orb: DOMRect): [number, number] {
  return [orb.left + orb.width / 2, orb.top + orb.height / 2];
}

// How far below the top of the page the ray crosses: above the centre
// area's tabs (which start at --centre-header-top, 16px).
const TOP_LANE_PX = 8;

/** The numbers in a ray's path: start x and y, the top lane's y, the
 *  channel's x, then the end's y and x (rayPath). */
function rayPoints(d: string): number[] {
  return (d.match(/-?[\d.]+/g) ?? []).map(Number);
}

/** Which way the ray leaves the orb, on the screen: from its start to its
 *  first corner (the channel). */
function rayDirection(d: string): [number, number] {
  const [, sy, top] = rayPoints(d);
  return [0, Math.sign(top - sy) || -1];
}

/** Which way the ray arrives from at the agent's orb: from its end back to
 *  its last corner (the channel). */
function rayArrival(d: string): [number, number] {
  const [, , , cx, , ex] = rayPoints(d);
  return [Math.sign(cx - ex) || 1, 0];
}

/** Where the ray ends: the agent's orb's centre. */
function rayEnd(d: string): [number, number] {
  const [, , , , ey, ex] = rayPoints(d);
  return [ex, ey];
}

/** The path, in right angles: up from the orb to the top lane, across it
 *  to the right-hand channel, down the channel, and left into the agent's
 *  orb; or, with the Agents tab closed, into its icon (`toTab`). Null while
 *  the tab is open but the agent's row is not showing yet. */
function rayPath(sessionId: string): { d: string; toTab: boolean } | null {
  const orb = document.getElementById("orb-control")?.getBoundingClientRect();
  const column = document.getElementById("right-column")?.getBoundingClientRect();
  const list = document.querySelector(".agents-list");
  const row = list?.querySelector(`li[data-session-id="${CSS.escape(sessionId)}"] .agent-orb`);
  const target = (list ? row : document.querySelector(".right-tabs [aria-label='Agents']"))
    ?.getBoundingClientRect();
  if (!orb || !target || !column) return null;
  const [sx, sy] = rayStart(orb);
  const ex = target.left + target.width / 2, ey = target.top + target.height / 2;
  if (!list) {
    // Straight down into the tab's icon from the top lane; the last run is
    // empty, so the path keeps the same six numbers (rayPoints).
    return { d: `M ${sx} ${sy} V ${TOP_LANE_PX} H ${ex} V ${ey} H ${ex}`, toTab: true };
  }
  // The middle of what is left of the right-hand lane beside the menu button
  // (#controls, in the top right corner), so the ray never runs through it.
  const menu = document.getElementById("controls")?.getBoundingClientRect();
  const inner = Math.max(column.right, menu?.right ?? 0);
  const channel = inner + (window.innerWidth - inner) / 2;
  return { d: `M ${sx} ${sy} V ${TOP_LANE_PX} H ${channel} V ${ey} H ${ex}`, toTab: false };
}

export function RayLane() {
  const [rays, setRays] = useState<Ray[]>([]);

  // Every message draws, including several sent at once: the store's own
  // listener is called once per message, where reading it during render
  // would see only the last.
  useEffect(() => {
    const frames = new Set<number>();
    const draw = (sessionId: string, n: number) => {
      if (isQuiet()) return;         // the quiet page draws no rays
      let tries = 0;
      // The list may first have to bring the agent into view, so look for
      // its row over a few frames.
      const find = () => {
        const path = rayPath(sessionId);
        if (path) {
          const { d } = path;
          const { colour, release } = takeRayColour();
          // The orb goes into its transmitting state for as long as the ray
          // shows, leaning the way the ray leaves it.
          mainOrb.current?.transmit(colour, { direction: rayDirection(d), seconds: RAY_MS / 1000 });
          setRays((now) => [...now, { n, sessionId, d, colour }]);
          // The agent's orb receives when the first pulse gets there, and
          // only if its row is still showing: measured again, since the list
          // may have moved.
          // The Agents tab's icon lights instead when another tab is open.
          setTimeout(() => {
            const there = rayPath(sessionId);
            if (there?.toTab) agentsTabLit.set(true);
            else if (there) agentReceipt.set({
              sessionId, n, colour, direction: rayArrival(there.d),
              seconds: (RAY_MS - RAY_TRAVEL_MS) / 1000 });
          }, RAY_TRAVEL_MS);
          setTimeout(() => {
            release();
            setRays((now) => now.filter((r) => r.n !== n));
          }, RAY_MS);
        } else if (++tries < FIND_TRIES) {
          frames.add(requestAnimationFrame(find));
        }
      };
      frames.add(requestAnimationFrame(find));
    };
    const unsubscribe = agentMessage.subscribe(() => {
      const message = agentMessage.get();
      if (message) draw(message.sessionId, message.n);
    });
    return () => {
      unsubscribe();
      frames.forEach(cancelAnimationFrame);
    };
  }, []);

  // A ray follows its agent while it shows: the list can reorder (a message
  // moves its agent up) or cycle under it. If the agent's row is not showing
  // the ray is not drawn: its last path would end at a row that now holds a
  // different agent.
  // It re-measures on every frame, and on every commit of the agents list
  // before that commit is painted.
  useEffect(() => {
    if (!rays.length) return;
    let frame = 0;
    const remeasure = () => setRays((now) => {
      let moved = false;
      const next = now.map((ray) => {
        const d = rayPath(ray.sessionId)?.d ?? "";
        if (d !== ray.d) { moved = true; return { ...ray, d }; }
        return ray;
      });
      return moved ? next : now;
    });
    const follow = () => {
      remeasure();
      frame = requestAnimationFrame(follow);
    };
    frame = requestAnimationFrame(follow);
    const unsubscribe = agentsLaidOut.subscribe(remeasure);
    return () => {
      cancelAnimationFrame(frame);
      unsubscribe();
    };
  }, [rays.length]);

  return (
    <svg id="ray-lane" aria-hidden="true">
      <defs>
        <filter id="ray-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>
      {rays.filter((ray) => ray.d).map((ray) => (
        <g key={ray.n} className="ray" data-ray-to={ray.n}
           style={{ ["--ray-colour" as string]: ray.colour }}>
          <path d={ray.d} className="ray-glow" filter="url(#ray-glow)" />
          <path d={ray.d} className="ray-core" />
          <path d={ray.d} className="ray-pulse" pathLength={100} />
          {[0, 1].map((ring) => (
            <circle key={ring} className="ray-receipt" cx={rayEnd(ray.d)[0]}
                    cy={rayEnd(ray.d)[1]} r={RECEIPT_RING_R}
                    style={{ animationDelay: `${RAY_TRAVEL_MS + ring * RECEIPT_RING_GAP_MS}ms` }} />
          ))}
        </g>
      ))}
    </svg>
  );
}
