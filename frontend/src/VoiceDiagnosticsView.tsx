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
 *
 * Registered for pointing at: the view (`voice`: its overview, range,
 * zoom, timeline, incidents, notifications, messages and Close) and each
 * incident (`voiceIncident`, by its record id). Close is
 * `voice.diagnostics.close`, as the brain runs it. The range and the zoom
 * are still this view's own state until their commands are declared
 * (`voice.range`, `voice.zoom`, in the diagnostics family). Save and the
 * quiet page are his own acts, not navigation.
 */

import { useState, useSyncExternalStore } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { useLiveRead } from "./liveRead";
import { formatNumber, useT, type Translate } from "./locale";
import { useTick } from "./WorkflowsView";
import { Chip, HeadroomBar, HeardRing, PipelineTrack, Sparkline, SpeedRing, type Point } from "./VoiceVisuals";
import { ago } from "./workflows";
import { audioStatus, BACKLOG_DROP_SEC, BACKLOG_WARN_SEC, getHistory, health, heardShare, isQuiet, notices,
         quietUntil, setQuiet, summary, voiceChanged, type Sample, type VoiceEvent } from "./voiceHealth";
import { beforeDrops, clock, DROP_KINDS, groupMessages, INCIDENT_KINDS, incidentWords,
         noticesBySource, quietComparison, sourceWords } from "./voiceAnalysis";

/** How far back the view looks; each is worded `main.voice.range.<id>`. */
const RANGES = [
  { id: "15m", sec: 15 * 60 },
  { id: "1h", sec: 3600 },
  { id: "1d", sec: 24 * 3600 },
] as const;

/** A figure to `digits` decimals, in the language's own digits. */
const decimals = (value: number, digits: number) =>
  formatNumber(value, undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
const ZOOM_BEFORE_SEC = 30;
const ZOOM_AFTER_SEC = 5;

export function VoiceDiagnosticsView() {
  const t = useT();
  const [range, setRange] = useState<(typeof RANGES)[number]>(RANGES[0]);
  const [zoom, setZoom] = useState<number | null>(null);
  // Read again whenever the range, the zoom or the record changes; the
  // window ends now, so it is worked out at read time.
  const key = `${range.id}:${zoom ?? ""}`;
  const ui = useUiTarget({ ref: { region: "main", view: "main.diagnostics", kind: "voice" },
                           label: t("main.voice.diagnostics"), socket: CENTRE_SOCKET });
  return (
    <div id="voice-diagnostics" ref={ui.whole}>
      <Overview partRef={ui.part} />
      <div className="voice-range" role="group" aria-label={t("main.voice.howFarBack")} ref={ui.part("range")}>
        {RANGES.map((r) => (
          <button key={r.id} type="button" className={`voice-range-button${!zoom && r.id === range.id ? " selected" : ""}`}
                  onClick={() => { setRange(r); setZoom(null); }}>{t(`main.voice.range.${r.id}`)}</button>
        ))}
        {zoom !== null && <span className="voice-zoomed" ref={ui.part("zoom")}>{t("main.voice.zoomed", { at: clock(zoom) })}</span>}
      </div>
      <Stretch key={key} window={() => {
        const now = Date.now() / 1000;
        return zoom !== null ? [zoom - ZOOM_BEFORE_SEC, zoom + ZOOM_AFTER_SEC] : [now - range.sec, now];
      }} onZoom={setZoom} partRef={ui.part} />
    </div>
  );
}

/** A part's callback ref, from the view's registered target. */
type PartRef = (part: string) => (el: Element | null) => void;

function Overview({ partRef }: { partRef: PartRef }) {
  useTick(1000);
  const t = useT();
  const live = useSyncExternalStore(health.subscribe, health.get);
  const record = useSyncExternalStore(summary.subscribe, summary.get).value;
  const status = audioStatus(live?.state ?? record?.state ?? "idle", record?.lastDropAt ?? null);
  const cause = live ? live.cause : record?.cause ?? "";
  const pipeline = live?.pipeline ?? record?.pipeline;
  const last = record?.last2min[record.last2min.length - 1];
  const today = record?.today;
  return (
    <section className="voice-overview" aria-label={t("main.voice.overview")} ref={partRef("overview")}>
      <span className="voice-ring-cap">
        <HeardRing share={record ? heardShare(record.lastHour) : null} tone={status.tone} size={72} />
        <small>{t("main.voice.heardLastHour")}</small>
      </span>
      <div className="voice-overview-words">
        <h2 className={`voice-status-word tone-${status.tone}`}>{status.word}</h2>
        <p className="voice-cause">
          {/* The cause is the voice record's own words, shown as recorded. */}
          {status.tone !== "ok" && cause ? <span data-original-detail="">{cause}</span> : " "}
          {record?.lastDropAt != null && <span className="voice-last-drop"> · {t("main.voice.lastDrop", { when: ago(record.lastDropAt) })}</span>}
        </p>
        {pipeline && <PipelineTrack pipeline={pipeline} />}
        <HeadroomBar backlogSec={live?.backlogSec ?? last?.backlogSec ?? null} />
      </div>
      <span className="voice-ring-cap">
        <SpeedRing rtf={live ? live.rtf : last?.rtf ?? null} size={72} />
        <small>{t("main.voice.speed")}</small>
      </span>
      <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")}
              ref={partRef("close")}
              {...bindingProps(bind("voice.diagnostics.close", {}))}>×</button>
      {today && (
        <div className="voice-chips voice-today" aria-label={t("main.voice.today")}>
          <Chip tone="plain">{t("main.voice.listened", { minutes: Math.round(today.listenedSec / 60) })}</Chip>
          <Chip tone="plain">{t("main.voice.sentencesHeard", { count: today.finals })}</Chip>
          <Chip tone="plain">{t("main.voice.typed", { count: today.typed })}</Chip>
          <Chip tone={today.drops ? "bad" : "ok"}>{t("main.voice.drops", { count: today.drops })}</Chip>
          <Chip tone={today.reconnects ? "warn" : "ok"}>{t("main.voice.reconnects", { count: today.reconnects })}</Chip>
        </div>
      )}
    </section>
  );
}

function Stretch({ window: span, onZoom, partRef }: { window: () => [number, number];
                                                       onZoom: (t: number) => void; partRef: PartRef }) {
  const t = useT();
  const [from, to] = span();
  const read = useLiveRead(() => { const [f, until] = span(); return getHistory(f, until); }, [voiceChanged]);
  const local = useSyncExternalStore(notices.subscribe, notices.get);
  const samples = read.value?.samples ?? [];
  const events = read.value?.events ?? [];
  return (
    <>
      {read.error && <p className="read-failure">{t("main.voice.unreadable", { error: read.error })}</p>}
      <Timeline samples={samples} events={events} from={from} to={to} onZoom={onZoom} partRef={partRef("timeline")} />
      <Incidents events={events} partRef={partRef("incidents")} />
      <Notifications samples={samples} events={events} partRef={partRef("notifications")} />
      <Messages events={events} local={local.filter((n) => n.at / 1000 >= from)} partRef={partRef("messages")} />
      <button type="button" className="voice-save" disabled={!read.value}
              onClick={() => save(from, to, samples, events)}>{t("main.voice.save")}</button>
    </>
  );
}

/** A section's callback ref, as its view's registered part. */
type SectionRef = (el: Element | null) => void;

function Timeline({ samples, events, from, to, onZoom, partRef }: {
  samples: Sample[]; events: VoiceEvent[]; from: number; to: number; onZoom: (t: number) => void;
  partRef: SectionRef }) {
  const t = useT();
  const series = (f: (s: Sample) => number | null): Point[] => samples.map((s) => ({ t: s.at, v: f(s) }));
  const drops = events.filter((e) => DROP_KINDS.has(e.kind))
    .map((e) => ({ t: e.at, title: e.cause
      ? t("main.voice.dropAtBecause", { what: incidentWords(e.kind), at: clock(e.at), cause: e.cause })
      : t("main.voice.dropAt", { what: incidentWords(e.kind), at: clock(e.at) }) }));
  const rows = timelineRows(t, series);
  return (
    <section className="voice-timeline" aria-label={t("main.voice.timeline")} ref={partRef}>
      <h3>{t("main.voice.timeline")}</h3>
      {!samples.length && <p className="voice-empty">{t("main.voice.nothingRecorded")}</p>}
      {rows.map((r) => (
        <div key={r.id} className="voice-timeline-row">
          <span className="voice-timeline-label">{r.label}</span>
          <Sparkline label={r.label} points={r.points} from={from} to={to} max={r.max}
                     lines={r.lines} marks={drops} onMark={onZoom} kind={r.bars ? "bars" : "line"} />
        </div>
      ))}
      <div className="voice-axis"><span>{clock(from)}</span><span>{clock(to)}</span></div>
    </section>
  );
}

type Row = { id: string; label: string; points: Point[]; max: number; bars?: boolean;
             lines?: { at: number; label: string; tone: "warn" | "bad" }[] };

/** The timeline's rows, each a measure against time with the lines it is
 *  judged by. Units are symbols, the same in either language. */
function timelineRows(t: Translate, series: (f: (s: Sample) => number | null) => Point[]): Row[] {
  return [
    { id: "behind", label: t("main.voice.row.behind"), points: series((s) => s.backlogSec), max: BACKLOG_DROP_SEC * 1.25,
      lines: [{ at: BACKLOG_DROP_SEC, label: t("main.voice.line.drop"), tone: "bad" },
              { at: BACKLOG_WARN_SEC, label: `${formatNumber(BACKLOG_WARN_SEC)} s`, tone: "warn" }] },
    { id: "speed", label: t("main.voice.row.speed"), points: series((s) => s.rtf), max: 1.5,
      lines: [{ at: 1, label: t("main.voice.line.realTime"), tone: "bad" }] },
    { id: "late", label: t("main.voice.row.late"), points: series((s) => s.loopDelayMs), max: 500,
      lines: [{ at: 200, label: `${formatNumber(200)} ms`, tone: "warn" }] },
    { id: "cpu", label: t("main.voice.row.cpu"), points: series((s) => s.processCpu), max: 400 },
    { id: "load", label: t("main.voice.row.load"), points: series((s) => s.loadAvg), max: 24,
      lines: [{ at: 14, label: t("main.voice.line.busy"), tone: "warn" }] },
    { id: "stalls", label: t("main.voice.row.stalls"), points: series((s) => s.longTaskMs), max: 500, bars: true },
    { id: "notices", label: t("main.voice.row.notices"), points: series((s) => s.notices), max: 20, bars: true },
  ];
}

function Incidents({ events, partRef }: { events: VoiceEvent[]; partRef: SectionRef }) {
  const t = useT();
  const incidents = events.filter((e) => INCIDENT_KINDS.has(e.kind)).reverse();
  return (
    <section className="voice-incidents" aria-label={t("main.voice.incidents")} ref={partRef}>
      <h3>{t("main.voice.incidents")}</h3>
      {!incidents.length && <p className="voice-empty">{t("main.voice.noIncidents")}</p>}
      <div className="voice-cards">
        {incidents.slice(0, 50).map((e) => <IncidentCard key={e.id} e={e} />)}
      </div>
    </section>
  );
}

function IncidentCard({ e }: { e: VoiceEvent }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.diagnostics", kind: "voiceIncident", key: String(e.id) },
                           label: incidentWords(e.kind), socket: CENTRE_SOCKET });
  return (
    <article className={`voice-card kind-${e.kind}`} data-event={e.id} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <span className="voice-card-when">{clock(e.at)} · {incidentWords(e.kind)}</span>
      {/* The cause and the line are the voice record's own words, shown as recorded. */}
      <p>{e.cause ? <b data-original-detail="">{e.cause}</b> : <b>{t("main.voice.noCause")}</b>}
        {e.text ? <span data-original-detail="">{` ${e.text}`}</span> : null}</p>
      {e.topProcesses.length > 0 && (
        <p className="voice-card-busy">{t("main.voice.busiest", {
          processes: e.topProcesses.map((p) => `${p.name} ${formatNumber(Math.round(p.cpu))}%`).join(", ") })}</p>
      )}
    </article>
  );
}

function Notifications({ samples, events, partRef }: { samples: Sample[]; events: VoiceEvent[]; partRef: SectionRef }) {
  const t = useT();
  const quiet = useSyncExternalStore(quietUntil.subscribe, isQuiet);
  const sources = noticesBySource(samples);
  const most = Math.max(1, ...sources.map(([, n]) => n));
  const b = beforeDrops(samples, events);
  const q = quietComparison(samples, events);
  const perHour = (x: { hours: number; drops: number }) => x.hours > 0 ? decimals(x.drops / x.hours, 1) : "—";
  return (
    <section className="voice-notifications" aria-label={t("main.voice.notifications")} ref={partRef}>
      <h3>{t("main.voice.notifications")}</h3>
      <div className="voice-bars">
        {sources.map(([source, n]) => (
          <div key={source} className="voice-bar-row">
            <span className="voice-bar-label">{sourceWords(source)}</span>
            <span className="voice-bar"><span style={{ width: `${(n / most) * 100}%` }} /></span>
            <span className="voice-bar-count">{n}</span>
          </div>
        ))}
        {!sources.length && <p className="voice-empty">{t("main.voice.noNotifications")}</p>}
      </div>
      {b.drops > 0 && (
        <table className="voice-compare">
          <thead><tr><th /><th>{t("main.voice.noticesPerSecond")}</th><th>{t("main.voice.stallsPerSecond")}</th>
            <th>{t("main.voice.rendersPerSecond")}</th></tr></thead>
          <tbody>
            <tr><th>{t("main.voice.beforeDrop")}</th><td>{decimals(b.before.notices, 1)}</td>
              <td>{formatNumber(Math.round(b.before.longTaskMs))}</td><td>{decimals(b.before.renders, 1)}</td></tr>
            <tr><th>{t("main.voice.restOfTime")}</th><td>{decimals(b.normal.notices, 1)}</td>
              <td>{formatNumber(Math.round(b.normal.longTaskMs))}</td><td>{decimals(b.normal.renders, 1)}</td></tr>
          </tbody>
        </table>
      )}
      <div className="voice-quiet">
        <button type="button" className={`voice-quiet-button${quiet ? " on" : ""}`} aria-pressed={quiet}
                onClick={() => setQuiet(!quiet)}>
          {quiet ? t("main.voice.endQuiet") : t("main.voice.startQuiet")}
        </button>
        <span>{t("main.voice.dropsPerHour", { quiet: perHour(q.quiet), usual: perHour(q.usual) })}</span>
      </div>
    </section>
  );
}

function Messages({ events, local, partRef }: {
  events: VoiceEvent[]; local: ReturnType<typeof notices.get>; partRef: SectionRef }) {
  const t = useT();
  const groups = groupMessages(events, local);
  return (
    <section className="voice-messages" aria-label={t("main.voice.messages")} ref={partRef}>
      <h3>{t("main.voice.messages")}</h3>
      {!groups.length && <p className="voice-empty">{t("main.voice.nothingShown")}</p>}
      <ol>
        {groups.slice(0, 200).map((g) => (
          <li key={`${g.source}:${g.text}:${g.first}`}>
            <time>{clock(g.last)}</time>
            <span className="voice-message-source">{sourceWords(g.source)}</span>
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
