/**
 * How long ago something was, the one way the page says it, in the
 * interface's language (locale.ts). `shortAge` is the compact figure ("42s",
 * "2m", "3d"; "42 s", "2 min" in Spanish), `shortAgo` that figure with "ago"
 * (or "just now"), and `spokenAge` / `spokenAgo` the same in words for a
 * sentence or a title. Each caller only chooses how recent counts as "just
 * now". A component that shows one gets its words with `useT()`, so it is
 * drawn again when the language changes.
 */

import { t } from "./locale";

/** An age in its shortest form: "0s", "42s", "2m", "1h", "3d", "15w", "2y". */
export function shortAge(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return t("time.short.seconds", { n: s });
  if (s < 3600) return t("time.short.minutes", { n: Math.floor(s / 60) });
  if (s < 48 * 3600) return t("time.short.hours", { n: Math.floor(s / 3600) });
  const d = Math.floor(s / 86400);
  if (d < 100) return t("time.short.days", { n: d });
  return d < 700 ? t("time.short.weeks", { n: Math.floor(d / 7) }) : t("time.short.years", { n: Math.floor(d / 365) });
}

/** "just now" under `justNowMs`, else "2m ago", "3h ago", "2d ago". */
export function shortAgo(ms: number, justNowMs: number): string {
  return ms < justNowMs ? t("time.justNow") : t("time.ago", { age: shortAge(ms) });
}

const UNITS = [[86400, "time.days"], [3600, "time.hours"], [60, "time.minutes"], [1, "time.seconds"]] as const;

/** An age in words: "40 seconds", "2 minutes", "1 hour". */
export function spokenAge(ms: number): string {
  const s = Math.floor(ms / 1000);
  const [size, unit] = UNITS.find(([size]) => s >= size) ?? UNITS[UNITS.length - 1];
  return t(unit, { count: Math.floor(s / size) });
}

/** "just now" or "2 minutes ago". */
export function spokenAgo(ms: number): string {
  return ms < 10_000 ? t("time.justNow") : t("time.ago", { age: spokenAge(ms) });
}
