/**
 * One brain's weekly usage history, in the centre area (usage.ts): the same
 * burn-down the brain's box in the left column draws for this week
 * (WeeklyUsage.tsx), for this week and every week before it, one week at a
 * time. A week is drawn across its whole span, from its start to its reset:
 * what is LEFT of the week (100% minus what is used), all of it at the top
 * and none at the bottom; the dashed straight line from all left at the
 * start to none at the reset; the readings as one continuous line, green
 * while ahead of the straight line and red while behind it (as at its last
 * reading), with the gap between them filled the same way; the reset marked
 * at the week's end, and this week's last reading marked as the box marks
 * it. Above it, the reading in the box's terms: what is left and how far
 * ahead of or behind the straight line it is.
 *
 * Opened from that brain's own box (`usage.open {provider}`): one brain per
 * view, named in its title, never two together. This week shows first;
 * Earlier week, Later week and This week (`usage.week`) move through the
 * weeks in order. Closed by `usage.close`. The figures are
 * usage_history.py's, read through the same `/api/usage/history` the
 * brain's usage_history reads. Only the seven-day window is kept.
 *
 * The line joins the measured readings across any stretch without one; a
 * reading that did not measure the week is passed over, never drawn as 0
 * used or all left.
 *
 * Registered for pointing at: the view (`usageHistory`, keyed by its brain:
 * header, close, weeks, chart, latest).
 */

import { useState, useSyncExternalStore, type PointerEvent } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { formatDateTime, formatPercent, useT, type Translate } from "./locale";
import { PROVIDERS } from "./providers";
import { spokenAgo } from "./timeFormat";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { usageHistory, usageWeek, type UsageHistory, type UsagePoint, type UsageWeek } from "./usage";
import { daysLeft, magnitude, resetDay, sign } from "./WeeklyUsage";

type PartRef = (el: Element | null) => void;

function brainName(provider: string): string {
  return PROVIDERS[provider]?.name ?? provider;
}

/** The view's title, naming its brain: "Anthropic usage". */
export function usageTitle(t: Translate, provider: string): string {
  return t("main.usage.title", { brain: brainName(provider) });
}

function percent(value: number): string {
  return formatPercent(Math.round(value) / 100);
}

function when(at: number): string {
  return formatDateTime(at * 1000, { weekday: "short", day: "numeric", month: "short",
                                     hour: "2-digit", minute: "2-digit" });
}

/** What is left of the week at a reading: 100% minus what is used. */
export function left(used: number): number {
  return Math.max(0, 100 - used);
}

/** How far ahead of the straight line a reading is, in percentage points
 *  (expected use minus actual use, as usage_store.week works it out for the
 *  box): positive is more left than expected. */
export function ahead(at: number, used: number, week: UsageWeek, weekSec: number): number {
  const elapsed = Math.min(Math.max(at - week.start, 0), weekSec);
  return Math.round((100 * elapsed / weekSec - used) * 10) / 10;
}

export function UsageHistoryView({ provider }: { provider: string }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.usage", kind: "usageHistory", key: provider },
                           label: usageTitle(t, provider), socket: CENTRE_SOCKET });
  const loaded = useSyncExternalStore(usageHistory.subscribe, usageHistory.get);
  const index = useSyncExternalStore(usageWeek.subscribe, usageWeek.get);
  const history = loaded.value?.provider === provider ? loaded.value : null;
  const weeks = history?.weeks ?? [];
  const week = weeks[Math.min(index, Math.max(0, weeks.length - 1))] ?? null;
  const current = index === 0;

  return (
    <div id="usage-view" data-provider={provider} ref={ui.whole}>
      <section className="subsystem-head" ref={ui.part("header")}>
        <div className="subsystem-head-words">
          <h2>{usageTitle(t, provider)}</h2>
          <p className="subsystem-head-state">
            {loaded.error ? t("main.usage.unreadable", { error: loaded.error })
              : !history ? t("main.usage.reading")
              : !weeks.length ? t("main.usage.noWeeks")
              : t("main.usage.weekWords")}
          </p>
        </div>
        <button type="button" className="workflow-close" title={t("main.common.close")}
                aria-label={t("main.common.close")} ref={ui.part("close")}
                {...bindingProps(bind("usage.close", {}))}>×</button>
      </section>
      {history && week && (
        <>
          <div className="usage-weeks" role="group" aria-label={t("main.usage.weeks")} ref={ui.part("weeks")}>
            <button type="button" data-week="earlier" disabled={index + 1 >= weeks.length}
                    {...bindingProps(bind("usage.week", { to: "earlier" }))}>{t("main.usage.earlier")}</button>
            <span className="usage-week-name" data-week-index={index}>
              {current ? t("main.usage.thisWeek", { day: resetDay(week.resets_at) })
                : t("main.usage.pastWeek", { day: resetDay(week.resets_at) })}
              <small>{t("main.usage.weekOf", { n: index + 1, count: weeks.length })}</small>
            </span>
            <button type="button" data-week="later" disabled={index === 0}
                    {...bindingProps(bind("usage.week", { to: "later" }))}>{t("main.usage.later")}</button>
            {!current && (
              <button type="button" data-week="current"
                      {...bindingProps(bind("usage.week", { to: "current" }))}>{t("main.usage.backToThisWeek")}</button>
            )}
          </div>
          <Reading history={history} week={week} current={current} partRef={ui.part("latest")} />
          <Chart history={history} week={week} current={current} partRef={ui.part("chart")} />
          <p className="usage-legend">{t("main.usage.legend")}</p>
        </>
      )}
    </div>
  );
}

/** The reading above the chart, in the box's terms: this week's latest
 *  reading, or the last reading of an earlier week. */
function Reading({ history, week, current, partRef }: {
  history: UsageHistory; week: UsageWeek; current: boolean; partRef: PartRef;
}) {
  const t = useT();
  const measured = week.points.filter((p) => p.used !== null);
  const lastPoint = measured[measured.length - 1];
  const latest = history.latest;
  const reading = current && latest.used !== null && latest.observed_at !== null
    && latest.resets_at !== null && Math.abs(latest.resets_at - week.resets_at) <= 60
    ? { at: latest.observed_at, used: latest.used }
    : lastPoint ? { at: lastPoint.at, used: lastPoint.used! } : null;
  if (!reading) return <p className="usage-latest" data-measured="false" ref={partRef}>{t("main.usage.notMeasured")}</p>;
  const pace = ahead(reading.at, reading.used, week, history.week_sec);
  const state = pace >= 0 ? "ahead" : "behind";
  return (
    <p className="usage-latest" data-measured="true" ref={partRef}>
      <span className={`usage-left ${state}`}>
        {t(current ? "main.usage.left" : "main.usage.endedWith", { left: percent(left(reading.used)) })}
      </span>
      <span className={`usage-pace ${state}`} title={t("main.usage.paceTitle")}>
        {sign(pace)}{magnitude(pace)}%</span>
      {" "}{t("main.usage.used", { used: percent(reading.used) })}
      {" "}{current
        ? t("main.usage.measured", { ago: spokenAgo(Math.max(0, Date.now() - reading.at * 1000)),
                                     days: daysLeft(week.resets_at) })
        : t("main.usage.lastReading", { when: when(reading.at) })}
    </p>
  );
}

const W = 640, H = 200;
const PAD = { left: 40, right: 12, top: 10, bottom: 24 };

function Chart({ history, week, current, partRef }: {
  history: UsageHistory; week: UsageWeek; current: boolean; partRef: PartRef;
}) {
  const t = useT();
  const [hover, setHover] = useState<UsagePoint | null>(null);
  const measured = week.points.filter((p) => p.used !== null);
  const start = week.start, end = week.resets_at;
  const span = Math.max(1, end - start);
  const clamp = (at: number) => Math.min(Math.max(at, start), end);
  const x = (at: number) => PAD.left + ((clamp(at) - start) / span) * (W - PAD.left - PAD.right);
  // What is left, as the box draws it: all left (0% used) at the top.
  const y = (used: number) => PAD.top + (Math.min(Math.max(used, 0), 100) / 100) * (H - PAD.top - PAD.bottom);
  const xy = (at: number, used: number) => `${x(at).toFixed(1)},${y(used).toFixed(1)}`;
  const ideal = (at: number) => 100 * Math.min(Math.max((at - start) / span, 0), 1);
  const lastPoint = measured[measured.length - 1];
  const state = lastPoint && ahead(lastPoint.at, lastPoint.used!, week, history.week_sec) < 0 ? "behind" : "ahead";
  const line = measured.map((p) => xy(p.at, p.used!)).join(" ");
  const days = Array.from({ length: 8 }, (_, i) => start + i * 86_400).filter((d) => d <= end);

  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    if (!measured.length) return;
    const box = e.currentTarget.getBoundingClientRect();
    const at = start + ((e.clientX - box.left) / box.width * W - PAD.left) / (W - PAD.left - PAD.right) * span;
    let best = measured[0];
    for (const p of measured) if (Math.abs(p.at - at) < Math.abs(best.at - at)) best = p;
    setHover(best);
  };

  const words = measured.length === 0 ? t("main.usage.chartEmpty")
    : t("main.usage.chartWords", { count: measured.length, day: resetDay(week.resets_at) });
  return (
    <div className="usage-chart" ref={partRef}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={words} data-week-reset={week.resets_at}
           onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
        {[0, 50, 100].map((used) => (
          <g key={used} className="usage-grid">
            <line x1={PAD.left} x2={W - PAD.right} y1={y(used)} y2={y(used)} />
            <text x={PAD.left - 6} y={y(used) + 4} textAnchor="end">{formatPercent(left(used) / 100)}</text>
          </g>
        ))}
        {days.map((d, i) => (
          <g key={d} className="usage-day">
            <line x1={x(d)} x2={x(d)} y1={H - PAD.bottom} y2={H - PAD.bottom + 4} />
            {i < days.length - 1 && (
              <text className="usage-axis" x={x(d + 43_200)} y={H - 6} textAnchor="middle">
                {formatDateTime(d * 1000, { weekday: "short" })}
              </text>
            )}
          </g>
        ))}
        {measured.length > 1 && (
          <polygon className={`weekly-gap usage-gap ${state}`}
                   points={`${line} ${xy(lastPoint.at, ideal(lastPoint.at))} ${xy(measured[0].at, ideal(measured[0].at))}`} />
        )}
        <line className="weekly-ideal" x1={x(start)} y1={y(0)} x2={x(end)} y2={y(100)} />
        {measured.length === 1
          ? <circle className={`usage-dot ${state}`} cx={x(lastPoint.at)} cy={y(lastPoint.used!)} r={3} />
          : measured.length > 1 && <polyline className={`weekly-actual usage-line ${state}`} points={line} />}
        <line className="usage-reset" x1={x(end)} x2={x(end)} y1={PAD.top} y2={H - PAD.bottom}>
          <title>{t("main.usage.resetAt", { when: when(end) })}</title>
        </line>
        {current && lastPoint && (
          <line className="weekly-now" x1={x(lastPoint.at)} x2={x(lastPoint.at)} y1={PAD.top} y2={H - PAD.bottom} />
        )}
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
