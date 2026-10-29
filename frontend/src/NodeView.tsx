/**
 * One JARVIS instance in full, in the centre area, opened from its tile in
 * the Network panel (NetworkTile.tsx): whose it is, what it runs, whether it
 * is online and when the team server last heard from it, the last human
 * interaction it recorded (relative and absolute, and where it came from),
 * its CPU and memory with their scope, and its brains: the signed pace, how
 * many of them are measured, and for each account its pace, where it ends
 * the week at its current rate and when it resets.
 *
 * Only what the team server sent (network.ts). A server update is never
 * called human presence.
 */

import { useSyncExternalStore, type ReactNode } from "react";
import { PROVIDERS } from "./providers";
import { resetDay } from "./WeeklyUsage";
import { useTick } from "./WorkflowsView";
import {
  bytesText, cpuPercent, MEASURED_WORDS, memoryPercent, network, openNetwork, paceText, paceWords,
  PRESENCE_WORDS, rosterCurrent, SOURCE_WORDS, spokenAge, whenText,
  type AccountPace, type NodeView as Node, type Resources,
} from "./network";
import { humanReading } from "./NetworkTile";

const ACTIVITY_WORDS: Record<Node["activity_state"], string> = {
  available: "Recorded by this instance",
  never_observed: "None observed yet",
  unsupported: "This instance does not report it",
};

/** "32% · host · read 2 seconds before its update", or why there is none. */
function cpuText(r: Resources): string {
  const cpu = cpuPercent(r);
  return cpu === null ? MEASURED_WORDS[r.state] : `${Math.round(cpu)}% · ${r.scope} · ${readAge(r.age_ms)}`;
}

function memoryText(r: Resources): string {
  const mem = memoryPercent(r);
  return mem === null ? MEASURED_WORDS[r.state]
    : `${bytesText(r.memory_used_bytes!)} of ${bytesText(r.memory_total_bytes!)} (${Math.round(mem)}%) · ${r.scope}`;
}

function readAge(ms: number): string {
  return `read ${spokenAge(ms)} before its update`;
}

/** Where an account ends the week at its current rate. */
function endOfWeek(a: AccountPace): string {
  if (a.left_at_reset_percent !== null) return `at this rate ${Math.round(a.left_at_reset_percent)}% left at reset`;
  if (a.runs_out_hours_before_reset !== null) {
    return `at this rate runs out about ${spokenAge(a.runs_out_hours_before_reset * 3_600_000)} before reset`;
  }
  return "";
}

function Account({ a }: { a: AccountPace }) {
  const name = PROVIDERS[a.provider]?.name ?? a.provider;
  const measured = a.pace_points !== null && a.remaining_percent !== null;
  return (
    <li className="network-account" data-provider={a.provider}>
      <span className="network-account-name">{name} · {a.role === "brain" ? "Brain" : "Standby"}</span>
      <span>
        {measured
          ? [`${Math.round(a.remaining_percent!)}% remaining`, `${paceText(a.pace_points!)}%`, endOfWeek(a)]
              .filter(Boolean).join(" · ")
          : "Not measured"}
      </span>
      <span className="network-account-meta">
        {[a.resets_at ? `resets ${resetDay(Date.parse(a.resets_at) / 1000)}` : "",
          a.age_ms !== null ? readAge(a.age_ms) : ""].filter(Boolean).join(" · ")}
      </span>
    </li>
  );
}

/** A centre detail's head: its name, one line under it, and Close, which
 *  goes back to the view before. */
export function DetailHead({ title, state, className = "", icon }: {
  title: string; state: string; className?: string; icon?: ReactNode;
}) {
  return (
    <section className="subsystem-head">
      {icon}
      <div className="subsystem-head-words">
        <h2>{title}</h2>
        <p className={`subsystem-head-state ${className}`}>{state}</p>
      </div>
      <button type="button" className="workflow-close" title="Close" aria-label="Close"
              onClick={() => openNetwork.set(null)}>×</button>
    </section>
  );
}

/** Labelled facts, one per line: the centre details' one layout. */
export function Facts({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="network-facts">
      {rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>
  );
}

function brainPace(node: Node): string {
  const b = node.brains;
  if (b.configured === 0) return "No brain accounts configured";
  if (b.pace_points === null) return "Not measured";
  return `${paceText(b.pace_points)}% · ${paceWords(b.pace_points)} (average of ${b.measured})`;
}

function coverage(node: Node): string {
  const b = node.brains;
  if (b.configured === 0) return "—";
  return `${b.measured}/${b.configured} measured${b.measured && b.measured < b.configured
    ? " — combined value incomplete" : ""}`;
}

export function NodeView({ nodeId }: { nodeId: string }) {
  useTick(1000);
  const live = useSyncExternalStore(network.subscribe, network.get);
  const status = live.value;
  const snapshot = status?.snapshot ?? null;
  const node = snapshot?.nodes.find((n) => n.node_id === nodeId) ?? null;
  const self = node !== null && node.node_id === snapshot!.you_node_id;
  const current = status !== null && rosterCurrent(status);
  const human = node ? humanReading(node) : null;
  const rows: [string, string][] = node ? [
    ["Owner", node.owner_login],
    ["Instance", node.node_id],
    ["Version / OS", `JARVIS ${node.jarvis_version} · ${node.os}`],
    ["Presence", current ? PRESENCE_WORDS[node.presence] : "Inventory stale; current presence unknown"],
    ["Last server update", whenText(node.last_seen_at)],
    ["Last human interaction", human!.ageMs === null ? (node.activity_state === "unsupported"
      ? "Activity unavailable" : "No activity observed")
      : `${whenText(node.last_human_activity_at)}${node.last_human_activity_source
        ? ` · ${SOURCE_WORDS[node.last_human_activity_source]}` : ""}`],
    ["Activity data", ACTIVITY_WORDS[node.activity_state]],
    ["CPU", cpuText(node.resources)],
    ["Memory", memoryText(node.resources)],
    ["Brain pace", brainPace(node)],
    ["Quota coverage", coverage(node)],
    ["Average remaining", node.brains.average_remaining_percent === null ? "—"
      : `${Math.round(node.brains.average_remaining_percent)}%${
        node.brains.measured < node.brains.configured ? " · partial" : ""}`],
  ] : [];
  return (
    <div id="network-detail" className="network-detail" data-node={nodeId}>
      <DetailHead title={`${node ? node.label : "Instance"}${self ? " · This Mac" : ""}`}
                  state="Values from Team Server" />
      {!current && node && (
        <p className="network-stale">Values are stale: the last arrived {whenText(status!.received_at)}.</p>
      )}
      {!node && <p className="network-waiting">The team server no longer lists this instance.</p>}
      {node && <Facts rows={rows} />}
      {node && node.brains.accounts.length > 0 && (
        <section aria-label="Brain accounts">
          <h3>Brain accounts</h3>
          <ul className="network-accounts">
            {node.brains.accounts.map((a) => <Account key={a.account_ref} a={a} />)}
          </ul>
        </section>
      )}
    </div>
  );
}
