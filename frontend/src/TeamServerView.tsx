/**
 * The team server's health in full, in the centre area, opened from the
 * Team Server tile (TeamServerTile.tsx). The database first: whether it is
 * connected and why not, its ping, the last successful write, the last
 * database error, failures in a row, and their last fifteen minutes. Then
 * the round trip and last contact (this JARVIS's own), the version, commit
 * and uptime, and last the server's CPU and memory with their scope.
 *
 * Only what the team server sent (network.ts), apart from the round trip,
 * the last contact and the connection, which only this JARVIS can know.
 */

import { useSyncExternalStore } from "react";
import { useTick } from "./WorkflowsView";
import {
  bytesText, cpuPercent, MEASURED_WORDS, memoryPercent, network, serverReading,
  whenText, type HealthPoint, type NetworkStatus, type ServerHealth,
} from "./network";
import { DetailHead, Facts } from "./NodeView";
import { readinessClass, ServerIcon } from "./TeamServerTile";

const CONNECTION_WORDS: Record<NetworkStatus["connection"], string> = {
  connected: "Connected", stale: "Can't reach the team server", signed_out: "Signed out",
  connecting: "Connecting", pairing: "Waiting for approval", disconnected: "Not connected",
};

/** The last fifteen minutes: the ping as a line, broken where there was
 *  none, and a red mark at each failed write. */
function History({ points }: { points: HealthPoint[] }) {
  if (!points.length) return <p className="network-waiting">No history yet.</p>;
  const W = 300, H = 48;
  const top = Math.max(10, ...points.map((p) => p.database_ping_ms ?? 0));
  const x = (i: number) => (points.length === 1 ? W / 2 : (i / (points.length - 1)) * W);
  const y = (ms: number) => H - (ms / top) * (H - 4);
  const runs: string[][] = [[]];
  points.forEach((p, i) => {
    if (p.database_ping_ms === null) runs.push([]);
    else runs[runs.length - 1].push(`${x(i).toFixed(1)},${y(p.database_ping_ms).toFixed(1)}`);
  });
  return (
    <svg className="health-history" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none"
         role="img" aria-label={`Database ping over the last ${points.length} readings, highest ${Math.round(top)} ms; ${
           points.filter((p) => p.write_ok === false).length} failed writes`}>
      {runs.filter((r) => r.length).map((r, i) => <polyline key={i} className="health-ping" points={r.join(" ")} />)}
      {points.map((p, i) => p.write_ok === false
        && <line key={`f${i}`} className="health-failed" x1={x(i)} x2={x(i)} y1={0} y2={H} />)}
    </svg>
  );
}

function uptime(sec: number): string {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}

function resources(h: ServerHealth): string {
  const r = h.resources;
  const cpu = cpuPercent(r), mem = memoryPercent(r);
  if (cpu === null && mem === null) return MEASURED_WORDS[r.state];
  return [cpu === null ? "CPU —" : `CPU ${Math.round(cpu)}%`,
          mem === null ? "memory —" : `${bytesText(r.memory_used_bytes!)} of ${bytesText(r.memory_total_bytes!)}`,
          r.scope].join(" · ");
}

export function TeamServerView() {
  useTick(1000);
  const live = useSyncExternalStore(network.subscribe, network.get);
  const status = live.value;
  const r = serverReading(status);
  const h = r.health;
  const unknown = "Unknown from here";
  const database: [string, string][] = [
    ["Database connection", !h ? unknown : h.database === "connected" ? "Connected"
      : `Unreachable${h.last_database_error ? ` · ${h.last_database_error}` : ""}`],
    ["Database ping", !h || h.database_ping_ms === null ? "—"
      : `${Math.round(h.database_ping_ms)} ms · checked ${whenText(h.database_checked_at)}`],
    ["Last successful write", !h ? unknown : h.last_write_ok_at ? whenText(h.last_write_ok_at) : "None on record"],
    ["Last database error", !h ? unknown : h.last_database_error ?? "None"],
    ["Failures in a row", !h ? unknown : String(h.database_failures_in_a_row)],
  ];
  const contact: [string, string][] = [
    ["Round trip", r.server.ms === null ? "—" : `${Math.round(r.server.ms)} ms`],
    ["Last contact", whenText(status?.last_contact_at)],
    ["Connection", status ? CONNECTION_WORDS[status.connection] : live.error ? `Couldn't read it (${live.error})` : "Reading…"],
    ...(status?.last_error ? [["Last exchange error", status.last_error] as [string, string]] : []),
  ];
  const service: [string, string][] = [
    ["Version / commit", h ? `${h.version} · ${h.commit}` : "—"],
    ["Uptime", h ? uptime(h.uptime_sec) : "—"],
    ["CPU / memory", h ? resources(h) : "—"],
  ];
  return (
    <div id="network-detail" className="network-detail" data-node="server">
      <DetailHead title="Team server" state={r.readiness} className={`readiness-${readinessClass(r.readiness)}`}
                  icon={<span className={`server-beacon readiness-${readinessClass(r.readiness)}`}><ServerIcon /></span>} />
      <section aria-label="Database">
        <h3>Database</h3>
        <Facts rows={database} />
        <h3>Last 15 minutes</h3>
        {h ? <History points={h.history} /> : <p className="network-waiting">{unknown}.</p>}
      </section>
      <section aria-label="Connection">
        <h3>Connection</h3>
        <Facts rows={contact} />
      </section>
      <section aria-label="Service">
        <h3>Service</h3>
        <Facts rows={service} />
      </section>
    </div>
  );
}
