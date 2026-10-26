/**
 * The pictures voice health is drawn with, shared by the Diagnostics tab
 * (VoiceSummary.tsx) and the Diagnostics view (VoiceDiagnosticsView.tsx):
 *
 *   - HeardRing: the share of his sentences heard in the last hour, ringed
 *     in the status colour (voiceHealth.audioStatus);
 *   - SpeedRing: how much of the recogniser's real-time budget it is using;
 *   - PipelineTrack: the five stages the audio passes through, each in its
 *     own colour, with its delay under any that is not well;
 *   - HeadroomBar: how far behind recognition is, up to where speech drops;
 *   - Sparkline: a value over time against the lines that matter, with each
 *     drop marked;
 *   - Chip: one count or reading with a coloured dot.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Ring } from "./WorkflowVisuals";
import { BACKLOG_DROP_SEC, BACKLOG_WARN_SEC, STAGES, STAGE_WORDS, type Stage, type StageState,
         type Tone } from "./voiceHealth";

export function HeardRing({ share, tone, size }: { share: number | null; tone: Tone; size: number }) {
  const pct = share === null ? null : Math.floor(share * 100);
  return <Ring done={pct ?? 0} total={100} size={size} className={`heard-ring tone-${tone}`}
               label="Sentences heard, last hour" text={pct === null ? "—" : `${pct}%`} />;
}

// Amber from here; red at all of it (it can no longer keep up).
const SPEED_WARN = 0.9;

export function SpeedRing({ rtf, size }: { rtf: number | null; size: number }) {
  const pct = rtf === null ? null : Math.round(rtf * 100);
  return <Ring done={Math.min(pct ?? 0, 100)} total={100} size={size}
               className={`speed-ring tone-${tone(rtf, SPEED_WARN, 1)}`}
               label="Recogniser time used per second of speech"
               text={pct === null ? "—" : `${pct}%`} />;
}

export function HeadroomBar({ backlogSec }: { backlogSec: number | null }) {
  const share = Math.min(1, Math.max(0, (backlogSec ?? 0) / BACKLOG_DROP_SEC));
  return (
    <span className={`headroom tone-${tone(backlogSec, BACKLOG_WARN_SEC, BACKLOG_DROP_SEC * 0.75)}`}
          title={`${seconds(backlogSec)} behind; speech is dropped at ${BACKLOG_DROP_SEC} s`}>
      <span className="headroom-label">Headroom</span>
      <span className="headroom-bar"><span style={{ width: `${share * 100}%` }} /></span>
      <span className="headroom-end">{BACKLOG_DROP_SEC} s drop</span>
    </span>
  );
}

/** "0.4 s", "—" when unknown. */
export const seconds = (v: number | null | undefined) => v == null ? "—" : `${v.toFixed(1)} s`;

function stageValue(stage: Stage, value: number | null): string {
  if (value == null) return "";
  if (stage === "mic") return value === 0 ? "silent" : "";
  if (stage === "jarvis") return `${Math.round(value)} ms`;
  return value >= 0.05 ? seconds(value) : "";
}

export function PipelineTrack({ pipeline, labels = true }: {
  pipeline: Record<Stage, { state: StageState; value: number | null }>; labels?: boolean }) {
  return (
    <div className="pipeline" role="list" aria-label="Where the audio is">
      {STAGES.map((stage) => {
        const { state, value } = pipeline[stage] ?? { state: "unknown", value: null };
        return (
          <div key={stage} role="listitem" className={`pipeline-stage st-${state}`}
               data-stage={stage} title={`${STAGE_WORDS[stage]}: ${state}`}>
            <span className="pipeline-bar" />
            {labels && <span className="pipeline-label">{STAGE_WORDS[stage]}</span>}
            {labels && <span className="pipeline-value">{state === "ok" ? "" : stageValue(stage, value)}</span>}
          </div>
        );
      })}
    </div>
  );
}

export interface Point { t: number; v: number | null }

/** `points` against time from `from` to `to` (any one unit), scaled to
 *  `max`, with dashed `lines` and a dot at each of `marks`. */
export function Sparkline({ points, from, to, max, lines = [], marks = [], height = 56,
                            label, kind = "line", onMark }: {
  points: Point[]; from: number; to: number; max: number;
  lines?: { at: number; label: string; tone: "warn" | "bad" }[];
  marks?: { t: number; title: string }[]; height?: number; label: string;
  kind?: "line" | "bars";
  /** Clicking a mark: the Diagnostics view zooms to it. */
  onMark?: (t: number) => void;
}) {
  // Drawn at its real width, so text and dots keep their shape.
  const ref = useRef<SVGSVGElement>(null);
  const [W, setW] = useState(260);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setW(Math.max(40, Math.round(el.getBoundingClientRect().width)));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  const span = Math.max(to - from, 1e-9);
  const x = (t: number) => ((t - from) / span) * W;
  const y = (v: number) => height - 2 - (Math.min(v, max) / max) * (height - 8);
  const known = points.filter((p) => p.v != null && p.t >= from && p.t <= to) as { t: number; v: number }[];
  const path = known.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`).join(" ");
  const last = known[known.length - 1];
  return (
    <svg ref={ref} className="sparkline" viewBox={`0 0 ${W} ${height}`} width="100%" height={height}
         role="img" aria-label={label}>
      {lines.map((l) => (
        <g key={l.label}>
          <line className={`spark-line tone-${l.tone}`} x1={0} x2={W} y1={y(l.at)} y2={y(l.at)} />
          <text className="spark-text" x={W - 2} y={y(l.at) - 2} textAnchor="end">{l.label}</text>
        </g>
      ))}
      {kind === "bars"
        ? known.map((p) => (
            <rect key={p.t} className="spark-bar" x={x(p.t)} width={Math.max(1, W / Math.max(known.length, 1) - 1)}
                  y={y(p.v)} height={height - 2 - y(p.v)} />))
        : path && <>
            <path className="spark-area" d={`${path} L${x(last.t).toFixed(1)} ${height} L${x(known[0].t).toFixed(1)} ${height}Z`} />
            <path className="spark-path" d={path} />
            <circle className="spark-end" cx={x(last.t)} cy={y(last.v)} r={2.5} />
          </>}
      {marks.filter((m) => m.t >= from && m.t <= to).map((m) => (
        <circle key={`${m.t}`} className={`spark-mark${onMark ? " clickable" : ""}`}
                cx={x(m.t)} cy={6} r={3.5} onClick={onMark ? () => onMark(m.t) : undefined}>
          <title>{m.title}</title>
        </circle>
      ))}
    </svg>
  );
}

export function Chip({ tone, children, title }: {
  tone: "ok" | "warn" | "bad" | "plain"; children: ReactNode; title?: string }) {
  return (
    <span className={`voice-chip tone-${tone}`} title={title}>
      {tone !== "plain" && <span className="voice-chip-dot" />}
      {children}
    </span>
  );
}

/** ok / warn / bad for a reading against its warning and bad lines. */
export function tone(value: number | null | undefined, warn: number, bad: number): "ok" | "warn" | "bad" {
  if (value == null) return "ok";
  return value >= bad ? "bad" : value >= warn ? "warn" : "ok";
}
