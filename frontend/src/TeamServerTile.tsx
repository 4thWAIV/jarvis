/**
 * The left column's Team Server tile, the brain boxes' footprint: a server
 * symbol and its readiness word (Ready, Not ready, No contact, Sign in),
 * beside three rows, each led by a status dot with its figure on the right:
 * Server with the round trip, Database with its ping or "down", and Last
 * write with how long ago a database write last succeeded (the node updates
 * the team server stores are the writes). Green is working, red failing
 * (a diamond, not a dot, and "down"), a hollow amber ring not known (a dash).
 *
 * Everything but the round trip comes from the team server's own answer
 * (network.ts); the round trip is the one figure only this JARVIS can time.
 * A click opens the full health detail in the centre (TeamServerView.tsx):
 * the registered `network.open {kind: server}`, as the brain's request is.
 * The tile is a target the brain can point at from the left rail, with the
 * readiness word and each row as parts.
 */

import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { useSyncExternalStore, type ReactNode } from "react";
import {
  network, openNetwork, serverReading, sinceMs, type RowState, type ServerReading,
} from "./network";
import { shortAge, spokenAge, spokenAgo } from "./timeFormat";
import { useTick } from "./WorkflowsView";

/** The readiness word as a class name: "ready", "not-ready", … */
export function readinessClass(word: string): string {
  return word.toLowerCase().replace(" ", "-");
}

export function ServerIcon() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true">
      <rect x="5" y="4" width="22" height="10" rx="3" />
      <rect x="5" y="18" width="22" height="10" rx="3" />
      <path d="M10 9h1m4 0h7M10 23h1m4 0h7" />
    </svg>
  );
}

/** The whole tile in words, changing with its state. */
function spoken(r: ServerReading): string {
  const parts = [`Team server: ${r.readiness}.`];
  parts.push(r.server.ms === null ? "Round trip unknown." : `Round trip ${Math.round(r.server.ms)} milliseconds.`);
  if (r.database.state === "bad") {
    parts.push("Database down.");
    parts.push(r.write.ageMs === null ? "Writes failing." : `Writes failing for ${spokenAge(r.write.ageMs)}.`);
  } else if (r.database.state === "ok") {
    parts.push(`Database connected, ping ${Math.round(r.database.ms!)} milliseconds.`);
    parts.push(r.write.ageMs === null ? "No write on record." : `Last write succeeded ${spokenAgo(r.write.ageMs)}.`);
  } else {
    parts.push("Database state unknown.");
  }
  parts.push("Open details in the centre.");
  return parts.join(" ");
}

type PartRef = (el: Element | null) => void;

function HealthRow({ name, state, title, partRef, children }: {
  name: string; state: RowState; title: string; partRef: PartRef; children: ReactNode;
}) {
  return (
    <span className={`health-row ${state}`} title={title} data-row={name} ref={partRef}>
      <i aria-hidden="true" />
      <span>{name}</span>
      <strong>{children}</strong>
    </span>
  );
}

export function TeamServerTile() {
  useTick(1000);
  const live = useSyncExternalStore(network.subscribe, network.get);
  const open = useSyncExternalStore(openNetwork.subscribe, openNetwork.get);
  const r = serverReading(live.value);
  const contact = sinceMs(live.value?.last_contact_at);
  const dash = <b>—</b>;
  const ui = useUiTarget({ ref: { region: "left", view: "left.fixed", kind: "teamServer" }, label: "Team server",
                           socket: { rail: "rail.left", side: "left" } });
  return (
    <button type="button" id="team-server-tile" ref={ui.whole}
            className={`compact-tile server-tile readiness-${readinessClass(r.readiness)}${
              open?.kind === "server" ? " selected" : ""}`}
            aria-label={spoken(r)} aria-pressed={open?.kind === "server"}
            {...bindingProps(bind("network.open", { kind: "server" }))}>
      <span className="tile-title" aria-hidden="true">TEAM SERVER</span>
      <span className="tile-body">
        <span className="server-beacon" aria-hidden="true">
          <ServerIcon />
          <strong className="readiness-word" ref={ui.part("readiness")}>{r.readiness}</strong>
        </span>
        <span className="health-rows" aria-hidden="true">
          <HealthRow name="Server" state={r.server.state} partRef={ui.part("server")}
                     title={`Round trip to the team server; last contact ${
                       contact === null ? "never" : spokenAgo(contact)}`}>
            {r.server.ms === null ? dash : <><b>{Math.round(r.server.ms)}</b> ms</>}
          </HealthRow>
          <HealthRow name="Database" state={r.database.state} title="The team server's database; its ping"
                     partRef={ui.part("database")}>
            {r.database.state === "bad" ? <b>down</b>
              : r.database.ms === null ? dash : <><b>{Math.round(r.database.ms)}</b> ms</>}
          </HealthRow>
          <HealthRow name="Last write" state={r.write.state} title="The last successful database write"
                     partRef={ui.part("lastWrite")}>
            {r.write.ageMs === null ? (r.write.state === "bad" ? <b>down</b> : dash)
              : <><b>{shortAge(r.write.ageMs)}</b><small className="ago"> ago</small></>}
          </HealthRow>
        </span>
      </span>
    </button>
  );
}
