/**
 * How long ago something was, the one way the page says it. `shortAge` is
 * the compact figure ("42s", "2m", "3d"), `shortAgo` that figure with "ago"
 * (or "just now"), and `spokenAge` / `spokenAgo` the same in words for a
 * sentence or a title. Each caller only chooses how recent counts as "just
 * now".
 */

/** An age in at most three characters: "0s", "42s", "2m", "1h", "3d", "15w", "2y". */
export function shortAge(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 48 * 3600) return `${Math.floor(s / 3600)}h`;
  const d = Math.floor(s / 86400);
  if (d < 100) return `${d}d`;
  return d < 700 ? `${Math.floor(d / 7)}w` : `${Math.floor(d / 365)}y`;
}

/** "just now" under `justNowMs`, else "2m ago", "3h ago", "2d ago". */
export function shortAgo(ms: number, justNowMs: number): string {
  return ms < justNowMs ? "just now" : `${shortAge(ms)} ago`;
}

const UNITS: [number, string][] = [[86400, "day"], [3600, "hour"], [60, "minute"], [1, "second"]];

/** An age in words: "40 seconds", "2 minutes", "1 hour". */
export function spokenAge(ms: number): string {
  const s = Math.floor(ms / 1000);
  const [size, unit] = UNITS.find(([size]) => s >= size) ?? UNITS[UNITS.length - 1];
  const n = Math.floor(s / size);
  return `${n} ${unit}${n === 1 ? "" : "s"}`;
}

/** "just now" or "2 minutes ago". */
export function spokenAgo(ms: number): string {
  return ms < 10_000 ? "just now" : `${spokenAge(ms)} ago`;
}
