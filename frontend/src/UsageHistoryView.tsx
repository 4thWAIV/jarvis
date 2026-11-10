/**
 * One brain's usage history, in the centre area (usage.ts): for each of its
 * windows (five-hour, seven-day), the same burn-down the brain's box in the
 * left column draws for the week (WeeklyUsage.tsx), over the range chosen:
 * what is LEFT of the window (100% minus what is used), 100% at the top and
 * none at the bottom; each window's straight line from all left at its start
 * to none left at its reset, dashed; the readings as one continuous line,
 * green while ahead of the straight line and red while behind it, with the
 * gap between them filled the same way; and each reset marked. Above each
 * chart, the latest reading in the box's terms: what is left and how far
 * ahead of or behind the straight line it is.
 *
 * Opened from that brain's own box (`usage.open {provider}`): one brain per
 * view, named in its title, never two together. Closed by `usage.close`; the
 * range (last day, week or month, or all of it) is `usage.range`. The
 * figures are usage_history.py's, read through the same
 * `/api/usage/history` the brain's usage_history reads.
 *
 * The line joins the measured readings across any stretch without one, so
 * it reads as one line; a reading that did not measure the window is passed
 * over, never drawn as 0 used or all left.
 *
 * Registered for pointing at: the view (`usageHistory`, keyed by its brain:
 * header, close, range, charts) and each chart (`usageChart`, keyed
 * `<provider>:<window>`: title, latest, chart).
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
import { magnitude, sign } from "./WeeklyUsage";

const REFRESH_MS = 60_000;
/** Two reset times this close are the same window (usage_history.SAME_RESET_SEC). */
const SAME_RESET_SEC = 60;

type PartRef = (el: Element | null) => void;

function brainName(provider: string): string {
  return PROVIDERS[provider]?.name ?? provider;
}

/** The view's title, naming its brain: "Anthropic usage". */
export function usageTitle(t: Translate, provider: string): string {
  return t("main.usage.title", { brain: brainName(provider) });
}

export function UsageHistoryView({ provider }: { provider: string }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.usage", kind: "usageHistory", key: provider },
                           label: usageTitle(t, provider), socket: CENTRE_SOCKET });
  const range = useSyncExternalStore(usageRange.subscribe, usageRange.get);
  const [history, setHistory] = useState<UsageHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = () => readUsageHistory(provider, range)
      .then((body) => { if (live) { setHistory(body); setError(null); } })
      .catch((e: Error) => { if (live) setError(e.message); });
    setHistory(null);
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => { live = false; clearInterval(timer); };
  }, [provider, range]);

  return (
    <div id="usage-view" data-provider={provider} ref={ui.whole}>
      <section className="subsystem-head" ref={ui.part("header")}>
        <div className="subsystem-head-words">
          <h2>{usageTitle(t, provider)}</h2>
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
        {history?.series.filter((s) => s.provider === provider).map((s) => (
          <SeriesCard key={`${s.provider}:${s.window}`} series={s} history={history} />
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

/** What is left of the window at a reading: 100% minus what is used. */
export function left(used: number): number {
  return Math.max(0, 100 - used);
}

/** How far ahead of the straight line a reading is, in percentage points
 *  (expected use minus actual use, as usage_store.week works it out for the
 *  box): positive is more left than expected. Null without a reset time or
 *  a known window length. */
export function ahead(p: UsagePoint, windowSec: number | null): number | null {
  if (p.used === null || p.resets_at === null || !windowSec) return null;
  const elapsed = Math.min(Math.max(p.at - (p.resets_at - windowSec), 0), windowSec);
  return Math.round((100 * elapsed / windowSec - p.used) * 10) / 10;
}

/** The measured readings grouped by the window they belong to (its reset
 *  time); a reading that names no reset stays with the window before it. */
export function windows(points: UsagePoint[]): UsagePoint[][] {
  const out: UsagePoint[][] = [];
  let current: UsagePoint[] = [];
  let reset: number | null = null;
  for (const p of points) {
    if (p.used === null) continue;
    if (p.resets_at !== null && reset !== null && Math.abs(p.resets_at - reset) > SAME_RESET_SEC && current.length) {
      out.push(current);
      current = [];
    }
    if (p.resets_at !== null) reset = p.resets_at;
    current.push(p);
  }
  if (current.length) out.push(current);
  return out;
}

function SeriesCard({ series, history }: { series: UsageSeries; history: UsageHistory }) {
  const t = useT();
  const key = `${series.provider}:${series.window}`;
  const ui = useUiTarget({ ref: { region: "main", view: "main.usage", kind: "usageChart", key },
                           label: t("main.usage.chartLabel", { brain: brainName(series.provider),
                                                               window: windowName(t, series) }),
                           socket: CENTRE_SOCKET });
  const last = series.latest;
  const pace = last.used === null || last.observed_at === null ? null
    : ahead({ at: last.observed_at, used: last.used, low: null, high: null, resets_at: last.resets_at,
              status: "", account: null, samples: 1 }, series.window_sec);
  const state = pace === null ? "none" : pace >= 0 ? "ahead" : "behind";
  return (
    <article className="usage-series" data-series={key} ref={ui.whole}>
      <header>
        <h4 ref={ui.part("title")}>{windowName(t, series)}</h4>
        <p className="usage-latest" data-measured={last.used !== null} ref={ui.part("latest")}>
          {last.used === null || last.observed_at === null ? t("main.usage.notMeasured") : <>
            <span className={`usage-left ${state}`}>{t("main.usage.left", { left: percent(left(last.used)) })}</span>
            {pace !== null && <span className={`usage-pace ${state}`} title={t("main.usage.paceTitle")}>
              {sign(pace)}{magnitude(pace)}%</span>}
            {" "}{t("main.usage.latest", { used: percent(last.used),
                                          ago: spokenAgo(Math.max(0, Date.now() - last.observed_at * 1000)) })}
            {last.resets_at ? " " + t("main.usage.resets", { when: when(last.resets_at) }) : ""}
          </>}
        </p>
      </header>
      <Chart series={series} history={history} partRef={ui.part("chart")} />
    </article>
  );
}

const W = 640, H = 180;
const PAD = { left: 40, right: 12, top: 10, bottom: 24 };

function Chart({ series, history, partRef }: { series: UsageSeries; history: UsageHistory; partRef: PartRef }) {
  const t = useT();
  const [hover, setHover] = useState<UsagePoint | null>(null);
  const measured = series.points.filter((p) => p.used !== null);
  // The range drawn: the one asked for, or (all of it) from the first reading.
  const start = history.since > 0 || !measured.length ? history.since : measured[0].at;
  const end = history.until;
  const span = Math.max(1, end - start);
  const clamp = (at: number) => Math.min(Math.max(at, start), end);
  const x = (at: number) => PAD.left + ((clamp(at) - start) / span) * (W - PAD.left - PAD.right);
  // What is left, as the box draws it: all left (0% used) at the top.
  const y = (used: number) => PAD.top + (Math.min(Math.max(used, 0), 100) / 100) * (H - PAD.top - PAD.bottom);
  const xy = (at: number, used: number) => `${x(at).toFixed(1)},${y(used).toFixed(1)}`;
  const winSec = series.window_sec;
  // The straight line's use at `at`, in the window that resets at `reset`.
  const ideal = (at: number, reset: number) => 100 * Math.min(Math.max((at - (reset - winSec!)) / winSec!, 0), 1);
  const groups = windows(series.points);

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (!measured.length) return;
    const box = e.currentTarget.getBoundingClientRect();
    const at = start + ((e.clientX - box.left) / box.width * W - PAD.left) / (W - PAD.left - PAD.right) * span;
    let best = measured[0];
    for (const p of measured) if (Math.abs(p.at - at) < Math.abs(best.at - at)) best = p;
    setHover(best);
  };

  const words = measured.length === 0
    ? t("main.usage.chartEmpty")
    : t("main.usage.chartWords", { count: measured.length, resets: series.resets.length });
  return (
    <div className="usage-chart" ref={partRef}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={words}
           onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {[0, 50, 100].map((used) => (
          <g key={used} className="usage-grid">
            <line x1={PAD.left} x2={W - PAD.right} y1={y(used)} y2={y(used)} />
            <text x={PAD.left - 6} y={y(used) + 4} textAnchor="end">{formatPercent(left(used) / 100)}</text>
          </g>
        ))}
        <text className="usage-axis" x={PAD.left} y={H - 6} textAnchor="start">{when(start)}</text>
        <text className="usage-axis" x={W - PAD.right} y={H - 6} textAnchor="end">{when(end)}</text>
        {groups.map((group, i) => {
          const reset = group[group.length - 1].resets_at;
          const state = (ahead(group[group.length - 1], winSec) ?? 0) >= 0 ? "ahead" : "behind";
          // One line: each window's readings, joined to the last reading of
          // the window before it.
          const prev = i > 0 ? groups[i - 1][groups[i - 1].length - 1] : null;
          const line = [...(prev ? [prev] : []), ...group].map((p) => xy(p.at, p.used!)).join(" ");
          const first = group[0], last = group[group.length - 1];
          const straight = reset !== null && winSec;
          return (
            <g key={i} data-window-reset={reset ?? undefined}>
              {straight && group.length > 1 && (
                <polygon className={`weekly-gap usage-gap ${state}`}
                         points={`${group.map((p) => xy(p.at, p.used!)).join(" ")} ${xy(last.at, ideal(last.at, reset))} ${
                           xy(first.at, ideal(first.at, reset))}`} />
              )}
              {straight && (
                <line className="weekly-ideal" x1={x(reset - winSec)} y1={y(ideal(clamp(reset - winSec), reset))}
                      x2={x(reset)} y2={y(ideal(clamp(reset), reset))} />
              )}
              {group.length === 1 && !prev
                ? <circle className={`usage-dot ${state}`} cx={x(first.at)} cy={y(first.used!)} r={3} />
                : <polyline className={`weekly-actual usage-line ${state}`} points={line} />}
            </g>
          );
        })}
        {series.resets.map((r) => (
          <line key={r} className="usage-reset" x1={x(r)} x2={x(r)} y1={PAD.top} y2={H - PAD.bottom}>
            <title>{t("main.usage.resetAt", { when: when(r) })}</title>
          </line>
        ))}
        {hover && (
          <g className="usage-hover">
            <line x1={x(hover.at)} x2={x(hover.at)} y1={PAD.top} y2={H - PAD.bottom} />
            <circle cx={x(hover.at)} cy={y(hover.used!)} r={4} />
          </g>
        )}
        {measured.length === 0 && (
          <text className="usage-empty" x={W / 2} y={H / 2} textAnchor="middle">{t("main.usage.chartEmpty")}</text>
        )}
      </svg>
      {hover && (
        <p className="usage-tooltip" role="status">
          {when(hover.at)}: {t(history.resolution === "hour" ? "main.usage.hourLeast" : "main.usage.left",
                               { left: percent(left(hover.used!)) })}
        </p>
      )}
    </div>
  );
}
