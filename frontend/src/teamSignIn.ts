/**
 * Signing this JARVIS in to its team server from outside Settings (the
 * reminder at the top of the centre area, TeamSignInReminder.tsx).
 *
 * With an address saved (`last_server_url` on GET /api/team, team_client's
 * snapshot), it is Settings, Team's own flow: POST /api/team/connect with
 * that address, the team server's approval page opened in a new tab, and
 * Settings opened at Team, where the code to check against that page is
 * shown and followed. Part way through signing in already, or with no
 * address saved, it only opens Settings at Team: the code, or the address
 * field.
 */

import type { Connection } from "./network";
import { openSettings } from "./Settings";

/** The part of GET /api/team (team_client.snapshot) signing in needs. */
interface TeamStart {
  state: Connection;
  last_server_url: string | null;
  pairing: { verification_uri_complete: string } | null;
}

/** One /api/team request; a refusal is thrown with the backend's words. */
async function team(method: "GET" | "POST", url: string, body?: unknown): Promise<TeamStart> {
  const res = await fetch(url, {
    method, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload: unknown = null;
  try { payload = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const err = (payload as { error?: { message?: string } } | null)?.error;
    throw new Error(err?.message || `The request failed (${res.status}).`);
  }
  return payload as TeamStart;
}

export async function signIn(): Promise<void> {
  const status = await team("GET", "/api/team");
  const address = status.last_server_url;
  if (status.state !== "pairing" && address) {
    const started = await team("POST", "/api/team/connect", { server_url: address });
    if (started.pairing) {
      window.open(started.pairing.verification_uri_complete, "_blank", "noopener,noreferrer");
    }
  }
  openSettings("team");
}
