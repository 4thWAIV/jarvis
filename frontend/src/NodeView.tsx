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

import { CENTRE_SOCKET } from "./ConversationView";
import { formatPercent, t, useT } from "./locale";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { useSyncExternalStore, type ReactNode } from "react";
import { PROVIDERS } from "./providers";
import { resetDay } from "./WeeklyUsage";
import { useTick } from "./WorkflowsView";
import {
  bytesText, cpuPercent, MEASURED_WORDS, memoryPercent, network, openNetwork, paceText, paceWords,
  PRESENCE_WORDS, rosterCurrent, SOURCE_WORDS, whenText,
  type AccountPace, type NodeView as Node, type Resources,
} from "./network";
import { spokenAge } from "./timeFormat";
import { humanReading } from "./NetworkTile";

/** A whole percentage, from a percentage figure (32 is "32%"). */
const percent = (value: number) => formatPercent(Math.round(value) / 100);

/** "32% · host · read 2 seconds before its update", or why there is none. */
function cpuText(r: Resources): string {
  const cpu = cpuPercent(r);
  return cpu === null ? MEASURED_WORDS[r.state] : `${percent(cpu)} · ${r.scope} · ${readAge(r.age_ms)}`;
}

function memoryText(r: Resources): string {
  const mem = memoryPercent(r);
  return mem === null ? MEASURED_WORDS[r.state]
    : `${t("main.node.memoryUsed", { used: bytesText(r.memory_used_bytes!), total: bytesText(r.memory_total_bytes!),
                                     percent: percent(mem) })} · ${r.scope}`;
}

function readAge(ms: number): string {
  return t("main.node.readAge", { age: spokenAge(ms) });
}

/** Where an account ends the week at its current rate. */
function endOfWeek(a: AccountPace): string {
  if (a.left_at_reset_percent !== null) return t("main.node.leftAtReset", { percent: percent(a.left_at_reset_percent) });
  if (a.runs_out_hours_before_reset !== null) {
    return t("main.node.runsOut", { age: spokenAge(a.runs_out_hours_before_reset * 3_600_000) });
  }
  return "";
}

function Account({ a }: { a: AccountPace }) {
  const t = useT();
  const name = PROVIDERS[a.provider]?.name ?? a.provider;
  const measured = a.pace_points !== null && a.remaining_percent !== null;
  return (
    <li className="network-account" data-provider={a.provider}>
      <span className="network-account-name">{name} · {a.role === "brain" ? t("main.node.brain") : t("main.node.standby")}</span>
      <span>
        {measured
          ? [t("main.node.remaining", { percent: percent(a.remaining_percent!) }), `${paceText(a.pace_points!)}%`, endOfWeek(a)]
              .filter(Boolean).join(" · ")
          : t("main.node.notMeasured")}
      </span>
      <span className="network-account-meta">
        {[a.resets_at ? t("main.node.resets", { day: resetDay(Date.parse(a.resets_at) / 1000) }) : "",
          a.age_ms !== null ? readAge(a.age_ms) : ""].filter(Boolean).join(" · ")}
      </span>
    </li>
  );
}

/** A centre detail's head: its name, one line under it, and Close
 *  (`network.close`), which goes back to the view before. `partRef`, the
 *  detail's registered target's, takes the title as `name` and Close as
 *  `close`. */
export function DetailHead({ title, state, className = "", icon, partRef }: {
  title: string; state: string; className?: string; icon?: ReactNode;
  partRef?: (part: string) => (el: Element | null) => void;
}) {
  const t = useT();
  return (
    <section className="subsystem-head">
      {icon}
      <div className="subsystem-head-words">
        <h2 ref={partRef?.("name")}>{title}</h2>
        <p className={`subsystem-head-state ${className}`}>{state}</p>
      </div>
      <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")}
              ref={partRef?.("close")}
              {...bindingProps(bind("network.close", {}))}>×</button>
    </section>
  );
}

/** Labelled facts, one per line: the centre details' one layout. A row
 *  naming a part registers its value's own extent (`partRef`), so the
 *  brain points at the value, not the whole line. */
export function Facts({ rows, partRef }: {
  rows: [string, string, string?][];
  partRef?: (part: string) => (el: Element | null) => void;
}) {
  return (
    <dl className="network-facts">
      {rows.map(([k, v, part]) => (
        <div key={k}><dt>{k}</dt><dd>{part && partRef ? <span ref={partRef(part)}>{v}</span> : v}</dd></div>
      ))}
    </dl>
  );
}

function brainPace(node: Node): string {
  const b = node.brains;
  if (b.configured === 0) return t("main.node.noAccounts");
  if (b.pace_points === null) return t("main.node.notMeasured");
  return t("main.node.pace", { pace: `${paceText(b.pace_points)}%`, words: paceWords(b.pace_points), count: b.measured });
}

function coverage(node: Node): string {
  const b = node.brains;
  if (b.configured === 0) return "—";
  return b.measured && b.measured < b.configured
    ? t("main.node.measuredIncomplete", { measured: b.measured, configured: b.configured })
    : t("main.node.measured", { measured: b.measured, configured: b.configured });
}

export function NodeView({ nodeId }: { nodeId: string }) {
  useTick(1000);
  const t = useT();
  const live = useSyncExternalStore(network.subscribe, network.get);
  const status = live.value;
  const snapshot = status?.snapshot ?? null;
  const node = snapshot?.nodes.find((n) => n.node_id === nodeId) ?? null;
  const self = node !== null && node.node_id === snapshot!.you_node_id;
  const current = status !== null && rosterCurrent(status);
  const human = node ? humanReading(node) : null;
  const ui = useUiTarget(node ? { ref: { region: "main", view: "main.network", kind: "node", key: node.node_id },
                                    label: node.label, socket: CENTRE_SOCKET } : null);
  const average = node?.brains.average_remaining_percent ?? null;
  const rows: [string, string, string?][] = node ? [
    [t("main.node.owner"), node.owner_login, "owner"],
    [t("tabs.main.network"), node.node_id],
    [t("main.node.version"), `JARVIS ${node.jarvis_version} · ${node.os}`, "version"],
    [t("main.node.presence"), current ? PRESENCE_WORDS[node.presence] : t("main.node.presenceStale"), "presence"],
    [t("main.node.lastUpdate"), whenText(node.last_seen_at), "lastUpdate"],
    [t("main.node.lastHuman"), human!.ageMs === null ? (node.activity_state === "unsupported"
      ? t("main.node.activityUnavailable") : t("main.node.noActivity"))
      : `${whenText(node.last_human_activity_at)}${node.last_human_activity_source
        ? ` · ${SOURCE_WORDS[node.last_human_activity_source]}` : ""}`, "human"],
    [t("main.node.activityData"), t(`main.node.activity.${node.activity_state}`)],
    ["CPU", cpuText(node.resources), "cpu"],
    [t("main.node.memory"), memoryText(node.resources), "memory"],
    [t("main.node.brainPace"), brainPace(node), "pace"],
    [t("main.node.coverage"), coverage(node), "coverage"],
    [t("main.node.averageRemaining"), average === null ? "—"
      : node.brains.measured < node.brains.configured ? t("main.node.partial", { percent: percent(average) }) : percent(average),
     "remaining"],
  ] : [];
  return (
    <div id="network-detail" className="network-detail" data-node={nodeId} ref={ui.whole}>
      <DetailHead title={node ? (self ? t("main.node.thisMac", { name: node.label }) : node.label) : t("tabs.main.network")}
                  state={t("main.node.fromTeamServer")} partRef={node ? ui.part : undefined} />
      {!current && node && (
        <p className="network-stale">{t("main.node.stale", { when: whenText(status!.received_at) })}</p>
      )}
      {!node && <p className="network-waiting">{t("main.node.gone")}</p>}
      {node && <Facts rows={rows} partRef={ui.part} />}
      {node && node.brains.accounts.length > 0 && (
        <section aria-label={t("main.node.accounts")} ref={ui.part("accounts")}>
          <h3>{t("main.node.accounts")}</h3>
          <ul className="network-accounts">
            {node.brains.accounts.map((a) => <Account key={a.account_ref} a={a} />)}
          </ul>
        </section>
      )}
    </div>
  );
}
