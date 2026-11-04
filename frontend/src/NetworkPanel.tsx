/**
 * The right panel's Network tab: every JARVIS instance the team server
 * reports, this machine's first (the team server says which: `you_node_id`),
 * one compact tile each (NetworkTile.tsx). Above them, how many are online
 * and how fresh the roster is. While this JARVIS cannot reach the team
 * server the roster is its last answer, marked stale, and no instance is
 * called offline for that.
 *
 * Only what the team server sent (network.ts); nothing local.
 */

import { useSyncExternalStore } from "react";
import { useT, type Translate } from "./locale";
import { NetworkTile } from "./NetworkTile";
import { network, rosterCurrent, selfFirst, sinceMs, type NetworkStatus } from "./network";
import { shortAge } from "./timeFormat";
import { useTick } from "./WorkflowsView";

/** What to say when there is no roster to show. */
function waiting(t: Translate, status: NetworkStatus | null, error: string | null): string {
  if (!status) return error ? t("right.network.readFailed", { error }) : t("right.network.reading");
  if (status.connection === "disconnected" || status.connection === "pairing") {
    return t("right.network.notConnected");
  }
  if (status.connection === "signed_out") return t("right.network.signedOut");
  return t("right.network.firstAnswer");
}

export function NetworkPanel() {
  const t = useT();
  useTick(1000);
  const live = useSyncExternalStore(network.subscribe, network.get);
  const status = live.value;
  const snapshot = status?.snapshot ?? null;
  const current = status !== null && rosterCurrent(status);
  const nodes = snapshot ? selfFirst(snapshot) : [];
  const age = sinceMs(status?.received_at);
  const online = nodes.filter((n) => n.presence === "online").length;
  return (
    <div className="right-view network-view">
      <div className="network-heading">
        <span>{t("tabs.right.network")}</span>
        {snapshot && <span className="roster-count">
          {current ? t("right.network.online", { count: online }) : t("right.network.lastKnown", { count: nodes.length })}
        </span>}
      </div>
      {snapshot && (
        <p className={`roster-freshness${current ? "" : " stale"}`}>
          {age === null ? t("right.network.from")
            : t(current ? "right.network.fromUpdated" : "right.network.fromStale", { age: shortAge(age) })}
        </p>
      )}
      {!snapshot && <p className="network-waiting">{waiting(t, status, live.error)}</p>}
      {snapshot && !nodes.length && <p className="network-waiting">{t("right.network.noInstances")}</p>}
      {nodes.map((n) => (
        <NetworkTile key={n.node_id} node={n} self={n.node_id === snapshot!.you_node_id} current={current} />
      ))}
    </div>
  );
}
