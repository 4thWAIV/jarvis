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

import { RIGHT_SOCKET } from "./uiRight";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { useSyncExternalStore } from "react";
import { t, useT } from "./locale";
import {
  cpuPercent, memoryPercent, openNetwork, paceText, paceWidth, paceWords, PRESENCE_WORDS,
  sinceMs, type NodeView,
} from "./network";
import { shortAge, spokenAgo } from "./timeFormat";

/** Within this, the badge is solid cyan; past it, dashed lavender. */
const RECENT_MS = 5 * 60_000;

/** The badge's figure size past three characters (the `.long` size is for
 *  three). */
const LONGER_FIGURE_PX = "13px";

function PersonIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="7" r="3" />
      <path d="M5 21v-3a7 7 0 0 1 14 0v3" />
    </svg>
  );
}

/** The human badge's look, figure and words, in the interface's language. */
export function humanReading(node: NodeView, now: number = Date.now()):
    { kind: "recent" | "away" | "unknown"; figure: string; words: string; ageMs: number | null } {
  if (node.activity_state === "unsupported") {
    return { kind: "unknown", figure: "—", words: t("right.node.humanUnavailable"), ageMs: null };
  }
  const ageMs = node.activity_state === "available" ? sinceMs(node.last_human_activity_at, now) : null;
  if (ageMs === null) {
    return { kind: "unknown", figure: "?", words: t("right.node.humanNever"), ageMs: null };
  }
  return { kind: ageMs < RECENT_MS ? "recent" : "away", figure: shortAge(ageMs),
           words: t("right.node.humanLast", { ago: spokenAgo(ageMs) }), ageMs };
}

/** The whole tile in words: one clause each, in the tile's order. */
function spoken(node: NodeView, self: boolean, current: boolean, human: string): string {
  const cpu = cpuPercent(node.resources);
  const mem = memoryPercent(node.resources);
  const pace = node.brains.pace_points;
  return [
    t(self ? "right.node.spoken.headSelf" : "right.node.spoken.head", { label: node.label, owner: node.owner_login }),
    current ? `${PRESENCE_WORDS[node.presence].toLowerCase()};` : "",
    `${human}.`,
    cpu === null ? t("right.node.spoken.cpuUnknown") : t("right.node.spoken.cpu", { percent: Math.round(cpu) }),
    mem === null ? t("right.node.spoken.memoryUnknown") : t("right.node.spoken.memory", { percent: Math.round(mem) }),
    node.brains.configured === 0 ? t("right.node.spoken.noBrains")
      : pace === null ? t("right.node.spoken.paceUnmeasured")
      : t(node.brains.measured === 1 ? "right.node.spoken.paceOne" : "right.node.spoken.pace",
          { pace: paceWords(pace) }),
    current ? "" : t("right.node.spoken.stale"),
    t("right.node.spoken.open"),
  ].filter(Boolean).join(" ");
}

type PartRef = (el: Element | null) => void;

function Rail({ label, percent, colour, partRef }: {
  label: string; percent: number | null; colour: string; partRef?: PartRef;
}) {
  return (
    <span ref={partRef} className={`visual-metric${percent === null ? " unknown" : ""}`}>
      <span className="metric-label">{label}</span>
      <span className="metric-track">
        {percent !== null && <i style={{ width: `${Math.min(100, percent)}%`, background: colour }} />}
      </span>
      <strong>{percent === null ? "—" : <>{Math.round(percent)}<sup>%</sup></>}</strong>
    </span>
  );
}

function PaceRail({ points, partRef }: { points: number | null; partRef?: PartRef }) {
  const t = useT();
  const ahead = points !== null && paceText(points).startsWith("+");
  return (
    <span ref={partRef} className={`visual-metric pace-metric${points === null ? " unknown" : ""}`}
          title={points === null ? t("right.node.paceUnmeasured")
            : t(ahead ? "right.node.paceAheadTitle" : "right.node.paceTitle", { pace: paceWords(points) })}>
      <span className="metric-label">{t("right.node.brain")}</span>
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
  const t = useT();
  const open = useSyncExternalStore(openNetwork.subscribe, openNetwork.get);
  const selected = open?.kind === "node" && open.nodeId === node.node_id;
  const human = humanReading(node);
  const marker = current ? node.presence : "unknown";
  // This presentation of the node, for the brain to point at: the tile,
  // entered from the right-hand lane, and each reading on it.
  const ui = useUiTarget({ ref: { region: "right", view: "right.network", kind: "node", key: node.node_id },
                           label: node.label, socket: RIGHT_SOCKET });
  return (
    <button type="button" data-node={node.node_id} ref={ui.whole}
            className={`compact-tile node-tile${self ? " self-tile" : ""}${
              current ? "" : " stale"}${selected ? " selected" : ""}`}
            aria-label={spoken(node, self, current, human.words)} aria-pressed={selected}
            {...bindingProps(bind("network.open", { kind: "node", nodeId: node.node_id }))}>
      <span className="tile-title" aria-hidden="true">
        <i ref={ui.part("presence")} className={`online-marker ${marker}`} />
        <span ref={ui.part("name")} className="tile-name">{node.label.toUpperCase()}</span>
        {self && <span className="tile-self">{"\u00a0"}{t("right.node.thisMac")}</span>}
      </span>
      <span className="tile-body">
        <span ref={ui.part("human")} className={`human-beacon ${human.kind}${human.figure.length > 2 ? " long" : ""}`} aria-hidden="true"
              title={human.ageMs === null ? human.words : `${human.words} (${node.last_human_activity_at})`}>
          <PersonIcon />
          {/* A figure longer than the badge was drawn for ("2 min", "15 sem"
              in Spanish) is set smaller, so it stays inside the badge. */}
          <strong style={human.figure.length > 3 ? { fontSize: LONGER_FIGURE_PX } : undefined}>{human.figure}</strong>
          <span>{t("right.node.human")}</span>
        </span>
        <span className="metric-stack" aria-hidden="true">
          <Rail label="CPU" percent={cpuPercent(node.resources)} colour="#64ded5" partRef={ui.part("cpu")} />
          <Rail label={t("right.node.memory")} percent={memoryPercent(node.resources)} colour="#b8a0f4" partRef={ui.part("memory")} />
          <PaceRail points={node.brains.pace_points} partRef={ui.part("pace")} />
        </span>
      </span>
    </button>
  );
}
