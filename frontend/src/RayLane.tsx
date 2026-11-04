/**
 * The rays: when JARVIS sends a message, a ray like a fibre-optic cable
 * carries it, to an agent or to a person's queue item alike (transfers.ts).
 * It runs in right angles (rayGeometry.ts): up out of the main orb (upper
 * left), along the strip above the centre area's tabs, across to the lane at
 * the page's right edge, down the channel in the middle of that lane (the
 * same channel for every ray), and left into its end in the right panel: an
 * agent's own orb at the right end of its row in the Agents tab, or a queue
 * item's row (transferEndpoints.ts). When that view is not open, the ray ends
 * at its tab's icon instead; the Agents tab's stays lit until it is opened.
 * It is drawn behind the orbs, from the main orb's centre. Pulses travel
 * along it, and it fades out after six seconds.
 *
 * Outgoing, the main orb spins up and fills with the ray's colour from the
 * centre outward as it goes; when the first pulse reaches an agent, the
 * agent's orb fills with the ray's colour from its edge inward, leaning
 * toward the ray, and rings ripple out from the end: the message arriving.
 * Incoming (a person's message come in) is the same ray travelled the other
 * way: the pulses run toward the main orb, which receives when the first one
 * gets there, and the rings ripple out from it.
 *
 * Its colour belongs to the transmission, not the agent or the person
 * (rayColours.ts): the first ray showing is the primary colour, a second at
 * the same time the next. At most RAYS_SHOWN_MAX show; more are counted,
 * and an outgoing one counted still lights its tab's icon.
 */

import { useEffect, useState } from "react";
import { EffectLife, onFrame, onGeometryChange, VISUALLY_HIDDEN } from "./effectLife";
import { transferReceive, transferTransmit } from "./orbSource";
import { takeRayColour } from "./rayColours";
import { RAY_MS, RAY_TRAVEL_MS } from "./agents";
import { rayArrival, rayDirection, rayEnd, rayOrigin } from "./rayGeometry";
import { measure, type Endpoint } from "./transferEndpoints";
import { onTransfer, RAYS_SHOWN_MAX, transferMotion, type Direction, type Motion,
         type Transfer } from "./transfers";

const FIND_TRIES = 30;          // frames to wait for the end to come into view
const RECEIPT_RING_R = 10;      // px; a ring grows from about the agent orb's size
const RECEIPT_RING_GAP_MS = 350;   // between the two rings

interface Ray {
  id: string;
  end: Endpoint;
  direction: Direction;
  motion: Motion;
  d: string;
  colour: string;
  words: string;
}

export function RayLane() {
  const [rays, setRays] = useState<Ray[]>([]);
  const [overflow, setOverflow] = useState(0);

  // Every transfer draws, including several sent at once: the sources'
  // listeners are called once per message, where reading a store during
  // render would see only the last.
  useEffect(() => {
    const showing = new Set<EffectLife>();
    let shown = 0;

    // An outgoing message whose view is not open lights its tab's icon
    // when it gets there (the Agents tab's stays lit until it is opened),
    // drawn or only counted.
    const reachedTab = (transfer: Transfer, colour: string, seconds: number): boolean => {
      if (!measure(transfer.end)?.toTab) return false;
      transfer.end.reached(true, { colour, direction: [0, 0], seconds });
      return true;
    };

    const draw = (transfer: Transfer) => {
      const motion = transferMotion(transfer);
      if (motion === "none") return;          // the quiet page draws no rays
      const life = new EffectLife();
      showing.add(life);
      const finish = () => { life.end(); showing.delete(life); };
      if (shown >= RAYS_SHOWN_MAX) {
        // Counted, not drawn, and only for as long as a ray would show.
        setOverflow((n) => n + 1);
        if (transfer.direction === "outgoing") {
          life.after(RAY_TRAVEL_MS, () => { reachedTab(transfer, "", 0); });
        }
        life.after(RAY_MS, () => { setOverflow((n) => n - 1); finish(); });
        return;
      }
      shown++;
      life.hold(() => { shown--; });
      let tries = 0;
      // The view may first have to bring the end into view, so look for it
      // over a few frames.
      const find = () => {
        const path = measure(transfer.end);
        if (!path) {
          if (++tries < FIND_TRIES) life.frame(find);
          else finish();
          return;
        }
        const { d } = path;
        const { colour, release } = takeRayColour();
        life.hold(release);
        const moving = motion === "animate";
        const outgoing = transfer.direction === "outgoing";
        // Outgoing, the orb goes into its transmitting state for as long as
        // the ray shows, leaning the way the ray leaves it.
        if (moving && outgoing) {
          transferTransmit(colour, { direction: rayDirection(d), seconds: RAY_MS / 1000 });
        }
        const ray: Ray = { id: transfer.id, end: transfer.end, direction: transfer.direction,
                           motion, d, colour, words: transfer.words };
        setRays((now) => [...now, ray]);
        life.hold(() => setRays((now) => now.filter((r) => r.id !== ray.id)));
        // When the first pulse gets there: the end receives an outgoing
        // message, measured again since the view may have moved (its tab's
        // icon lights instead when another tab is open); the main orb
        // receives an incoming one.
        life.after(RAY_TRAVEL_MS, () => {
          const seconds = (RAY_MS - RAY_TRAVEL_MS) / 1000;
          if (!outgoing) {
            if (moving) transferReceive(colour, { direction: rayDirection(d), seconds });
            return;
          }
          if (reachedTab(transfer, colour, seconds)) return;
          const there = measure(transfer.end);
          if (there && moving) {
            transfer.end.reached(false, { colour, direction: rayArrival(there.d), seconds });
          }
        });
        life.after(RAY_MS, finish);
      };
      life.frame(find);
    };

    const unsubscribe = onTransfer(draw);
    return () => {
      unsubscribe();
      showing.forEach((life) => life.end());
      showing.clear();
    };
  }, []);

  // A ray follows its end while it shows: a list can reorder (a message
  // moves its agent up) or cycle under it. An agent's ray whose row is not
  // showing is not drawn: its last path would end at a row that now holds a
  // different agent; a queue item's ends at the Queue's icon instead.
  // It re-measures on every frame, and on every commit of a list before
  // that commit is painted.
  useEffect(() => {
    if (!rays.length) return;
    const remeasure = () => setRays((now) => {
      let moved = false;
      const next = now.map((ray) => {
        const d = measure(ray.end)?.d ?? "";
        if (d !== ray.d) { moved = true; return { ...ray, d }; }
        return ray;
      });
      return moved ? next : now;
    });
    const stopFrames = onFrame(remeasure);
    const stopGeometry = onGeometryChange(remeasure);
    return () => {
      stopFrames();
      stopGeometry();
    };
  }, [rays.length]);

  const words = rays.map((ray) => ray.words).filter(Boolean);
  if (overflow) words.push(`and ${overflow} more`);
  return (
    <>
      <svg id="ray-lane" aria-hidden="true">
        <defs>
          <filter id="ray-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3" />
          </filter>
        </defs>
        {rays.filter((ray) => ray.d).map((ray) => {
          const incoming = ray.direction === "incoming";
          const [rx, ry] = incoming ? rayOrigin(ray.d) : rayEnd(ray.d);
          return (
            <g key={ray.id} className="ray" data-ray-to={ray.id} data-direction={ray.direction}
               data-motion={ray.motion} style={{ ["--ray-colour" as string]: ray.colour }}>
              <path d={ray.d} className="ray-glow" filter="url(#ray-glow)" />
              <path d={ray.d} className="ray-core" />
              {ray.motion === "animate" && <>
                <path d={ray.d} className="ray-pulse" pathLength={100}
                      style={incoming ? { animationDirection: "reverse" } : undefined} />
                {[0, 1].map((ring) => (
                  <circle key={ring} className="ray-receipt" cx={rx} cy={ry} r={RECEIPT_RING_R}
                          style={{ animationDelay: `${RAY_TRAVEL_MS + ring * RECEIPT_RING_GAP_MS}ms` }} />
                ))}
              </>}
            </g>
          );
        })}
        {overflow > 0 && rays[0]?.d && (
          <text className="ray-overflow" x={rayOrigin(rays[0].d)[0] + 8} y={18}
                fill="rgba(229, 231, 235, 0.9)" fontSize={11}>+{overflow}</text>
        )}
      </svg>
      <div id="ray-status" role="status" style={VISUALLY_HIDDEN}>{words.join(". ")}</div>
    </>
  );
}
