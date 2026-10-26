/**
 * Voice health on the page: what the server says about the audio once a
 * second (the `health` message on /ws/transcribe, voice_diagnostics.py), what
 * the page measures about itself for the server's record (`takeTelemetry`),
 * and every line of text the page shows Tim about voice input or JARVIS's own
 * state (`notice`), which is drawn in the Diagnostics view's Messages instead
 * of under the orb.
 *
 * The record itself is the server's: the Diagnostics views read it from
 * /api/voice/diagnostics*, and /ws/sessions says "voice" when it changes.
 * What is here is only the last two minutes of health for the sparkline and
 * the counts not yet sent.
 */

import { liveRead } from "./liveRead";
import { createStore } from "./store";

export type HealthState = "healthy" | "no-frames" | "browser-behind" | "connection-behind"
  | "server-starved" | "decoder-slow";
export type StageState = "ok" | "warn" | "bad" | "unknown";
export type Stage = "mic" | "browser" | "connection" | "recogniser" | "jarvis";
export const STAGES: Stage[] = ["mic", "browser", "connection", "recogniser", "jarvis"];
export const STAGE_WORDS: Record<Stage, string> = {
  mic: "Mic", browser: "Page", connection: "Link", recogniser: "Recog", jarvis: "Server",
};

export interface Health {
  state: HealthState;
  cause: string;
  backlogSec: number;
  rtf: number | null;
  loopDelayMs: number;
  processCpu: number;
  loadAvg: number;
  pipeline: Record<Stage, { state: StageState; value: number | null }>;
  at: number;                    // page clock, ms
}

/** One second of the server's record (voice_diagnostics.sample_json). */
export interface Sample {
  at: number; capture: string | null;
  receivedSec: number; decodedSec: number; backlogSec: number; oldestSec: number;
  rtf: number | null; loopDelayMs: number; processCpu: number; loadAvg: number;
  framesCaptured: number | null; framesSent: number | null; heldSec: number | null;
  socketSec: number | null; longTaskMs: number | null; longTasks: number | null;
  tabHidden: boolean | null; notices: number | null; noticesBySource: Record<string, number>;
  voiceMsgs: number | null; sessionMsgs: number | null; renders: number | null;
  finals: number; typed: number; quiet: boolean | null; state: HealthState; cause: string;
}

/** One event of the server's record (voice_diagnostics.event_json). */
export interface VoiceEvent {
  id: number; at: number; kind: string; capture: string | null; source: string | null;
  text: string | null; cause: string | null; topProcesses: { name: string; cpu: number }[];
}

export interface Summary {
  state: HealthState | "idle";
  cause: string;
  pipeline: Record<Stage, { state: StageState; value: number | null }>;
  last2min: Sample[];
  incidentsLastHour: { drops: number; reconnects: number; errors: number };
  lastHour: { finals: number; drops: number };
  lastDropAt: number | null;
  today: { listenedSec: number; finals: number; typed: number; drops: number; reconnects: number };
  now: number;
}

export const HEALTH_WORDS: Record<HealthState | "idle", string> = {
  healthy: "Hearing you", "no-frames": "No audio from the mic", "browser-behind": "Browser behind",
  "connection-behind": "Connection backed up", "server-starved": "Server too busy",
  "decoder-slow": "Recogniser behind", idle: "Not listening",
};

export type Tone = "ok" | "warn" | "bad" | "off";

// Speech dropped this recently makes the status "Dropping speech".
const DROPPING_FOR_SEC = 60;

/** How well the audio is working, in one word and one colour: hearing him,
 *  falling behind (any stage not healthy), dropping speech (a drop in the
 *  last minute, or no audio at all), or not listening. */
export function audioStatus(state: HealthState | "idle", lastDropAt: number | null,
                            now = Date.now() / 1000): { word: string; tone: Tone } {
  if (state === "idle") return { word: "Not listening", tone: "off" };
  if (state === "no-frames" || (lastDropAt !== null && now - lastDropAt < DROPPING_FOR_SEC)) {
    return { word: "Dropping speech", tone: "bad" };
  }
  if (state !== "healthy") return { word: "Falling behind", tone: "warn" };
  return { word: "Hearing you", tone: "ok" };
}

/** Sentences heard as a share of sentences heard plus speech dropped, or
 *  null with nothing said. */
export function heardShare(h: { finals: number; drops: number }): number | null {
  const said = h.finals + h.drops;
  return said ? h.finals / said : null;
}

// What the record judges against, drawn on the charts.
export const BACKLOG_WARN_SEC = 1;
export const BACKLOG_DROP_SEC = 2;
const HISTORY_MS = 2 * 60 * 1000;

/** The latest health, or null before any (or once input stops). */
export const health = createStore<Health | null>(null);
/** The last two minutes of health, oldest first. */
export const recentHealth = createStore<Health[]>([]);

export function healthHeard(msg: Record<string, unknown>): void {
  const h = { ...(msg as unknown as Health), at: performance.now() };
  health.set(h);
  const cutoff = h.at - HISTORY_MS;
  recentHealth.set([...recentHealth.get().filter((x) => x.at >= cutoff), h]);
}

export function healthLost(): void {
  health.set(null);
}

// ── the record, read ──────────────────────────────────────────────────────

/** Moves on every "voice" hint on /ws/sessions. */
export const voiceChanged = createStore(0);

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return (await res.json()) as T;
}

export const summary = liveRead(() => getJson<Summary>("/api/voice/diagnostics"), [voiceChanged]);

export const getHistory = (from: number, to: number) =>
  getJson<{ samples: Sample[]; events: VoiceEvent[] }>(
    `/api/voice/diagnostics/samples?from=${from}&to=${to}`);

/** Whether the centre area shows the Diagnostics view. */
export const openDiagnostics = createStore(false);

// ── what the page measures ────────────────────────────────────────────────

export interface Notice { source: string; text: string; at: number }

/** Every notice since the page loaded, newest first (kept to the last 200),
 *  for the Messages list before the server has recorded them. */
export const notices = createStore<Notice[]>([]);
const NOTICES_KEPT = 200;

let counts: Record<string, number> = {};
let unsent: Notice[] = [];
let voiceMsgs = 0;
let sessionMsgs = 0;
let renders = 0;
let longTaskMs = 0;
let longTasks = 0;

/** A line of text the page shows him: counted, listed, and sent to the
 *  record with the next telemetry. The same line again is counted again. */
export function notice(source: string, text: string): void {
  counts[source] = (counts[source] ?? 0) + 1;
  if (!text) return;
  const n = { source, text, at: Date.now() };
  unsent.push(n);
  notices.set([n, ...notices.get()].slice(0, NOTICES_KEPT));
}

export const countVoiceMessage = () => { voiceMsgs++; };
export const countSessionMessage = () => { sessionMsgs++; };
export const countRender = () => { renders++; };

try {
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      longTasks++;
      longTaskMs += entry.duration;
    }
  }).observe({ type: "longtask", buffered: false });
} catch { /* no long-task timing in this browser: reported as none */ }

/** Quiet page: the agents' and workflows' orbs stop moving and the rays are
 *  not drawn, so drops can be compared with the page quiet and not. */
export const quietUntil = createStore<number | null>(null);
export const QUIET_MS = 15 * 60 * 1000;
export const isQuiet = () => {
  const until = quietUntil.get();
  return until !== null && Date.now() < until;
};
export function setQuiet(on: boolean): void {
  quietUntil.set(on ? Date.now() + QUIET_MS : null);
  if (on) setTimeout(() => { if (!isQuiet()) quietUntil.set(null); }, QUIET_MS + 50);
}

/** What the page measured since the last call, for the telemetry message;
 *  the counts start again. The audio figures are voiceInput's. */
export function takeTelemetry(audio: { framesCaptured: number; framesSent: number;
                                       heldSec: number; socketSec: number }) {
  const out = {
    type: "telemetry", ...audio,
    longTaskMs, longTasks, tabHidden: document.visibilityState === "hidden",
    notices: counts, voiceMsgs, sessionMsgs, renders, quiet: isQuiet(),
    messages: unsent.slice(0, 20).map((n) => ({ source: n.source, text: n.text, at: n.at })),
  };
  unsent = unsent.slice(20);
  counts = {};
  voiceMsgs = sessionMsgs = renders = longTasks = 0;
  longTaskMs = 0;
  return out;
}
