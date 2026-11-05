/**
 * What the Diagnostics view works out from a stretch of the voice record
 * (voiceHealth.ts types): the messages grouped, the notifications by source,
 * whether the page was busier than usual just before each drop, and drops
 * per hour with the page quiet and not. Pure: no fetching and no drawing.
 */

import protocol from "../../jarvis/transcribeProtocol.json";
import { formatDateTime, t } from "./locale";
import type { Notice, Sample, VoiceEvent } from "./voiceHealth";

// voice_diagnostics.DROP_KINDS and INCIDENT_KINDS: "dropped" is the page
// throwing away audio it held, its reason in the event's text.
export const DROP_KINDS = new Set(["overload", "dropped"]);
export const INCIDENT_KINDS = new Set(["overload", "decoder-failed", "interrupted", "busy",
                                       "model-unavailable", "dropped"]);
/** An incident's kind in words, or the kind itself for one this page does
 *  not know. */
export const incidentWords = (kind: string): string =>
  INCIDENT_KINDS.has(kind) ? t(`main.voice.incident.${kind as IncidentKind}`) : kind;
type IncidentKind = "overload" | "decoder-failed" | "interrupted" | "busy" | "model-unavailable" | "dropped";

type DropReason = "fellBehind" | "tooSlow" | "backedUp" | "disconnected" | "lostJarvis";
const DROP_REASONS = new Set<string>(protocol.dropReasons);
/** Why the page dropped what it held (the protocol's `dropReasons`), in
 *  words, or null for a reason this page does not know. */
export const dropReasonWords = (reason: string | null): string | null =>
  reason && DROP_REASONS.has(reason) ? t(`main.voice.dropReason.${reason as DropReason}`) : null;

// The page's notice sources (the callers of voiceHealth.notice) and their
// words; a source not here is shown as it was recorded.
const SOURCE_KEYS: Record<string, "status" | "input" | "playback" | "unvoiced" | "assistant"> = {
  Status: "status", Input: "input", Playback: "playback", "Unvoiced speech": "unvoiced", Assistant: "assistant",
};
/** Where a notice came from, in words. */
export const sourceWords = (source: string): string =>
  source in SOURCE_KEYS ? t(`main.voice.source.${SOURCE_KEYS[source]}`) : source;
// The same line again within this long is one group: "×12".
const GROUP_WITHIN_SEC = 60;
// What "just before a drop" means.
const BEFORE_SEC = 30;

export interface MessageGroup { source: string; text: string; first: number; last: number; count: number }

/** The page's text lines, newest first, the same line from the same source
 *  within a minute of the last one grouped. `local` are this page's own,
 *  shown until the record has them (it does within a second or two). */
export function groupMessages(events: VoiceEvent[], local: Notice[] = []): MessageGroup[] {
  const lines = events.filter((e) => e.kind === "message" && e.text)
    .map((e) => ({ source: e.source ?? "", text: e.text!, at: e.at }));
  for (const n of local) {
    const at = n.at / 1000;
    if (!lines.some((l) => l.text === n.text && l.source === n.source && Math.abs(l.at - at) < 3)) {
      lines.push({ source: n.source, text: n.text, at });
    }
  }
  lines.sort((a, b) => a.at - b.at);
  const groups: MessageGroup[] = [];
  for (const l of lines) {
    const g = groups.find((x) => x.source === l.source && x.text === l.text
                                 && l.at - x.last <= GROUP_WITHIN_SEC);
    if (g) { g.last = l.at; g.count++; }
    else groups.push({ source: l.source, text: l.text, first: l.at, last: l.at, count: 1 });
  }
  return groups.sort((a, b) => b.last - a.last);
}

/** Notifications shown, by source, over these samples. */
export function noticesBySource(samples: Sample[]): [string, number][] {
  const totals: Record<string, number> = {};
  for (const s of samples) {
    for (const [source, n] of Object.entries(s.noticesBySource ?? {})) {
      totals[source] = (totals[source] ?? 0) + n;
    }
  }
  return Object.entries(totals).sort((a, b) => b[1] - a[1]);
}

export interface PageLoad { notices: number; longTaskMs: number; renders: number; seconds: number }

function load(samples: Sample[]): PageLoad {
  const measured = samples.filter((s) => s.notices != null);
  const n = measured.length || 1;
  const sum = (f: (s: Sample) => number | null) => measured.reduce((t, s) => t + (f(s) ?? 0), 0) / n;
  return { notices: sum((s) => s.notices), longTaskMs: sum((s) => s.longTaskMs),
           renders: sum((s) => s.renders), seconds: measured.length };
}

/** The page's notifications, browser stalls and renders a second in the
 *  BEFORE_SEC before each drop, against the rest of the stretch. */
export function beforeDrops(samples: Sample[], events: VoiceEvent[]):
    { drops: number; before: PageLoad; normal: PageLoad } {
  const drops = events.filter((e) => DROP_KINDS.has(e.kind)).map((e) => e.at);
  const near = (s: Sample) => drops.some((d) => s.at <= d && s.at > d - BEFORE_SEC);
  return { drops: drops.length, before: load(samples.filter(near)),
           normal: load(samples.filter((s) => !near(s))) };
}

/** Drops per hour of listening, with the page quiet and not. */
export function quietComparison(samples: Sample[], events: VoiceEvent[]):
    { quiet: { hours: number; drops: number }; usual: { hours: number; drops: number } } {
  const quietAt = (t: number) => {
    let best: Sample | null = null;
    for (const s of samples) if (s.at <= t && (!best || s.at > best.at)) best = s;
    return best?.quiet === true;
  };
  const drops = events.filter((e) => DROP_KINDS.has(e.kind));
  const hours = (q: boolean) => samples.filter((s) => (s.quiet === true) === q).length / 3600;
  return {
    quiet: { hours: hours(true), drops: drops.filter((d) => quietAt(d.at)).length },
    usual: { hours: hours(false), drops: drops.filter((d) => !quietAt(d.at)).length },
  };
}

/** "14:49:52" in local time, from unix seconds. */
export const clock = (at: number) =>
  formatDateTime(at * 1000, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
