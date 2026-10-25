/**
 * The pictures the workflow views are drawn with, shared by the Workflows
 * list (WorkflowsView.tsx) and one workflow's detail (WorkflowDetail.tsx):
 *
 *   - WorkflowOrb: the same orb as the agents' (orb.ts), in light red, the
 *     colour of a workflow. It moves with the workflow's state: it spins
 *     while running, rests while waiting, pulses when it needs him, goes
 *     dark and cool when blocked, and gray when it has gone quiet or ended;
 *     a ring round it says the state in its own colour as well;
 *   - PhaseTrack: one segment per phase, done, current or still to come;
 *   - Ring: a progress ring with its count in the middle;
 *   - OpenIcons: open decisions, blockers and waits as coloured icons with
 *     their counts;
 *   - AgeBar: how long something has been open, as a filling bar.
 */

import { useEffect, useRef } from "react";
import { createOrb, type Orb, type OrbPalette, type OrbState } from "./orb";
import { FINISHED, type Workflow, type WorkflowItem, type WorkflowState } from "./workflows";

// Light red, the colour of a workflow, in the shades each orb state settles to.
export const WORKFLOW_PALETTE: OrbPalette = {
  idle: 0xfb7185, thinking: 0xfda4af, speaking: 0xff8fa3, compacting: 0x9f4a5a,
};

const ORB_LOOK: Record<WorkflowState, { state: OrbState; gray: boolean }> = {
  running: { state: "thinking", gray: false },
  waiting: { state: "idle", gray: false },
  needs_you: { state: "speaking", gray: false },
  blocked: { state: "compacting", gray: false },
  silent: { state: "idle", gray: true },
  succeeded: { state: "idle", gray: false },
  failed: { state: "idle", gray: true },
  cancelled: { state: "idle", gray: true },
};

export function WorkflowOrb({ state, size, particles = 300 }: {
  state: WorkflowState; size: number; particles?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const orbRef = useRef<Orb | null>(null);
  const look = ORB_LOOK[state];

  useEffect(() => {
    orbRef.current = createOrb(canvasRef.current!, {
      particles, transparent: true, palette: WORKFLOW_PALETTE });
    return () => {
      orbRef.current?.destroy();
      orbRef.current = null;
    };
  }, [particles]);

  useEffect(() => {
    orbRef.current?.setGray(look.gray);
    orbRef.current?.setState(look.state);
  }, [look.gray, look.state]);

  return (
    <span className="workflow-orb" data-state={state} style={{ width: size, height: size }}>
      <canvas ref={canvasRef} data-orb-state={look.state} data-orb-gray={look.gray} />
    </span>
  );
}

/** Where each phase stands, from the workflow's items and its current phase. */
export function phaseStates(w: Workflow): { item: WorkflowItem; at: "done" | "current" | "failed" | "todo" }[] {
  return w.items.filter((i) => i.kind === "phase").map((item) => ({
    item,
    at: item.state === "done" || item.state === "skipped" ? "done"
      : item.state === "failed" ? "failed"
      : item.slug === w.phase && !FINISHED.has(w.state) ? "current" : "todo",
  }));
}

export function PhaseTrack({ w, labels = false }: { w: Workflow; labels?: boolean }) {
  const phases = phaseStates(w);
  if (!phases.length) return null;
  return (
    <span className={`phase-track${labels ? " labelled" : ""}`} role="list" aria-label="Phases">
      {phases.map(({ item, at }) => (
        <span key={item.slug} role="listitem" className={`phase-segment at-${at}`}
              data-phase={item.slug} title={`${item.title}: ${at}`}>
          {labels && <span className="phase-label">{item.title}</span>}
        </span>
      ))}
    </span>
  );
}

export function Ring({ done, total, size = 40, label }: {
  done: number; total: number; size?: number; label?: string }) {
  const share = total > 0 ? Math.min(1, done / total) : 0;
  const r = size / 2 - 3;
  const c = 2 * Math.PI * r;
  return (
    <span className="workflow-ring" style={{ width: size, height: size }} role="progressbar"
          aria-valuemin={0} aria-valuemax={total} aria-valuenow={done} aria-label={label}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle className="ring-track" cx={size / 2} cy={size / 2} r={r} />
        <circle className="ring-fill" cx={size / 2} cy={size / 2} r={r}
                strokeDasharray={`${share * c} ${c}`}
                transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      </svg>
      <span className="ring-count">{done}/{total}</span>
    </span>
  );
}

const ICONS = {
  decision: <path d="M8 1.5 14.5 8 8 14.5 1.5 8Z" />,
  blocker: <path d="M5.2 1.5h5.6l3.7 3.7v5.6l-3.7 3.7H5.2l-3.7-3.7V5.2Z" />,
  wait: <><circle cx="8" cy="8" r="6.2" fill="none" strokeWidth="1.8" />
    <path d="M8 4.5V8l2.4 1.6" fill="none" strokeWidth="1.8" strokeLinecap="round" /></>,
};

export function OpenIcon({ kind, count }: { kind: keyof typeof ICONS; count?: number }) {
  return (
    <span className={`open-icon open-${kind}`} title={`${count ?? ""} open ${kind}${count === 1 ? "" : "s"}`}>
      <svg width="14" height="14" viewBox="0 0 16 16">{ICONS[kind]}</svg>
      {count !== undefined && <span className="open-count">{count}</span>}
    </span>
  );
}

export function OpenIcons({ w }: { w: Workflow }) {
  const counts = [["decision", w.open_decisions.length], ["blocker", w.open_blockers.length],
                  ["wait", w.open_waits.length]] as const;
  return (
    <span className="open-icons">
      {counts.filter(([, n]) => n > 0).map(([kind, n]) => <OpenIcon key={kind} kind={kind} count={n} />)}
    </span>
  );
}

// An hour is a full bar: past that, an open item is simply old.
const AGE_FULL_SEC = 3600;

export function AgeBar({ since, now = Date.now() / 1000 }: { since: number; now?: number }) {
  const share = Math.min(1, Math.max(0, now - since) / AGE_FULL_SEC);
  return <span className="age-bar"><span style={{ width: `${share * 100}%` }} /></span>;
}
