/**
 * Signing this JARVIS in to its team server: the one path Settings, Team
 * (TeamSection.tsx) and the reminder at the top of the centre area
 * (TeamSignInReminder.tsx) both take, and the request helper the Team
 * section's other calls use.
 *
 * Signing in is POST /api/team/connect with the team server's address
 * (team_api.py starts the pairing), then the team server's approval page in
 * a new tab, where the code JARVIS shows is checked and this Mac approved.
 */

import { codedSaid, t, type CodedLine, type Said } from "./locale";
import type { Connection } from "./network";

/** What JARVIS says about the team server (team_client's TEAM_SAID): by
 *  its code (`said` beside the English on a refusal, `last_error_said` on
 *  the status and the network exchange) in the language in force, with the
 *  team server's own words after it as they came; without a code, the
 *  English as it came. */
export const teamSaid = (line: CodedLine | null | undefined, english: string): Said =>
  codedSaid("team.error", line, english);

/** A refusal from /api/team*: its English (`message`, as before) and,
 *  when JARVIS said it, its code. */
export class TeamRefusal extends Error {
  constructor(message: string, readonly said: CodedLine | null) {
    super(message);
  }
}

/** What a thrown refusal says to the person. */
export const refusalSaid = (e: unknown): Said =>
  teamSaid(e instanceof TeamRefusal ? e.said : null, e instanceof Error ? e.message : String(e));

/** One /api/team* request; a refusal is thrown as a TeamRefusal: the
 *  backend's words by code where it gave one (team_client's TeamError), else
 *  as they came (the team server's own refusal); with none, the page's own,
 *  in the language in force. */
export async function teamCall<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload: unknown = null;
  try { payload = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const err = (payload as { error?: { message?: string; said?: CodedLine | null } } | null)?.error;
    throw new TeamRefusal(err?.message || t("left.team.requestFailed", { status: String(res.status) }),
                          err?.said ?? null);
  }
  return payload as T;
}

/** The team server's approval page, in a new tab. */
export function openApproval(url: string): void {
  window.open(url, "_blank", "noopener,noreferrer");
}

/** The part of team_client's snapshot (GET /api/team) signing in needs. */
interface TeamStart {
  state: Connection;
  last_server_url: string | null;
  pairing: { verification_uri_complete: string } | null;
}

/** Sign in to the team server at `serverUrl`: start the pairing and open
 *  its approval page. */
export async function connectTeam(serverUrl: string): Promise<void> {
  const started = await teamCall<TeamStart>("POST", "/api/team/connect", { server_url: serverUrl });
  if (started.pairing) openApproval(started.pairing.verification_uri_complete);
}

/** Sign in again with the address last used (`last_server_url`), unless a
 *  pairing is already under way or no address is saved; then there is
 *  nothing to start here, and the Team section is where to go on. */
export async function signInAgain(): Promise<void> {
  const status = await teamCall<TeamStart>("GET", "/api/team");
  if (status.state !== "pairing" && status.last_server_url) {
    await connectTeam(status.last_server_url);
  }
}
