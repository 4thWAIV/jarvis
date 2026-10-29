/**
 * One JARVIS instance in the Network panel, as the team server reported it,
 * the brain boxes' footprint. On the border: its name, "THIS MAC" when the
 * team server says it is the caller's own, and its online marker (filled
 * online, hollow otherwise). Inside: a person badge with the large age of
 * the last human interaction it recorded (solid cyan within five minutes,
 * dashed lavender older, a bright question mark when none was ever
 * observed), and three labelled rails: CPU used, memory used, and the brain
 * pace (signed, growing right of the centre line for plus and left for
 * minus; ten points fills one half). Unknown readings are a patterned rail
 * and a dash. Stale values keep their figures and lose their colour.
 *
 * Recorded recency, not verified presence. A click opens the node's detail in
 * the centre (NodeView.tsx).
 */

import { useSyncExternalStore } from "react";
import {
  cpuPercent, memoryPercent, openNetwork, paceText, paceWidth, paceWords, PRESENCE_WORDS,
  shortAge, sinceMs, spokenAgo, type NodeView,
} from "./network";

/** Within this, the badge is solid cyan; past it, dashed lavender. */
const RECENT_MS = 5 * 60_000;

function PersonIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="7" r="3" />
      <path d="M5 21v-3a7 7 0 0 1 14 0v3" />
    </svg>
  );
}

/** The human badge's look, figure and words. */
export function humanReading(node: NodeView, now: number = Date.now()):
    { kind: "recent" | "away" | "unknown"; figure: string; words: string; ageMs: number | null } {
  if (node.activity_state === "unsupported") {
    return { kind: "unknown", figure: "—", words: "human activity unavailable", ageMs: null };
  }
  const ageMs = node.activity_state === "available" ? sinceMs(node.last_human_activity_at, now) : null;
  if (ageMs === null) {
    return { kind: "unknown", figure: "?", words: "no human activity observed", ageMs: null };
  }
  return { kind: ageMs < RECENT_MS ? "recent" : "away", figure: shortAge(ageMs),
           words: `last human activity ${spokenAgo(ageMs)}`, ageMs };
}

/** The whole tile in words. */
function spoken(node: NodeView, self: boolean, current: boolean, human: string): string {
  const whose = `${self ? "this Mac, " : ""}owned by ${node.owner_login}`;
  const cpu = cpuPercent(node.resources);
  const mem = memoryPercent(node.resources);
  const pace = node.brains.pace_points;
  const brains = node.brains.measured === 1 ? "brain" : "brains";
  return [
    `${node.label}, ${whose}, as reported by Team Server:`,
    current ? `${PRESENCE_WORDS[node.presence].toLowerCase()};` : "",
    `${human}.`,
    cpu === null ? "CPU unknown," : `CPU ${Math.round(cpu)} percent,`,
    mem === null ? "memory unknown," : `memory ${Math.round(mem)} percent,`,
    node.brains.configured === 0 ? "no brain accounts configured."
      : pace === null ? "brain pace not measured." : `${brains} ${paceWords(pace)}.`,
    current ? "" : "Values are stale.",
    "Open details in the centre.",
  ].filter(Boolean).join(" ");
}

function Rail({ label, percent, colour }: { label: string; percent: number | null; colour: string }) {
  return (
    <span className={`visual-metric${percent === null ? " unknown" : ""}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-track">
        {percent !== null && <i style={{ width: `${Math.min(100, percent)}%`, background: colour }} />}
      </span>
      <strong>{percent === null ? "—" : <>{Math.round(percent)}<sup>%</sup></>}</strong>
    </span>
  );
}

function PaceRail({ points }: { points: number | null }) {
  const ahead = points !== null && paceText(points).startsWith("+");
  return (
    <span className={`visual-metric pace-metric${points === null ? " unknown" : ""}`}
          title={points === null ? "Brain pace not measured"
            : ahead ? `Brains ${paceWords(points)}: more left than expected at this point in the week`
            : `Brains ${paceWords(points)}`}>
      <span className="metric-label">BRAIN</span>
      <span className="pace-track">
        {points !== null && <i className={ahead ? "ahead" : "behind"} style={{ width: `${paceWidth(points)}%` }} />}
      </span>
      <strong className={points === null ? "" : ahead ? "pace-ahead" : "pace-behind"}>
        {points === null ? "—" : <>{paceText(points)}<sup>%</sup></>}
      </strong>
    </span>
  );
}

export function NetworkTile({ node, self, current }: { node: NodeView; self: boolean; current: boolean }) {
  const open = useSyncExternalStore(openNetwork.subscribe, openNetwork.get);
  const selected = open?.kind === "node" && open.nodeId === node.node_id;
  const human = humanReading(node);
  const marker = current ? node.presence : "unknown";
  return (
    <button type="button" data-node={node.node_id}
            className={`compact-tile node-tile${self ? " self-tile" : ""}${
              current ? "" : " stale"}${selected ? " selected" : ""}`}
            aria-label={spoken(node, self, current, human.words)} aria-pressed={selected}
            onClick={() => openNetwork.set({ kind: "node", nodeId: node.node_id })}>
      <span className="tile-title" aria-hidden="true">
        <i className={`online-marker ${marker}`} />
        <span className="tile-name">{node.label.toUpperCase()}</span>
        {self && <span className="tile-self">&nbsp;· THIS MAC</span>}
      </span>
      <span className="tile-body">
        <span className={`human-beacon ${human.kind}${human.figure.length > 2 ? " long" : ""}`} aria-hidden="true"
              title={human.ageMs === null ? human.words : `${human.words} (${node.last_human_activity_at})`}>
          <PersonIcon />
          <strong>{human.figure}</strong>
          <span>HUMAN</span>
        </span>
        <span className="metric-stack" aria-hidden="true">
          <Rail label="CPU" percent={cpuPercent(node.resources)} colour="#64ded5" />
          <Rail label="MEM" percent={memoryPercent(node.resources)} colour="#b8a0f4" />
          <PaceRail points={node.brains.pace_points} />
        </span>
      </span>
    </button>
  );
}
