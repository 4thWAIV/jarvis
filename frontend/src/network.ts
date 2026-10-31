/**
 * The Network view's one source: GET /api/team/network (team_api.py), the
 * last network snapshot the team server returned, unchanged, in an envelope
 * of the few facts only this JARVIS knows (when it arrived, whether it is
 * stale, the last exchange error, the round trip and the connection state).
 *
 * The Network panel, every node's detail and the Team Server tile and its
 * detail read only this. Nothing local is merged in: even this machine's own
 * tile shows what the team server sent back about it (`you_node_id` says
 * which one it is). It is read at once, on every "network" hint and every
 * (re)connect snapshot on /ws/sessions (sessionsSocket.ts), and on every
 * (re)connect of the voice socket (liveRead), whether or not the Network tab
 * is open.
 *
 * The types are the team server's models (team_server/teamserver/models.py:
 * NetworkSnapshot and what it holds), as JSON.
 */

import { liveRead } from "./liveRead";
import { createStore } from "./store";
import { spokenAgo } from "./timeFormat";
import { sign } from "./WeeklyUsage";

// ── The team server's snapshot ───────────────────────────────────────────

export type Provider = "anthropic" | "openai";
export type Measured = "measured" | "warming_up" | "unsupported" | "error";
export type ActivitySource = "gui" | "speech" | "typed";

export interface Resources {
  state: Measured;
  scope: "host" | "container";
  cpu_percent: number | null;
  memory_used_bytes: number | null;
  memory_total_bytes: number | null;
  age_ms: number;
}

export interface AccountPace {
  account_ref: string;
  provider: Provider;
  role: "brain" | "standby";
  remaining_percent: number | null;
  /** + more left than an even pace, − heading for the limit. */
  pace_points: number | null;
  resets_at: string | null;
  /** At the current rate; null when it runs out first. */
  left_at_reset_percent: number | null;
  runs_out_hours_before_reset: number | null;
  age_ms: number | null;
}

export interface BrainSummary {
  configured: number;
  measured: number;
  average_remaining_percent: number | null;
  /** The node tile's signed figure. */
  pace_points: number | null;
  accounts: AccountPace[];
}

export interface NodeView {
  node_id: string;
  owner_github_id: number;
  owner_login: string;
  label: string;
  jarvis_version: string;
  os: string;
  presence: "online" | "delayed" | "offline";
  last_seen_at: string;
  resources: Resources;
  brains: BrainSummary;
  activity_state: "never_observed" | "available" | "unsupported";
  last_human_activity_at?: string | null;
  last_human_activity_source?: ActivitySource | null;
}

export interface HealthPoint {
  at: string;
  database_ping_ms: number | null;
  write_ok: boolean | null;
}

export interface ServerHealth {
  database: "connected" | "unreachable";
  database_ping_ms: number | null;
  database_checked_at: string;
  last_write_ok_at: string | null;
  /** The error's class name only. */
  last_database_error: string | null;
  database_failures_in_a_row: number;
  uptime_sec: number;
  version: string;
  commit: string;
  resources: Resources;
  history: HealthPoint[];
}

export interface NetworkSnapshot {
  revision: number;
  server_time: string;
  you_node_id: string;
  nodes: NodeView[];
  server: ServerHealth;
}

// ── This JARVIS's envelope around it ─────────────────────────────────────

/** team_client's state: the same value the team server's subsystem row
 *  reads. The tile's word takes Sign in from it; the rest of the word
 *  follows the network exchange (readiness). */
export type Connection = "connected" | "stale" | "signed_out" | "connecting" | "pairing" | "disconnected";

export interface NetworkStatus {
  /** The last snapshot the team server returned, unchanged; null until one arrives. */
  snapshot: NetworkSnapshot | null;
  received_at: string | null;
  stale: boolean;
  last_error: string | null;
  round_trip_ms: number | null;
  last_contact_at: string | null;
  connection: Connection;
}

/** Moves on every "network" hint on /ws/sessions. */
export const networkChanged = createStore(0);

async function getNetwork(): Promise<NetworkStatus> {
  const res = await fetch("/api/team/network");
  if (!res.ok) throw new Error(`GET /api/team/network: ${res.status}`);
  return res.json();
}

export const network = liveRead(getNetwork, [networkChanged]);

/** What the centre area shows from the network, or null: the team server's
 *  health, or one node. */
export type NetworkOpen = { kind: "server" } | { kind: "node"; nodeId: string };
export const openNetwork = createStore<NetworkOpen | null>(null);

// ── Read out of it, the same way everywhere ──────────────────────────────

/** Milliseconds since an ISO time, by this page's clock; null for none. */
export function sinceMs(iso: string | null | undefined, now: number = Date.now()): number | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  return Number.isNaN(at) ? null : Math.max(0, now - at);
}

/** A time as the page's clock reads it: "28 Oct 22:40:03". */
export function clockTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

/** Relative and absolute together: "3 seconds ago (28 Oct 22:40:03)". */
export function whenText(iso: string | null | undefined, now: number = Date.now()): string {
  const ms = sinceMs(iso, now);
  return ms === null ? "—" : `${spokenAgo(ms)} (${clockTime(iso!)})`;
}

/** Whether the roster is the team server's current word: false while this
 *  JARVIS cannot reach it (its last values are shown, marked stale; no node
 *  is called offline for that). */
export function rosterCurrent(status: NetworkStatus): boolean {
  return status.connection === "connected" && !status.stale && status.snapshot !== null;
}

/** The node first, then the others in the team server's order. */
export function selfFirst(snapshot: NetworkSnapshot): NodeView[] {
  const self = snapshot.nodes.filter((n) => n.node_id === snapshot.you_node_id);
  return [...self, ...snapshot.nodes.filter((n) => n.node_id !== snapshot.you_node_id)];
}

/** A rounded percentage of a measured reading, or null when there is none. */
export function cpuPercent(r: Resources): number | null {
  return r.state === "measured" && r.cpu_percent !== null ? r.cpu_percent : null;
}

export function memoryPercent(r: Resources): number | null {
  if (r.state !== "measured" || r.memory_used_bytes === null || !r.memory_total_bytes) return null;
  return (100 * r.memory_used_bytes) / r.memory_total_bytes;
}

/** A pace with its sign: "+3.0", "−6.5" (a true minus). */
export function paceText(points: number): string {
  return `${sign(points) === "-" ? "−" : "+"}${Math.abs(points).toFixed(1)}`;
}

/** A pace in words: "3.0 points ahead of an even pace". */
export function paceWords(points: number): string {
  return sign(points) === "-"
    ? `${Math.abs(points).toFixed(1)} points behind an even pace, heading for the limit`
    : `${Math.abs(points).toFixed(1)} points ahead of an even pace`;
}

/** Ten points fills one half of the pace rail. */
export function paceWidth(points: number): number {
  return Math.min(Math.abs(points) / 10, 1) * 50;
}

/** "410 MB", "24.0 GB". */
export function bytesText(n: number): string {
  const gb = n / 1024 ** 3;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(n / 1024 ** 2)} MB`;
}

export const MEASURED_WORDS: Record<Measured, string> = {
  measured: "Measured", warming_up: "Warming up", unsupported: "Not supported here", error: "Sampler error",
};

export const SOURCE_WORDS: Record<ActivitySource, string> = {
  gui: "on screen", speech: "by voice", typed: "typed",
};

export const PRESENCE_WORDS: Record<NodeView["presence"], string> = {
  online: "Online", delayed: "Delayed", offline: "Offline",
};

// ── The Team Server tile and its detail ──────────────────────────────────

export type Readiness = "Ready" | "Not ready" | "No contact" | "Sign in";
/** A health row's state: working, failing or not known from here. */
export type RowState = "ok" | "bad" | "unknown";

export interface ServerReading {
  readiness: Readiness;
  /** The server's health, when this JARVIS is in contact and has one. */
  health: ServerHealth | null;
  server: { state: RowState; ms: number | null };
  database: { state: RowState; ms: number | null };
  write: { state: RowState; ageMs: number | null };
}

/** Whether this JARVIS is not signed in to a team server: never paired (or
 *  disconnected), signed out, or part way through signing in
 *  (team_client.NOT_SIGNED_IN). Not known yet is not "not signed in". The
 *  tile's Sign in and the reminder at the top of the centre area
 *  (TeamSignInReminder.tsx) both come from this. */
export function needsSignIn(status: NetworkStatus | null): boolean {
  const connection = status?.connection;
  return connection === "signed_out" || connection === "disconnected" || connection === "pairing";
}

/** The Team Server tile's word, the one place it is decided. Signed out (or
 *  never paired) is Sign in, from the connection. Otherwise the network
 *  exchange decides, not the 30-second status poll, so the word moves as
 *  soon as the exchange does: no snapshot is No contact; a database the
 *  team server says is down is Not ready, stale or not (a 503 with health
 *  is a current answer); any other stale snapshot is No contact, never
 *  Ready; and a fresh one is Ready. */
export function readiness(status: NetworkStatus | null): Readiness {
  if (needsSignIn(status)) return "Sign in";
  const snapshot = status?.snapshot;
  if (!snapshot) return "No contact";
  if (snapshot.server.database !== "connected") return "Not ready";
  return status!.stale ? "No contact" : "Ready";
}

/** A row from the team server's health: failing stays failing, stale or
 *  missing is unknown, and only a current value is working. */
function healthRow(unreachable: boolean, stale: boolean, value: number | null): RowState {
  return unreachable ? "bad" : stale || value === null ? "unknown" : "ok";
}

/** Everything the tile and the detail show about the team server. Values
 *  the team server sent are shown while this JARVIS is signed in, following
 *  the network exchange as the word does, not the status poll; while they
 *  are stale (the last exchange failed) a row that would
 *  look healthy is unknown (a dash); a failing row stays failing. The round
 *  trip is already null after any failed exchange but a 503 with health,
 *  which is a current answer, so it is shown as it is. */
export function serverReading(status: NetworkStatus | null, now: number = Date.now()): ServerReading {
  const word = readiness(status);
  const health = word === "Sign in" ? null : status?.snapshot?.server ?? null;
  const roundTrip = health ? status!.round_trip_ms : null;
  const unreachable = health?.database === "unreachable";
  const stale = status?.stale ?? true;
  const ping = health?.database_ping_ms ?? null;
  const writeAge = health ? sinceMs(health.last_write_ok_at, now) : null;
  const database = health ? healthRow(unreachable, stale, ping) : "unknown";
  const write = health ? healthRow(unreachable, stale, writeAge) : "unknown";
  return {
    readiness: word,
    health,
    server: { state: roundTrip === null ? "unknown" : "ok", ms: roundTrip },
    database: { state: database, ms: database === "ok" ? ping : null },
    // A failing write keeps its age: how long writes have been failing.
    write: { state: write, ageMs: write === "unknown" ? null : writeAge },
  };
}
