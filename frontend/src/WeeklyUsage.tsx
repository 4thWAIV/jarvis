/**
 * The account, in the Diagnostics panel, as two rows of the same height.
 * The first: the avatar, and how much of the week's limit is still available
 * (100% minus what is used). The second: the provider's logo, and how far the
 * week is from a straight line across it (expected use minus actual use, in
 * percentage points: positive, green, means more left than expected). Both
 * figures take the number's colour. To their right, as tall as the two
 * rows, a burn-down of the week: the days left until it resets above it and
 * the day it resets below it ("Fri 23 Oct"), both centred, monospaced and in
 * the figures' colour. The server
 * works it out (usage_store.week); this only draws it. Details are on hover.
 *
 * Each box is a target the brain can point at (keyed by its provider, from
 * the left rail), with the provider, what remains, the pace and the reset as
 * parts. A box whose provider is not known yet is not one. A click (or Enter)
 * opens that brain's own usage history in the centre (`usage.open
 * {provider}`, UsageHistoryView.tsx), the same burn-down over time.
 */

import { useEffect, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import { WorkerTotal } from "./WorkerStatus";
import { activeBrain, brains } from "./brains";
import { formatDateTime, formatNumber, formatPercent, t as say, useT, type Translate } from "./locale";
import { PROVIDERS, type ReportProvider } from "./providers";
import { shortAgo } from "./timeFormat";
import type { TargetRef } from "./uiContract";
import { useUiTarget } from "./uiRegistry";
import { targetProblem } from "./uiValidate";
import { bind, bindingProps, runBinding } from "./uiNavigation";

/** What makes a box open its own brain's usage history: a click, or Enter
 *  or Space. Nothing while the box's brain is not known yet. */
function opensHistory(provider: string | undefined, label: string) {
  if (provider === undefined) return {};
  const b = bind("usage.open", { provider });
  return {
    ...bindingProps(b),
    role: "button",
    tabIndex: 0,
    "aria-description": label,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); runBinding(b); }
    },
  };
}

type PartRef = (el: Element | null) => void;

/** This box as a target: keyed by the provider whose figures it shows (the
 *  one named, else the one the figures say, else the running brain), or
 *  none while that is not known. */
function useBrainKey(provider: string | undefined, week: Week | null): string | undefined {
  const live = useSyncExternalStore(brains.subscribe, brains.get);
  return provider ?? week?.provider?.service ?? activeBrain(live.value) ?? undefined;
}

function useAccountTarget(key: string | undefined) {
  const t = useT();
  const ref: TargetRef = { region: "left", view: "left.fixed", kind: "brainAccount", key };
  const valid = key !== undefined && targetProblem(ref) === null;
  return useUiTarget(valid ? { ref, label: t("targets.account", { provider: PROVIDERS[key]?.name ?? key }),
                               socket: { rail: "rail.left", side: "left" } } : null);
}

interface Week {
  measured: boolean;
  provider?: ReportProvider;
  week_start?: number;
  resets_at?: number;
  observed_at?: number;
  age_sec?: number;
  expected_used?: number;
  used?: number;
  ahead?: number;
  points?: { at: number; used: number }[];
}

const REFRESH_MS = 60_000;

/** Why the last read failed: the route is missing (an old server), the
 *  server answered with an error, or it did not answer. Kept as what
 *  happened, and worded where it is shown. */
type Failure = { kind: "no-route" } | { kind: "status"; status: number } | { kind: "no-answer" };

class ReadFailed extends Error {
  constructor(readonly failure: Failure) { super(failure.kind); }
}

function failureWords(t: Translate, failure: Failure): string {
  if (failure.kind === "no-route") return t("left.usage.noRoute");
  if (failure.kind === "status") return t("left.common.serverAnswered", { status: String(failure.status) });
  return t("left.usage.noAnswer");
}

/** The over/under figure without its sign, always four places: two digits,
 *  the point, one digit ("05.2", "15.0", "00.0"); past 99.9 it is held at
 *  99.9. Its sign is shown apart, as a superscript (`sign`). */
export function magnitude(value: number): string {
  return Math.min(Math.abs(value), 99.9).toFixed(1).padStart(4, "0");
}

/** "+" ahead of the line or level with it, "-" behind; a figure that shows
 *  as 00.0 is level. */
export function sign(value: number): "+" | "-" {
  return value < 0 && magnitude(value) !== "00.0" ? "-" : "+";
}

/** One brain's account box. `provider` names whose figures (the running
 *  brain's when left out); `role` marks it, when there is more than one
 *  brain, as the brain in use or the one standing by. */
export function WeeklyUsage({ provider, role }: { provider?: string;
                                                  role?: "brain" | "standby" } = {}) {
  const t = useT();
  const [week, setWeek] = useState<Week | null>(null);
  // Why the last request failed, if it did. A failed request is said as one:
  // it used to fall through to "not measured yet", which was untrue for as
  // long as the server lacked the route.
  const [failed, setFailed] = useState<Failure | null>(null);

  useEffect(() => {
    const load = () =>
      fetch(provider ? `/api/usage/week?provider=${encodeURIComponent(provider)}` : "/api/usage/week")
        .then((r) => {
          if (!r.ok) throw new ReadFailed(r.status === 404 ? { kind: "no-route" } : { kind: "status", status: r.status });
          return r.json();
        })
        .then((body: Week) => { setWeek(body); setFailed(null); })
        .catch((e: Error) => setFailed(e instanceof ReadFailed ? e.failure : { kind: "no-answer" }));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [provider]);
  const brain = useBrainKey(provider, week);
  const ui = useAccountTarget(brain);

  const roleTag = role &&
    <span className={`weekly-role ${role}`}>{t(role === "brain" ? "left.usage.roleBrain" : "left.usage.roleStandby")}</span>;
  const box = `weekly-usage${role ? ` role-${role}` : ""}`;

  // A dash stands in for each figure when there is none to show (red if the
  // request failed), with the reason on hover.
  if (failed || !week || !week.measured) {
    const why = failed ? t("left.usage.failed", { error: failureWords(t, failed) }) : t("left.usage.notMeasured");
    const cls = failed ? "behind" : "none";
    return (
      <section className={box} title={why} data-provider={provider} ref={ui.whole}
               {...opensHistory(brain, t("left.usage.openHistory"))}>
        {roleTag}
        <div className="account-rows">
          <div className="account-row available" ref={ui.part("remaining")}>
            <WorkerTotal provider={provider ?? week?.provider?.service} compact />
            <span className={`weekly-left ${cls}`}>—</span>
          </div>
          <div className="account-row pace" ref={ui.part("pace")}>
            <Provider provider={week?.provider ?? (provider ? { service: provider } : undefined)}
                      partRef={ui.part("name")} />
            <div className={`weekly-ahead ${cls}`}>—</div>
          </div>
        </div>
      </section>
    );
  }

  const ahead = week.ahead!;
  const good = ahead >= 0;
  const left = Math.max(0, 100 - week.used!);
  return (
    <section className={box} data-provider={provider} ref={ui.whole}
             {...opensHistory(brain, t("left.usage.openHistory"))}
             title={t("left.usage.reading", { gone: formatPercent(Math.round(week.expected_used!) / 100),
                                             used: formatPercent(Math.round(week.used!) / 100),
                                             age: shortAgo(week.age_sec! * 1000, 90_000) })}>
      {roleTag}
      <div className="account-rows">
        <div className="account-row available" ref={ui.part("remaining")}>
          <WorkerTotal provider={provider ?? week?.provider?.service} compact />
          <span className={`weekly-left ${good ? "ahead" : "behind"}`}>
            <span className="weekly-group">
              <span className="weekly-figure">{Math.round(left)}</span>
              <Percent />
            </span>
          </span>
        </div>
        <div className="account-row pace" ref={ui.part("pace")}>
          <Provider provider={week.provider} partRef={ui.part("name")} />
          <div className={`weekly-ahead ${good ? "ahead" : "behind"}`}>
            <span className="weekly-group">
              <span className={`weekly-sign ${sign(ahead) === "+" ? "plus" : "minus"}`}>{sign(ahead)}</span>
              <span className="weekly-figure">{magnitude(ahead)}</span>
              <Percent />
            </span>
          </div>
        </div>
      </div>
      <BurnDown week={week} good={good} partRef={ui.part("reset")} />
    </section>
  );
}

/** The half-size % in a figure's upper right. Both figures are percents of
 *  the week's limit, so both carry it. */
function Percent() {
  return (
    <span className="weekly-marks">
      <span className="weekly-pct">%</span>
    </span>
  );
}

/** The logo of the provider whose figures these are, once providers.ts has
 *  one. */
function Provider({ provider, partRef }: { provider?: ReportProvider; partRef: PartRef }) {
  const service = provider?.service ?? "";
  const info = PROVIDERS[service];
  // A logo file that is not there yet leaves the slot empty, not a broken image.
  const [missing, setMissing] = useState(false);
  const title = [info?.name ?? service, provider?.subscription].filter(Boolean).join(" ");
  return (
    <span className="weekly-provider" data-provider={service} title={title} ref={partRef}>
      {info?.logo && !missing &&
        <img src={info.logo} alt={info.name} onError={() => setMissing(true)} />}
    </span>
  );
}

/** Days from now until `resetsAt` (epoch seconds), one decimal place,
 *  never below zero: "6.2 days", "1.0 day" ("6,2 días" where that is the
 *  language's decimal mark). */
export function daysLeft(resetsAt: number, nowMs: number = Date.now()): string {
  const days = Number(Math.max(0, (resetsAt * 1000 - nowMs) / 86_400_000).toFixed(1));
  // The count as shown, one decimal place in the language's own figures;
  // its plural form is chosen by its value.
  return say("left.usage.daysLeft", {
    count: formatNumber(days, undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) });
}

/** The local day `at` (epoch seconds) falls on, in the interface's
 *  language in force: "Fri 23 Oct", "vie, 23 oct". A component showing it
 *  takes useT(), so it is drawn again when the language changes. */
export function resetDay(at: number): string {
  const piece = (options: Intl.DateTimeFormatOptions) => formatDateTime(at * 1000, options);
  return say("left.usage.resetDay", { weekday: piece({ weekday: "short" }), day: piece({ day: "numeric" }),
                                    month: piece({ month: "short" }) });
}

/** The burn-down with the days left above it and the reset day below it. */
function BurnDown({ week, good, partRef }: { week: Week; good: boolean; partRef: PartRef }) {
  useT();   // drawn again in a new language: daysLeft and resetDay say it in the one in force
  const state = good ? "ahead" : "behind";
  return (
    <div className="weekly-burn" ref={partRef}>
      <span className={`weekly-days ${state}`}>{daysLeft(week.resets_at!)}</span>
      <Chart week={week} good={good} />
      <span className={`weekly-reset ${state}`}>{resetDay(week.resets_at!)}</span>
    </div>
  );
}

/** Remaining (100% down to 0%) across the week: the straight line, and the
 *  readings so far. */
function Chart({ week, good }: { week: Week; good: boolean }) {
  const tr = useT();
  const W = 280, H = 40;
  const start = week.week_start!, end = week.resets_at!;
  const x = (t: number) => ((t - start) / (end - start)) * W;
  const y = (used: number) => (used / 100) * H;          // 0% used at the top
  const readings = [{ at: start, used: 0 }, ...(week.points ?? [])];
  const path = readings.map((p) => `${x(p.at).toFixed(1)},${y(p.used).toFixed(1)}`).join(" ");
  // The gap between our path and the straight line, up to the last reading,
  // filled solid: green when ahead of the line, red when behind it. The
  // straight line's y at any x is x * H / W.
  const lastX = x(readings[readings.length - 1].at);
  const gap = `${path} ${lastX.toFixed(1)},${(lastX * H / W).toFixed(1)}`;
  const state = good ? "ahead" : "behind";
  return (
    <svg className="weekly-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         aria-label={tr("left.usage.burnDown")}>
      <polygon className={`weekly-gap ${state}`} points={gap} />
      <line className="weekly-ideal" x1={0} y1={0} x2={W} y2={H} />
      <polyline className={`weekly-actual ${state}`} points={path} />
      <line className="weekly-now" x1={x(week.observed_at!)} y1={0} x2={x(week.observed_at!)} y2={H} />
    </svg>
  );
}
