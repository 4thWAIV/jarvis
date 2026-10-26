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
 */

import { useEffect, useState } from "react";
import { PROVIDERS, type ReportProvider } from "./providers";

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

function age(sec: number): string {
  if (sec < 90) return "just now";
  if (sec < 5400) return `${Math.round(sec / 60)} min ago`;
  return `${Math.round(sec / 3600)} h ago`;
}

/** One brain's account box. `provider` names whose figures (the running
 *  brain's when left out); `role` marks it, when there is more than one
 *  brain, as the brain in use or the one standing by. */
export function WeeklyUsage({ provider, role }: { provider?: string;
                                                  role?: "brain" | "standby" } = {}) {
  const [week, setWeek] = useState<Week | null>(null);
  // Why the last request failed, if it did. A failed request is said as one:
  // it used to fall through to "not measured yet", which was untrue for as
  // long as the server lacked the route.
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      fetch(provider ? `/api/usage/week?provider=${encodeURIComponent(provider)}` : "/api/usage/week")
        .then((r) => {
          if (!r.ok) throw new Error(r.status === 404
            ? "the server has no weekly usage yet: restart JARVIS"
            : `the server answered ${r.status}`);
          return r.json();
        })
        .then((body: Week) => { setWeek(body); setFailed(null); })
        .catch((e: Error) => setFailed(e.message || "the server did not answer"));
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [provider]);

  const roleTag = role &&
    <span className={`weekly-role ${role}`}>{role === "brain" ? "BRAIN" : "STANDBY"}</span>;
  const box = `weekly-usage${role ? ` role-${role}` : ""}`;

  // A dash stands in for each figure when there is none to show (red if the
  // request failed), with the reason on hover.
  if (failed || !week || !week.measured) {
    const why = failed ? `Couldn't read it: ${failed}` : "Not measured yet";
    const cls = failed ? "behind" : "none";
    return (
      <section className={box} title={why} data-provider={provider}>
        {roleTag}
        <div className="account-rows">
          <div className="account-row available">
            <span className="weekly-avatar" aria-hidden="true" />
            <span className={`weekly-left ${cls}`}>—</span>
          </div>
          <div className="account-row pace">
            <Provider provider={week?.provider ?? (provider ? { service: provider } : undefined)} />
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
    <section className={box} data-provider={provider}
             title={`${Math.round(week.expected_used!)}% of the week gone, ${Math.round(week.used!)}% used, reading ${age(week.age_sec!)}`}>
      {roleTag}
      <div className="account-rows">
        <div className="account-row available">
          <span className="weekly-avatar" aria-hidden="true" />
          <span className={`weekly-left ${good ? "ahead" : "behind"}`}>
            <span className="weekly-group">
              <span className="weekly-figure">{Math.round(left)}</span>
              <Percent />
            </span>
          </span>
        </div>
        <div className="account-row pace">
          <Provider provider={week.provider} />
          <div className={`weekly-ahead ${good ? "ahead" : "behind"}`}>
            <span className="weekly-group">
              <span className={`weekly-sign ${sign(ahead) === "+" ? "plus" : "minus"}`}>{sign(ahead)}</span>
              <span className="weekly-figure">{magnitude(ahead)}</span>
              <Percent />
            </span>
          </div>
        </div>
      </div>
      <BurnDown week={week} good={good} />
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
function Provider({ provider }: { provider?: ReportProvider }) {
  const service = provider?.service ?? "";
  const info = PROVIDERS[service];
  // A logo file that is not there yet leaves the slot empty, not a broken image.
  const [missing, setMissing] = useState(false);
  const title = [info?.name ?? service, provider?.subscription].filter(Boolean).join(" ");
  return (
    <span className="weekly-provider" data-provider={service} title={title}>
      {info?.logo && !missing &&
        <img src={info.logo} alt={info.name} onError={() => setMissing(true)} />}
    </span>
  );
}

/** Days from now until `resetsAt` (epoch seconds), one decimal place,
 *  never below zero: "6.2 days", "1.0 day". */
export function daysLeft(resetsAt: number, nowMs: number = Date.now()): string {
  const days = Math.max(0, (resetsAt * 1000 - nowMs) / 86_400_000);
  const shown = days.toFixed(1);
  return `${shown} ${shown === "1.0" ? "day" : "days"}`;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** The local day `at` (epoch seconds) falls on: "Fri 23 Oct". */
export function resetDay(at: number): string {
  const d = new Date(at * 1000);
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** The burn-down with the days left above it and the reset day below it. */
function BurnDown({ week, good }: { week: Week; good: boolean }) {
  const state = good ? "ahead" : "behind";
  return (
    <div className="weekly-burn">
      <span className={`weekly-days ${state}`}>{daysLeft(week.resets_at!)}</span>
      <Chart week={week} good={good} />
      <span className={`weekly-reset ${state}`}>{resetDay(week.resets_at!)}</span>
    </div>
  );
}

/** Remaining (100% down to 0%) across the week: the straight line, and the
 *  readings so far. */
function Chart({ week, good }: { week: Week; good: boolean }) {
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
         aria-label="Burn-down of the week">
      <polygon className={`weekly-gap ${state}`} points={gap} />
      <line className="weekly-ideal" x1={0} y1={0} x2={W} y2={H} />
      <polyline className={`weekly-actual ${state}`} points={path} />
      <line className="weekly-now" x1={x(week.observed_at!)} y1={0} x2={x(week.observed_at!)} y2={H} />
    </svg>
  );
}
