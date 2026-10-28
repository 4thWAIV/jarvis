/**
 * The left column's team server indicator: one dot and one word saying
 * whether JARVIS is online with its team server. It reads the team server's
 * row of the subsystem list (subsystems.ts, `team_server` in
 * subsystem_diagnostics.py), so it moves with every "subsystems" hint; the
 * full line is in its title, and a click opens that row's detail.
 */

import { openSubsystemRow, useSubsystems } from "./SubsystemList";
import type { SubsystemState } from "./subsystems";

const WORDS: Record<SubsystemState, string> = {
  ok: "Online", degraded: "Offline", down: "Offline", unknown: "Connecting", idle: "Not connected",
};

export function TeamIndicator() {
  const row = useSubsystems().list.find((s) => s.id === "team_server");
  const state: SubsystemState = row?.state ?? "unknown";
  return (
    <button type="button" id="team-indicator" className={`state-${state}`}
            title={row ? `Team server: ${row.summary}` : "Team server: no reading yet"}
            onClick={() => openSubsystemRow("team_server")}>
      <span className={`subsystem-dot state-${state}`} aria-hidden="true" />
      <span className="team-indicator-name">Team server</span>
      <span className="team-indicator-state">{WORDS[state]}</span>
    </button>
  );
}
