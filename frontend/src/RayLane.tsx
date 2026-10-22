/**
 * The lane on the far left of the page, kept clear for one thing: when JARVIS
 * sends a message to an agent, a ray like a fibre-optic cable carries it. It
 * runs in right angles: out of the main orb to the left, into the channel
 * down the middle of the lane (the same channel for every ray), down it, and
 * right into that agent's own orb. It is drawn behind the orbs, from the main
 * orb's centre, and the main orb spins up and fills with the ray's colour
 * from the centre outward as it goes. Pulses travel along it, and it fades out
 * after six seconds. Its colour belongs to the transmission, not the agent
 * (rayColours.ts): the first ray showing is the primary colour, a second at
 * the same time the next.
 */

import { useEffect, useState } from "react";
import { mainOrb } from "./OrbControl";
import { takeRayColour } from "./rayColours";
import { agentMessage } from "./agents";

const RAY_MS = 6000;            // how long a ray stays, matching the CSS fade
const FIND_TRIES = 30;          // frames to wait for the row to come into view

interface Ray { n: number; sessionId: string; d: string; colour: string }

/** Where a ray leaves the orb: its centre. The ray is drawn behind the orb
 *  (lower in the page than the left column), so it comes out from inside. */
export function rayStart(orb: DOMRect): [number, number] {
  return [orb.left + orb.width / 2, orb.top + orb.height / 2];
}

/** Which way the ray leaves the orb, on the screen: from its start to its
 *  first corner (the channel). */
function rayDirection(d: string): [number, number] {
  const [sx, , cx] = (d.match(/-?[\d.]+/g) ?? []).map(Number);
  return [Math.sign(cx - sx) || -1, 0];
}

/** The path, in right angles: left from the orb into the channel, down the
 *  channel, and right into the agent's orb. */
function rayPath(sessionId: string): string | null {
  const orb = document.getElementById("orb-control")?.getBoundingClientRect();
  const target = document.querySelector(
    `.agents-list li[data-session-id="${CSS.escape(sessionId)}"] .agent-orb`)
    ?.getBoundingClientRect();
  const column = document.getElementById("left-column")?.getBoundingClientRect();
  if (!orb || !target || !column) return null;
  const channel = column.left / 2;                    // the middle of the lane
  const [sx, sy] = rayStart(orb);
  const ex = target.left + target.width / 2, ey = target.top + target.height / 2;
  return `M ${sx} ${sy} H ${channel} V ${ey} H ${ex}`;
}

export function RayLane() {
  const [rays, setRays] = useState<Ray[]>([]);

  // Every message draws, including several sent at once: the store's own
  // listener is called once per message, where reading it during render
  // would see only the last.
  useEffect(() => {
    const frames = new Set<number>();
    const draw = (sessionId: string, n: number) => {
      let tries = 0;
      // The list may first have to bring the agent into view, so look for
      // its row over a few frames.
      const find = () => {
        const d = rayPath(sessionId);
        if (d) {
          const { colour, release } = takeRayColour();
          // The orb goes into its transmitting state for as long as the ray
          // shows, leaning the way the ray leaves it.
          mainOrb.current?.transmit(colour, { direction: rayDirection(d), seconds: RAY_MS / 1000 });
          setRays((now) => [...now, { n, sessionId, d, colour }]);
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
  // moves its agent up) or cycle under it. If the row goes out of view the
  // ray keeps the last path it had.
  useEffect(() => {
    if (!rays.length) return;
    let frame = 0;
    const follow = () => {
      setRays((now) => {
        let moved = false;
        const next = now.map((ray) => {
          const d = rayPath(ray.sessionId);
          if (d && d !== ray.d) { moved = true; return { ...ray, d }; }
          return ray;
        });
        return moved ? next : now;
      });
      frame = requestAnimationFrame(follow);
    };
    frame = requestAnimationFrame(follow);
    return () => cancelAnimationFrame(frame);
  }, [rays.length]);

  return (
    <svg id="ray-lane" aria-hidden="true">
      <defs>
        <filter id="ray-glow" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>
      {rays.map((ray) => (
        <g key={ray.n} className="ray" data-ray-to={ray.n}
           style={{ ["--ray-colour" as string]: ray.colour }}>
          <path d={ray.d} className="ray-glow" filter="url(#ray-glow)" />
          <path d={ray.d} className="ray-core" />
          <path d={ray.d} className="ray-pulse" pathLength={100} />
        </g>
      ))}
    </svg>
  );
}
