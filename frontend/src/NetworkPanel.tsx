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
import { NetworkTile } from "./NetworkTile";
import { network, rosterCurrent, selfFirst, shortAge, sinceMs, type NetworkStatus } from "./network";
import { useTick } from "./WorkflowsView";

/** What to say when there is no roster to show. */
function waiting(status: NetworkStatus | null, error: string | null): string {
  if (!status) return error ? `Couldn't read the network (${error}); trying again.` : "Reading the network…";
  if (status.connection === "disconnected" || status.connection === "pairing") {
    return "Not connected to a team server. Connect in Settings → Team.";
  }
  if (status.connection === "signed_out") return "Signed out of the team server. Sign in again in Settings → Team.";
  return "Waiting for the team server's first answer.";
}

export function NetworkPanel() {
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
        <span>Network</span>
        {snapshot && <span className="roster-count">{current ? `${online} online` : `Last-known: ${nodes.length}`}</span>}
      </div>
      {snapshot && (
        <p className={`roster-freshness${current ? "" : " stale"}`}>
          From Team Server · {age === null ? "" : current ? `updated ${shortAge(age)} ago` : `stale, ${shortAge(age)} old`}
        </p>
      )}
      {!snapshot && <p className="network-waiting">{waiting(status, live.error)}</p>}
      {snapshot && !nodes.length && <p className="network-waiting">The team server lists no instances.</p>}
      {nodes.map((n) => (
        <NetworkTile key={n.node_id} node={n} self={n.node_id === snapshot!.you_node_id} current={current} />
      ))}
    </div>
  );
}
