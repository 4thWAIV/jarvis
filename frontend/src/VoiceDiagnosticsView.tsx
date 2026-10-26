/**
 * The Diagnostics view in the centre area, opened from the Diagnostics tab
 * (VoiceSummary.tsx) or the health dot, the way a workflow row opens its
 * Workflow view. All of it is drawn from the voice record
 * (/api/voice/diagnostics*, voice_diagnostics.py), read again on every
 * "voice" hint:
 *
 *   - Overview: the health orb and the pipeline at full size, and today's
 *     totals;
 *   - Timeline: how far behind recognition was, its speed, how late the
 *     server ran, JARVIS's CPU and the machine's load, the page's stalls and
 *     its notifications, on one time axis, with each drop as a dot; clicking
 *     a dot zooms to the half minute before it;
 *   - Incidents: one card per failure, with its cause and the busiest
 *     processes at that moment;
 *   - Notifications: by source, the page just before each drop against the
 *     rest, and the quiet-page switch with drops per hour quiet and not;
 *   - Messages: every line of text the page showed, newest first, repeats
 *     grouped;
 *   - Save: the stretch on screen as JSON.
 */

import { useState, useSyncExternalStore } from "react";
import { useLiveRead } from "./liveRead";
import { useTick } from "./WorkflowsView";
import { Chip, PipelineTrack, Sparkline, VoiceOrb, type Point } from "./VoiceVisuals";
import { BACKLOG_DROP_SEC, BACKLOG_WARN_SEC, getHistory, health, HEALTH_WORDS, isQuiet, notices,
         openDiagnostics, quietUntil, setQuiet, summary, voiceChanged, type Sample, type VoiceEvent } from "./voiceHealth";
import { beforeDrops, clock, DROP_KINDS, groupMessages, INCIDENT_KINDS, INCIDENT_WORDS,
         noticesBySource, quietComparison } from "./voiceAnalysis";

const RANGES = [
  { id: "15m", words: "15 min", sec: 15 * 60 },
  { id: "1h", words: "1 hour", sec: 3600 },
  { id: "1d", words: "1 day", sec: 24 * 3600 },
];
const ZOOM_BEFORE_SEC = 30;
const ZOOM_AFTER_SEC = 5;

export function VoiceDiagnosticsView() {
  const [range, setRange] = useState(RANGES[0]);
  const [zoom, setZoom] = useState<number | null>(null);
  // Read again whenever the range, the zoom or the record changes; the
  // window ends now, so it is worked out at read time.
  const key = `${range.id}:${zoom ?? ""}`;
  return (
    <div id="voice-diagnostics">
      <Overview />
      <div className="voice-range" role="group" aria-label="How far back">
        {RANGES.map((r) => (
          <button key={r.id} type="button" className={`voice-range-button${!zoom && r.id === range.id ? " selected" : ""}`}
                  onClick={() => { setRange(r); setZoom(null); }}>{r.words}</button>
        ))}
        {zoom !== null && <span className="voice-zoomed">The half minute before the drop at {clock(zoom)}</span>}
      </div>
      <Stretch key={key} window={() => {
        const now = Date.now() / 1000;
        return zoom !== null ? [zoom - ZOOM_BEFORE_SEC, zoom + ZOOM_AFTER_SEC] : [now - range.sec, now];
      }} onZoom={setZoom} />
    </div>
  );
}

function Overview() {
  useTick(1000);
  const live = useSyncExternalStore(health.subscribe, health.get);
  const record = useSyncExternalStore(summary.subscribe, summary.get).value;
  const state = live?.state ?? record?.state ?? "idle";
  const cause = live ? live.cause : record?.cause ?? "";
  const pipeline = live?.pipeline ?? record?.pipeline;
  const t = record?.today;
  return (
    <section className="voice-overview" aria-label="Voice overview">
      <VoiceOrb state={state} size={72} />
      <div className="voice-overview-words">
        <h2>{HEALTH_WORDS[state]}</h2>
        <p className="voice-cause">{cause || " "}</p>
        {pipeline && <PipelineTrack pipeline={pipeline} />}
      </div>
      <button type="button" className="workflow-close" title="Close" aria-label="Close"
              onClick={() => openDiagnostics.set(false)}>×</button>
      {t && (
        <div className="voice-chips voice-today" aria-label="Today">
          <Chip tone="plain">listened {Math.round(t.listenedSec / 60)} min</Chip>
          <Chip tone="plain">{t.finals} sentences heard</Chip>
          <Chip tone="plain">{t.typed} typed</Chip>
          <Chip tone={t.drops ? "bad" : "ok"}>{t.drops} drops</Chip>
          <Chip tone={t.reconnects ? "warn" : "ok"}>{t.reconnects} reconnects</Chip>
        </div>
      )}
    </section>
  );
}

function Stretch({ window: span, onZoom }: { window: () => [number, number];
                                              onZoom: (t: number) => void }) {
  const [from, to] = span();
  const read = useLiveRead(() => { const [f, t] = span(); return getHistory(f, t); }, [voiceChanged]);
  const local = useSyncExternalStore(notices.subscribe, notices.get);
  const samples = read.value?.samples ?? [];
  const events = read.value?.events ?? [];
  return (
    <>
      {read.error && <p className="read-failure">Couldn't read the voice record ({read.error}); trying again.</p>}
      <Timeline samples={samples} events={events} from={from} to={to} onZoom={onZoom} />
      <Incidents events={events} />
      <Notifications samples={samples} events={events} />
      <Messages events={events} local={local.filter((n) => n.at / 1000 >= from)} />
      <button type="button" className="voice-save" disabled={!read.value}
              onClick={() => save(from, to, samples, events)}>Save as JSON</button>
    </>
  );
}

function Timeline({ samples, events, from, to, onZoom }: {
  samples: Sample[]; events: VoiceEvent[]; from: number; to: number; onZoom: (t: number) => void }) {
  const series = (f: (s: Sample) => number | null): Point[] => samples.map((s) => ({ t: s.at, v: f(s) }));
  const drops = events.filter((e) => DROP_KINDS.has(e.kind))
    .map((e) => ({ t: e.at, title: `${INCIDENT_WORDS[e.kind]} at ${clock(e.at)}${e.cause ? `: ${e.cause}` : ""}` }));
  const rows: { label: string; points: Point[]; max: number; lines?: { at: number; label: string; tone: "warn" | "bad" }[]; bars?: boolean }[] = [
    { label: "Behind (s)", points: series((s) => s.backlogSec), max: BACKLOG_DROP_SEC * 1.25,
      lines: [{ at: BACKLOG_DROP_SEC, label: "drop", tone: "bad" }, { at: BACKLOG_WARN_SEC, label: "1 s", tone: "warn" }] },
    { label: "Speed (time per second)", points: series((s) => s.rtf), max: 1.5,
      lines: [{ at: 1, label: "real time", tone: "bad" }] },
    { label: "Server late (ms)", points: series((s) => s.loopDelayMs), max: 500,
      lines: [{ at: 200, label: "200 ms", tone: "warn" }] },
    { label: "JARVIS CPU (%)", points: series((s) => s.processCpu), max: 400 },
    { label: "Machine load", points: series((s) => s.loadAvg), max: 24,
      lines: [{ at: 14, label: "busy", tone: "warn" }] },
    { label: "Page stalls (ms)", points: series((s) => s.longTaskMs), max: 500, bars: true },
    { label: "Notifications / s", points: series((s) => s.notices), max: 20, bars: true },
  ];
  return (
    <section className="voice-timeline" aria-label="Timeline">
      <h3>Timeline</h3>
      {!samples.length && <p className="voice-empty">Nothing recorded in this stretch: the microphone was not streaming.</p>}
      {rows.map((r) => (
        <div key={r.label} className="voice-timeline-row">
          <span className="voice-timeline-label">{r.label}</span>
          <Sparkline label={r.label} points={r.points} from={from} to={to} max={r.max}
                     lines={r.lines} marks={drops} onMark={onZoom} kind={r.bars ? "bars" : "line"} />
        </div>
      ))}
      <div className="voice-axis"><span>{clock(from)}</span><span>{clock(to)}</span></div>
    </section>
  );
}

function Incidents({ events }: { events: VoiceEvent[] }) {
  const incidents = events.filter((e) => INCIDENT_KINDS.has(e.kind)).reverse();
  return (
    <section className="voice-incidents" aria-label="Incidents">
      <h3>Incidents</h3>
      {!incidents.length && <p className="voice-empty">No drops or failures in this stretch.</p>}
      <div className="voice-cards">
        {incidents.slice(0, 50).map((e) => (
          <article key={e.id} className={`voice-card kind-${e.kind}`}>
            <span className="voice-card-when">{clock(e.at)} · {INCIDENT_WORDS[e.kind] ?? e.kind}</span>
            <p><b>{e.cause || "No cause recorded"}</b>{e.text ? ` ${e.text}` : ""}</p>
            {e.topProcesses.length > 0 && (
              <p className="voice-card-busy">Busiest: {e.topProcesses.map((p) => `${p.name} ${Math.round(p.cpu)}%`).join(", ")}</p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

function Notifications({ samples, events }: { samples: Sample[]; events: VoiceEvent[] }) {
  const quiet = useSyncExternalStore(quietUntil.subscribe, isQuiet);
  const sources = noticesBySource(samples);
  const most = Math.max(1, ...sources.map(([, n]) => n));
  const b = beforeDrops(samples, events);
  const q = quietComparison(samples, events);
  const perHour = (x: { hours: number; drops: number }) => x.hours > 0 ? (x.drops / x.hours).toFixed(1) : "—";
  return (
    <section className="voice-notifications" aria-label="Notifications">
      <h3>Notifications</h3>
      <div className="voice-bars">
        {sources.map(([source, n]) => (
          <div key={source} className="voice-bar-row">
            <span className="voice-bar-label">{source}</span>
            <span className="voice-bar"><span style={{ width: `${(n / most) * 100}%` }} /></span>
            <span className="voice-bar-count">{n}</span>
          </div>
        ))}
        {!sources.length && <p className="voice-empty">No notifications in this stretch.</p>}
      </div>
      {b.drops > 0 && (
        <table className="voice-compare">
          <thead><tr><th /><th>Notices / s</th><th>Page stalls ms / s</th><th>Renders / s</th></tr></thead>
          <tbody>
            <tr><th>30 s before a drop</th><td>{b.before.notices.toFixed(1)}</td>
              <td>{Math.round(b.before.longTaskMs)}</td><td>{b.before.renders.toFixed(1)}</td></tr>
            <tr><th>The rest of the time</th><td>{b.normal.notices.toFixed(1)}</td>
              <td>{Math.round(b.normal.longTaskMs)}</td><td>{b.normal.renders.toFixed(1)}</td></tr>
          </tbody>
        </table>
      )}
      <div className="voice-quiet">
        <button type="button" className={`voice-quiet-button${quiet ? " on" : ""}`} aria-pressed={quiet}
                onClick={() => setQuiet(!quiet)}>
          {quiet ? "End quiet page" : "Quiet page for 15 minutes"}
        </button>
        <span>Drops per hour: quiet {perHour(q.quiet)}, usual {perHour(q.usual)}</span>
      </div>
    </section>
  );
}

function Messages({ events, local }: { events: VoiceEvent[]; local: ReturnType<typeof notices.get> }) {
  const groups = groupMessages(events, local);
  return (
    <section className="voice-messages" aria-label="Messages">
      <h3>Messages</h3>
      {!groups.length && <p className="voice-empty">Nothing was shown in this stretch.</p>}
      <ol>
        {groups.slice(0, 200).map((g) => (
          <li key={`${g.source}:${g.text}:${g.first}`}>
            <time>{clock(g.last)}</time>
            <span className="voice-message-source">{g.source}</span>
            <span className="voice-message-text">{g.text}{g.count > 1 && <b> ×{g.count}</b>}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function save(from: number, to: number, samples: Sample[], events: VoiceEvent[]) {
  const body = JSON.stringify({ from, to, savedAt: Date.now() / 1000, samples, events }, null, 2);
  const url = URL.createObjectURL(new Blob([body], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `voice-diagnostics-${new Date(from * 1000).toISOString().slice(0, 16).replace(":", "")}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
