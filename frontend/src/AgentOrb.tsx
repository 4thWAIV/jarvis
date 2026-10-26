/**
 * A small orb at the left of an agent's box in the agents list: the same orb
 * as the big one (orb.ts), with fewer particles and a transparent background.
 *
 * What it shows is decided in one place, `agentOrbLook`: an agent the user
 * has not activated is entirely gray; an active one is idle when its session
 * is idle and thinking in every other state.
 *
 * When a message's ray reaches the agent (RayLane.tsx), its orb goes into the
 * receiving state in the ray's colour for as long as the ray still shows.
 */

import { useEffect, useRef, useSyncExternalStore } from "react";
import { isQuiet, quietUntil } from "./voiceHealth";
import { agentReceipt, type AgentRow } from "./agents";
import { createOrb, type Orb, type OrbState } from "./orb";

// Enough to read as the same orb at this size, few enough that a column of
// them costs little to draw.
const AGENT_ORB_PARTICLES = 300;

export function agentOrbLook(agent: AgentRow): { gray: boolean; state: OrbState } {
  return { gray: !agent.active, state: agent.state === "idle" ? "idle" : "thinking" };
}

export function AgentOrb({ agent }: { agent: AgentRow }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const orbRef = useRef<Orb | null>(null);
  const look = agentOrbLook(agent);
  const quiet = useSyncExternalStore(quietUntil.subscribe, isQuiet);

  useEffect(() => {
    orbRef.current = createOrb(canvasRef.current!, {
      particles: AGENT_ORB_PARTICLES, transparent: true });
    return () => {
      orbRef.current?.destroy();
      orbRef.current = null;
    };
  }, []);

  // Rows are reused as the list cycles, so the orb follows its agent: a
  // receipt for the agent this row shows now, and none carried over from the
  // agent it showed before.
  useEffect(() => {
    orbRef.current?.endTransmission();
    return agentReceipt.subscribe(() => {
      const receipt = agentReceipt.get();
      if (receipt?.sessionId === agent.session_id) {
        orbRef.current?.receive(receipt.colour, receipt);
      }
    });
  }, [agent.session_id]);

  useEffect(() => {
    orbRef.current?.setGray(look.gray);
    orbRef.current?.setState(look.state);
  }, [look.gray, look.state]);

  useEffect(() => { orbRef.current?.setPaused(quiet); }, [quiet]);

  return <canvas className="agent-orb" ref={canvasRef}
                 data-orb-state={look.state} data-orb-gray={look.gray} />;
}
