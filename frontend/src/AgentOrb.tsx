/**
 * A small orb at the left of an agent's box in the agents list: the same orb
 * as the big one (orb.ts), with fewer particles and a transparent background.
 *
 * What it shows is decided in one place, `agentOrbLook`: an agent the user
 * has not activated is entirely gray; an active one is idle when its session
 * is idle and thinking in every other state.
 */

import { useEffect, useRef } from "react";
import type { AgentRow } from "./agents";
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

  useEffect(() => {
    orbRef.current = createOrb(canvasRef.current!, {
      particles: AGENT_ORB_PARTICLES, transparent: true });
    return () => {
      orbRef.current?.destroy();
      orbRef.current = null;
    };
  }, []);

  useEffect(() => {
    orbRef.current?.setGray(look.gray);
    orbRef.current?.setState(look.state);
  }, [look.gray, look.state]);

  return <canvas className="agent-orb" ref={canvasRef}
                 data-orb-state={look.state} data-orb-gray={look.gray} />;
}
