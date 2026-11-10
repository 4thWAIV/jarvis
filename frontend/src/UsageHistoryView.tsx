/**
 * The brains' usage history, in the centre area (usage.ts): for each brain
 * and each of its windows (five-hour, seven-day), the percentage used over
 * time, with each reset marked as a dashed line, and the latest reading
 * above it. Opened from a brain's box in the left column (`usage.open`),
 * closed by `usage.close`; the range (last day, week, month or all of it)
 * is `usage.range`. The figures are usage_history.py's, read through the
 * same `/api/usage/history` the brain's usage_history reads.
 *
 * Absence is a state: a reading that did not measure a window, and a
 * stretch with no readings at all (further apart than the answer's
 * `gap_sec`), break the line. Nothing unmeasured is drawn as 0.
 *
 * One series per chart, so no legend: the title names it. Every chart has a
 * crosshair and a tooltip, and its words in `aria-label`.
 *
 * Registered for pointing at: the view (header, close, range, charts) and
 * each chart (`usageChart`, keyed `<provider>:<window>`: title, latest,
 * chart).
 */

import { useEffect, useState, useSyncExternalStore, type PointerEvent } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { formatDateTime, formatPercent, useT, type Translate } from "./locale";
import { PROVIDERS } from "./providers";
import { spokenAgo } from "./timeFormat";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import {
  RANGES, readUsageHistory, usageRange, type UsageHistory, type UsagePoint, type UsageSeries,
} from "./usage";

const REFRESH_MS = 60_000;

type PartRef = (el: Element | null) => void;

export function UsageHistoryView() {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.usage", kind: "usageHistory" },
                           label: t("tabs.main.usage"), socket: CENTRE_SOCKET });
  const range = useSyncExternalStore(usageRange.subscribe, usageRange.get);
  const [history, setHistory] = useState<UsageHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = () => readUsageHistory(range)
      .then((body) => { if (live) { setHistory(body); setError(null); } })
      .catch((e: Error) => { if (live) setError(e.message); });
    setHistory(null);
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => { live = false; clearInterval(timer); };
  }, [range]);

  const providers = [...new Set((history?.series ?? []).map((s) => s.provider))];
  return (
    <div id="usage-view" ref={ui.whole}>
      <section className="subsystem-head" ref={ui.part("header")}>
        <div className="subsystem-head-words">
          <h2>{t("tabs.main.usage")}</h2>
          <p className="subsystem-head-state">
            {error ? t("main.usage.unreadable", { error })
              : !history ? t("main.usage.reading")
              : t(history.resolution === "raw" ? "main.usage.everyReading" : "main.usage.byHour")}
          </p>
        </div>
        <button type="button" className="workflow-close" title={t("main.common.close")}
                aria-label={t("main.common.close")} ref={ui.part("close")}
                {...bindingProps(bind("usage.close", {}))}>×</button>
      </section>
      <div className="usage-ranges" role="group" aria-label={t("main.usage.range")} ref={ui.part("range")}>
        {RANGES.map((r) => (
          <button type="button" key={r} data-range={r} aria-pressed={range === r}
                  className={range === r ? "selected" : undefined}
                  {...bindingProps(bind("usage.range", { range: r }))}>
            {t(`main.usage.ranges.${r}`)}
          </button>
        ))}
      </div>
      <p className="usage-legend">{t("main.usage.legend")}</p>
      <div className="usage-brains" ref={ui.part("charts")}>
        {history && providers.map((name) => (
          <section key={name} className="usage-brain" data-provider={name}>
            <h3>{PROVIDERS[name]?.name ?? name}</h3>
            {history.series.filter((s) => s.provider === name).map((s) => (
              <SeriesCard key={`${s.provider}:${s.window}`} series={s} history={history} />
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

/** A window's name in the language in force, else the server's label. */
function windowName(t: Translate, s: UsageSeries): string {
  if (s.window === "five_hour") return t("main.usage.window.fiveHour");
  if (s.window === "seven_day") return t("main.usage.window.sevenDay");
  return s.label;
}

function percent(value: number): string {
  return formatPercent(Math.round(value) / 100);
}

function when(at: number): string {
  return formatDateTime(at * 1000, { weekday: "short", day: "numeric", month: "short",
                                     hour: "2-digit", minute: "2-digit" });
}

function SeriesCard({ series, history }: { series: UsageSeries; history: UsageHistory }) {
  const t = useT();
  const key = `${series.provider}:${series.window}`;
  const ui = useUiTarget({ ref: { region: "main", view: "main.usage", kind: "usageChart", key },
                           label: t("main.usage.chartLabel", { brain: PROVIDERS[series.provider]?.name ?? series.provider,
                                                               window: windowName(t, series) }),
                           socket: CENTRE_SOCKET });
  const last = series.latest;
  const latest = last.used === null || last.observed_at === null
    ? t("main.usage.notMeasured")
    : t("main.usage.latest", { used: percent(last.used),
                               ago: spokenAgo(Math.max(0, Date.now() - last.observed_at * 1000)) })
      + (last.resets_at ? " " + t("main.usage.resets", { when: when(last.resets_at) }) : "");
  return (
    <article className="usage-series" data-series={key} ref={ui.whole}>
      <header>
        <h4 ref={ui.part("title")}>{windowName(t, series)}</h4>
        <p className="usage-latest" data-measured={last.used !== null} ref={ui.part("latest")}>{latest}</p>
      </header>
      <Chart series={series} history={history} partRef={ui.part("chart")} />
    </article>
  );
}

const W = 640, H = 180;
const PAD = { left: 40, right: 12, top: 10, bottom: 24 };

/** Two reset times this close are the same window (usage_history.SAME_RESET_SEC). */
const SAME_RESET_SEC = 60;

function newWindow(prev: UsagePoint, p: UsagePoint): boolean {
  return prev.resets_at !== null && p.resets_at !== null && Math.abs(p.resets_at - prev.resets_at) > SAME_RESET_SEC;
}

/** The runs of the line: consecutive measured points of the same window no
 *  further apart than `gap` seconds. A reading not measured, a stretch with
 *  none, or a reset (a new window) ends a run. */
export function runs(points: UsagePoint[], gap: number): UsagePoint[][] {
  const out: UsagePoint[][] = [];
  let run: UsagePoint[] = [];
  let prev: UsagePoint | null = null;
  for (const p of points) {
    if (p.used === null || (prev && (p.at - prev.at > gap || newWindow(prev, p)))) {
      if (run.length) out.push(run);
      run = [];
    }
    if (p.used !== null) run.push(p);
    prev = p;
  }
  if (run.length) out.push(run);
  return out;
}

function Chart({ series, history, partRef }: { series: UsageSeries; history: UsageHistory; partRef: PartRef }) {
  const t = useT();
  const [hover, setHover] = useState<UsagePoint | null>(null);
  const points = series.points;
  // The range drawn: the one asked for, or (all of it) from the first reading.
  const start = history.since > 0 || !points.length ? history.since : points[0].at;
  const end = history.until;
  const span = Math.max(1, end - start);
  const x = (at: number) => PAD.left + ((Math.min(Math.max(at, start), end) - start) / span) * (W - PAD.left - PAD.right);
  const y = (used: number) => PAD.top + (1 - Math.min(Math.max(used, 0), 100) / 100) * (H - PAD.top - PAD.bottom);
  const lines = runs(points, history.gap_sec);
  const measured = points.filter((p) => p.used !== null).length;

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (!points.length) return;
    const box = e.currentTarget.getBoundingClientRect();
    const at = start + ((e.clientX - box.left) / box.width * W - PAD.left) / (W - PAD.left - PAD.right) * span;
    let best = points[0];
    for (const p of points) if (Math.abs(p.at - at) < Math.abs(best.at - at)) best = p;
    setHover(best);
  };

  const words = measured === 0
    ? t("main.usage.chartEmpty")
    : t("main.usage.chartWords", { count: measured, resets: series.resets.length });
  return (
    <div className="usage-chart" ref={partRef}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={words}
           onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {[0, 50, 100].map((v) => (
          <g key={v} className="usage-grid">
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} />
            <text x={PAD.left - 6} y={y(v) + 4} textAnchor="end">{formatPercent(v / 100)}</text>
          </g>
        ))}
        <text className="usage-axis" x={PAD.left} y={H - 6} textAnchor="start">{when(start)}</text>
        <text className="usage-axis" x={W - PAD.right} y={H - 6} textAnchor="end">{when(end)}</text>
        {series.resets.map((r) => (
          <line key={r} className="usage-reset" x1={x(r)} x2={x(r)} y1={PAD.top} y2={H - PAD.bottom}>
            <title>{t("main.usage.resetAt", { when: when(r) })}</title>
          </line>
        ))}
        {lines.map((run, i) => run.length === 1
          ? <circle key={i} className="usage-dot" cx={x(run[0].at)} cy={y(run[0].used!)} r={3} />
          : <polyline key={i} className="usage-line"
                      points={run.map((p) => `${x(p.at).toFixed(1)},${y(p.used!).toFixed(1)}`).join(" ")} />)}
        {hover && (
          <g className="usage-hover">
            <line x1={x(hover.at)} x2={x(hover.at)} y1={PAD.top} y2={H - PAD.bottom} />
            {hover.used !== null && <circle cx={x(hover.at)} cy={y(hover.used)} r={4} />}
          </g>
        )}
        {measured === 0 && (
          <text className="usage-empty" x={W / 2} y={H / 2} textAnchor="middle">{t("main.usage.chartEmpty")}</text>
        )}
      </svg>
      {hover && (
        <p className="usage-tooltip" role="status">
          {when(hover.at)}: {hover.used === null ? t("main.usage.notMeasuredThen")
            : history.resolution === "hour" ? t("main.usage.hourPeak", { used: percent(hover.used) })
            : percent(hover.used)}
        </p>
      )}
    </div>
  );
}
