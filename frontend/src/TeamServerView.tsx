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
import { formatNumber, formatPercent, t, useT } from "./locale";
import { useTick } from "./WorkflowsView";
import {
  bytesText, cpuPercent, MEASURED_WORDS, memoryPercent, network, READINESS_WORDS, serverReading,
  whenText, type HealthPoint, type NetworkStatus, type ServerHealth,
} from "./network";
import { CENTRE_SOCKET } from "./ConversationView";
import { DetailHead, Facts } from "./NodeView";
import { useUiTarget } from "./uiRegistry";
import { readinessClass, ServerIcon } from "./TeamServerTile";

/** How the connection to the team server stands, in words. */
const connectionWords = (connection: NetworkStatus["connection"]): string => t(`main.server.connection.${connection}`);

/** A whole number of milliseconds, in the language's own digits. */
const ms = (value: number) => `${formatNumber(Math.round(value))} ms`;

/** The last fifteen minutes: the ping as a line, broken where there was
 *  none, and a red mark at each failed write. */
function History({ points }: { points: HealthPoint[] }) {
  const t = useT();
  if (!points.length) return <p className="network-waiting">{t("main.server.noHistory")}</p>;
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
         role="img" aria-label={t("main.server.historyLabel", { readings: points.length, highest: ms(top),
                                                                 failed: points.filter((p) => p.write_ok === false).length })}>
      {runs.filter((r) => r.length).map((r, i) => <polyline key={i} className="health-ping" points={r.join(" ")} />)}
      {points.map((p, i) => p.write_ok === false
        && <line key={`f${i}`} className="health-failed" x1={x(i)} x2={x(i)} y1={0} y2={H} />)}
    </svg>
  );
}

function uptime(sec: number): string {
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  const short = (key: "time.short.days" | "time.short.hours" | "time.short.minutes", n: number) => t(key, { n });
  return d ? `${short("time.short.days", d)} ${short("time.short.hours", h)}`
    : h ? `${short("time.short.hours", h)} ${short("time.short.minutes", m)}` : short("time.short.minutes", m);
}

function resources(h: ServerHealth): string {
  const r = h.resources;
  const cpu = cpuPercent(r), mem = memoryPercent(r);
  if (cpu === null && mem === null) return MEASURED_WORDS[r.state];
  return [cpu === null ? "CPU —" : `CPU ${formatPercent(Math.round(cpu) / 100)}`,
          mem === null ? t("main.server.memoryUnknown")
            : t("main.server.memoryUsed", { used: bytesText(r.memory_used_bytes!), total: bytesText(r.memory_total_bytes!) }),
          r.scope].join(" · ");
}

export function TeamServerView() {
  useTick(1000);
  const t = useT();
  const live = useSyncExternalStore(network.subscribe, network.get);
  const status = live.value;
  const r = serverReading(status);
  const h = r.health;
  // Pointed at as its database, connection and service sections.
  const ui = useUiTarget({ ref: { region: "main", view: "main.network", kind: "teamServer" },
                           label: t("tabs.main.teamServer"), socket: CENTRE_SOCKET });
  const unknown = t("main.server.unknown");
  // Stale values never look healthy: what would is unknown; failures stay.
  const current = (healthy: boolean, text: string) =>
    status?.stale && healthy ? t("main.server.staleUnknown") : text;
  // The team server's own error words, as it sent them.
  const dbError = h?.last_database_error ?? null;
  const database: [string, string][] = !h ? [
    [t("main.server.dbConnection"), unknown], [t("main.server.dbPing"), "—"], [t("main.server.lastWrite"), unknown],
    [t("main.server.lastDbError"), unknown], [t("main.server.failuresInARow"), unknown],
  ] : [
    [t("main.server.dbConnection"), h.database === "connected" ? current(true, t("main.server.connection.connected"))
      : dbError ? t("main.server.unreachableBecause", { error: dbError }) : t("main.server.unreachable")],
    [t("main.server.dbPing"), r.database.ms === null ? "—"
      : t("main.server.pingChecked", { ping: ms(r.database.ms), when: whenText(h.database_checked_at) })],
    [t("main.server.lastWrite"), current(r.write.state !== "bad",
      h.last_write_ok_at ? whenText(h.last_write_ok_at) : t("main.server.noneOnRecord"))],
    [t("main.server.lastDbError"), current(dbError === null, dbError ?? t("main.server.none"))],
    [t("main.server.failuresInARow"), current(h.database_failures_in_a_row === 0, formatNumber(h.database_failures_in_a_row))],
  ];
  const contact: [string, string][] = [
    [t("main.server.roundTrip"), r.server.ms === null ? "—" : ms(r.server.ms)],
    [t("main.server.lastContact"), whenText(status?.last_contact_at)],
    [t("main.server.connectionLabel"), status ? connectionWords(status.connection)
      : live.error ? t("main.server.unreadable", { error: live.error }) : t("main.server.reading")],
    ...(status?.last_error ? [[t("main.server.lastExchangeError"), status.last_error] as [string, string]] : []),
  ];
  const service: [string, string][] = [
    [t("main.server.version"), h ? `${h.version} · ${h.commit}` : "—"],
    [t("main.server.uptime"), h ? uptime(h.uptime_sec) : "—"],
    [t("main.server.cpuMemory"), h ? resources(h) : "—"],
  ];
  return (
    <div id="network-detail" className="network-detail" data-node="server" ref={ui.whole}>
      <DetailHead title={t("tabs.main.teamServer")} state={READINESS_WORDS[r.readiness]} className={`readiness-${readinessClass(r.readiness)}`}
                  icon={<span className={`server-beacon readiness-${readinessClass(r.readiness)}`}><ServerIcon /></span>} />
      <section aria-label={t("main.server.database")} ref={ui.part("database")}>
        <h3>{t("main.server.database")}</h3>
        <Facts rows={database} />
        <h3>{t("main.server.last15")}</h3>
        {h ? <History points={h.history} /> : <p className="network-waiting">{unknown}</p>}
      </section>
      <section aria-label={t("main.server.connectionLabel")} ref={ui.part("connection")}>
        <h3>{t("main.server.connectionLabel")}</h3>
        <Facts rows={contact} />
      </section>
      <section aria-label={t("main.server.service")} ref={ui.part("service")}>
        <h3>{t("main.server.service")}</h3>
        <Facts rows={service} />
      </section>
    </div>
  );
}
