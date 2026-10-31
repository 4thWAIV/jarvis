/**
 * The Settings view's Team section: connect JARVIS to a team server
 * (team_server/), see who you are there and whether JARVIS can reach it, and
 * — for an administrator — manage the team's members. Messages between
 * people are switched on here too: this JARVIS's own switch, and the team's,
 * which an administrator sets and every member sees.
 *
 * Everything goes through JARVIS's own backend (/api/team*, team_api.py),
 * which holds the session in the Keychain; this page only ever sees status.
 * Connecting opens the team server's approval page in a new tab, where you
 * sign in with GitHub and approve this Mac with the code shown here.
 *
 * The backend reads the server's status every 30 seconds whether or not this
 * view is open; while it is open, this view re-reads the backend's copy.
 */

import { useEffect, useState } from "react";
import { useLiveRead } from "./liveRead";
import { StatusDot, type Dot } from "./StatusDot";

interface Me { github_id: number; login: string; name?: string | null; role: "admin" | "member";
               state: string }
interface TeamInfo { name: string; member_count: number; member_limit: number }
interface TeamStatus {
  state: "disconnected" | "pairing" | "connecting" | "connected" | "stale" | "signed_out";
  server_url: string | null;
  last_server_url: string | null;
  team: TeamInfo | null;
  me: Me | null;
  last_contact_at: string | null;
  last_error: string | null;
  pairing: { user_code: string; verification_uri_complete: string; expires_at: string } | null;
}
interface Member { github_id: number; login: string; name?: string | null;
                   role: "admin" | "member"; state: "approved" | "active" | "suspended" }
interface Roster { revision: number; member_limit: number; members: Member[] }
interface LocalSwitch { enabled: boolean; forced: boolean }
interface TeamSwitch { enabled: boolean; changed_at: string | null }
interface Resolved { github_id: number; login: string; name?: string | null; html_url: string;
                     on_roster: boolean }

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
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
  return payload as T;
}

const readStatus = () => call<TeamStatus>("GET", "/api/team");

const STATE_TEXT: Record<TeamStatus["state"], [Dot, string]> = {
  disconnected: ["off", "Not connected"],
  pairing: ["yellow", "Waiting for you to approve this Mac on the team server"],
  connecting: ["yellow", "Connecting"],
  connected: ["green", "Connected"],
  stale: ["yellow", "Can't reach the team server"],
  signed_out: ["red", "Signed out — sign in again"],
};

function when(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "unknown" : d.toLocaleString();
}

function openApproval(url: string) {
  window.open(url, "_blank", "noopener,noreferrer");
}

export function TeamSection() {
  const live = useLiveRead(readStatus);
  const status = live.value;
  const refresh = live.refresh;
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  // Offer the last address again once a code expires or is cancelled, so
  // starting over does not mean typing it again.
  const lastUrl = status?.last_server_url ?? null;
  useEffect(() => {
    if (lastUrl) setUrl((typed) => typed || lastUrl);
  }, [lastUrl]);

  // Re-read the backend's status while this view is open: quickly while a
  // pairing is waiting, otherwise at the backend's own pace.
  useEffect(() => {
    const ms = status?.state === "pairing" ? 2000 : 10000;
    const t = setInterval(refresh, ms);
    return () => clearInterval(t);
  }, [status?.state, refresh]);

  const act = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setNote(null);
    try { await work(); } catch (e) { setNote((e as Error).message); }
    setBusy(false);
    refresh();
  };

  const connect = (serverUrl: string) => act(async () => {
    const s = await call<TeamStatus>("POST", "/api/team/connect", { server_url: serverUrl });
    if (s.pairing) openApproval(s.pairing.verification_uri_complete);
  });
  const disconnect = () => act(async () => {
    const r = await call<{ message: string }>("POST", "/api/team/disconnect");
    setNote(r.message);
  });

  if (!status) {
    return <p className="settings-hint read-failure">
      {live.error ? `The team connection could not be read (${live.error}); trying again.`
                  : "Reading the team connection…"}</p>;
  }
  const [dot, text] = STATE_TEXT[status.state];
  const connected = status.server_url !== null && status.state !== "pairing"
                    && status.state !== "disconnected";

  const signedIn = connected && status.state !== "signed_out";
  return (
    <div className="team">
      <MessagingSwitches admin={signedIn && status.me?.role === "admin"} signedIn={signedIn} />
      <div className="status-row">
        <StatusDot status={dot} /><span>{text}</span>
        {connected && <span className="status-detail">last contact {when(status.last_contact_at)}</span>}
      </div>
      {status.last_error && <p className="settings-hint">{status.last_error}</p>}
      {note && <p className="settings-hint">{note}</p>}

      {status.state === "disconnected" && (
        <div className="settings-field">
          <label htmlFor="team-url">Team server address</label>
          <div className="settings-input-row">
            <input id="team-url" type="text" placeholder="https://team.example.com" value={url}
                   onChange={(e) => setUrl(e.target.value)} />
            <button className="settings-btn primary" disabled={busy || !url.trim()}
                    onClick={() => connect(url.trim())}>Sign in with GitHub</button>
          </div>
          <p className="settings-hint">JARVIS keeps working without a team server; this only
            adds your team.</p>
        </div>
      )}

      {status.pairing && (
        <div className="team-pairing">
          <p>Approve this Mac on the team server. Check it shows this code:</p>
          <div className="team-code">{status.pairing.user_code}</div>
          <div className="settings-actions">
            <button className="settings-btn"
                    onClick={() => openApproval(status.pairing!.verification_uri_complete)}>
              Open the approval page again</button>
            <button className="settings-btn" disabled={busy}
                    onClick={() => act(() => call("POST", "/api/team/cancel"))}>Cancel</button>
          </div>
        </div>
      )}

      {connected && (
        <>
          <div className="sysinfo-grid">
            <div className="sysinfo-row"><span className="sysinfo-label">Server</span>
              <span>{status.server_url}</span></div>
            {status.team && <div className="sysinfo-row"><span className="sysinfo-label">Team</span>
              <span>{status.team.name} ({status.team.member_count} of {status.team.member_limit})</span></div>}
            {status.me && <div className="sysinfo-row"><span className="sysinfo-label">You</span>
              <span>{status.me.login} ({status.me.role === "admin" ? "administrator" : "member"})</span></div>}
          </div>
          <div className="settings-actions">
            {status.state === "signed_out" && status.server_url && (
              <button className="settings-btn primary" disabled={busy}
                      onClick={() => connect(status.server_url!)}>Sign in again</button>)}
            <button className="settings-btn" disabled={busy} onClick={disconnect}>Disconnect</button>
          </div>
          {status.me?.role === "admin" && status.state !== "signed_out" && (
            <MembersPanel me={status.me} />)}
        </>
      )}
    </div>
  );
}

/** The two switches messages between people need: this JARVIS's, and the
 *  team server's (set by an administrator; a member sees it). Each takes
 *  effect at once; switching either off deletes nothing. */
function MessagingSwitches({ admin, signedIn }: { admin: boolean; signedIn: boolean }) {
  const [local, setLocal] = useState<LocalSwitch | null>(null);
  const [team, setTeam] = useState<TeamSwitch | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const read = () => {
    call<LocalSwitch>("GET", "/api/team/messaging/local").then(setLocal,
      (e: Error) => setProblem(e.message));
    if (signedIn) {
      call<TeamSwitch>("GET", "/api/team/messaging/team").then(setTeam,
        (e: Error) => setProblem(e.message));
    } else {
      setTeam(null);
    }
  };
  useEffect(read, [signedIn]);

  const set = (url: string, enabled: boolean) => {
    setBusy(true);
    setProblem(null);
    call("PUT", url, { enabled })
      .catch((e: Error) => setProblem(e.message))
      .finally(() => { setBusy(false); read(); });
  };

  return (
    <div className="settings-field team-messaging">
      <label className="brain-default">
        <input type="checkbox" checked={local?.enabled ?? false}
               disabled={busy || !local || local.forced}
               onChange={(e) => set("/api/team/messaging/local", e.target.checked)} />
        Messages between people on this JARVIS
      </label>
      {local?.forced && <p className="settings-hint">Switched on by JARVIS_MESSAGING, so it
        cannot be switched off here.</p>}
      {signedIn && team && (admin ? (
        <label className="brain-default">
          <input type="checkbox" checked={team.enabled} disabled={busy}
                 onChange={(e) => set("/api/team/messaging/team", e.target.checked)} />
          Messages between people for the whole team
        </label>
      ) : (
        <p className="settings-hint">Messages between people are {team.enabled ? "on" : "off"} for
          the team{team.enabled ? "" : "; an administrator can switch them on"}.</p>
      ))}
      {problem && <p className="settings-hint">{problem}</p>}
    </div>
  );
}

/** The roster's groups, by membership state (team_server/teamserver/team.py):
 *  `approved` is someone added who has not signed in yet. */
const GROUPS: [Member["state"], string][] = [
  ["active", "Signed in"],
  ["approved", "Pending (not signed in yet)"],
  ["suspended", "Suspended"],
];

/** Where an outcome is said: beside the Add controls, or in one member's row. */
type Where = "add" | number;
interface Outcome { where: Where; refused: boolean; text: string }

/** One member change's outcome, said where it was asked for. A refusal says
 *  first what did not happen, then the team server's reason. */
function Said({ outcome }: { outcome: Outcome }) {
  return outcome.refused
    ? <p className="team-refused" role="alert">{outcome.text}</p>
    : <p className="team-done">{outcome.text}</p>;
}

function MembersPanel({ me }: { me: Me }) {
  const [roster, setRoster] = useState<Roster | null>(null);
  const [unread, setUnread] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [login, setLogin] = useState("");
  const [found, setFound] = useState<Resolved | null>(null);
  const [role, setRole] = useState<"member" | "admin">("member");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try { setRoster(await call<Roster>("GET", "/api/team/members")); setUnread(null); }
    catch (e) { setUnread((e as Error).message); }
  };
  useEffect(() => { load(); }, []);

  /** Make one change. `notDone` is what to say beside `where` if the team
   *  server refuses it; the work returns what to say once it is done, if
   *  anything. The roster is read again either way. */
  const run = async (where: Where, notDone: string, work: () => Promise<string | void>) => {
    setBusy(true);
    setOutcome(null);
    try {
      const done = await work();
      if (done) setOutcome({ where, refused: false, text: done });
    } catch (e) {
      setOutcome({ where, refused: true, text: `${notDone} ${(e as Error).message}` });
    }
    setBusy(false);
    await load();
  };
  const change = (m: Member, body: { role?: string; status?: string }, notDone: string) =>
    run(m.github_id, notDone, async () => {
      await call("PUT", `/api/team/members/${m.github_id}`,
                 { expected_revision: roster!.revision, ...body });
    });
  const endSessions = (m: Member) => run(m.github_id, `${m.login}'s sessions were not ended.`,
    async () => { await call("POST", `/api/team/members/${m.github_id}/revoke-sessions`); });
  const resolve = () => run("add", `${login.trim()} could not be looked up.`, async () => {
    setFound(null);
    setFound(await call<Resolved>("POST", "/api/team/members/resolve", { login: login.trim() }));
  });
  // A refused Add keeps the person and the role, so Add can be pressed again.
  const add = (who: Resolved) => run("add", `${who.login} was not added.`, async () => {
    await call("PUT", `/api/team/members/${who.github_id}`,
               { expected_revision: roster!.revision, role });
    setFound(null);
    setLogin("");
    setRole("member");
    return `${who.login} added; pending until they sign in.`;
  });
  const said = (where: Where) => outcome?.where === where
    && <Said outcome={outcome} />;
  const addRefused = outcome?.where === "add" && outcome.refused;

  const row = (m: Member) => (
    <li key={m.github_id} className="team-member">
      <span className="team-member-name">{m.login}{m.name ? ` — ${m.name}` : ""}</span>
      <span className="team-member-meta">{m.role === "admin" ? "administrator" : "member"}</span>
      <span className="team-member-actions">
        {m.role === "member"
          ? <button className="settings-btn" disabled={busy}
                    onClick={() => change(m, { role: "admin" },
                                          `${m.login} was not made an administrator.`)}>
              Make administrator</button>
          : <button className="settings-btn" disabled={busy}
                    onClick={() => change(m, { role: "member" },
                                          `${m.login} was not made a member.`)}>
              Make member</button>}
        {m.state === "suspended"
          ? <button className="settings-btn" disabled={busy}
                    onClick={() => change(m, { status: "approved" },
                                          `${m.login} was not re-enabled.`)}>Re-enable</button>
          : <button className="settings-btn" disabled={busy || m.github_id === me.github_id}
                    onClick={() => change(m, { status: "suspended" },
                                          `${m.login} was not suspended.`)}>Suspend</button>}
        {m.state === "approved" &&
          <button className="settings-btn" disabled={busy}
                  onClick={() => change(m, { status: "removed" },
                                        `${m.login} was not removed.`)}>Remove</button>}
        {m.state === "active" && m.github_id !== me.github_id &&
          <button className="settings-btn" disabled={busy}
                  onClick={() => endSessions(m)}>End sessions</button>}
      </span>
      {said(m.github_id)}
    </li>
  );

  return (
    <div className="team-members">
      <h4>Members</h4>
      {unread && <p className="settings-hint read-failure">
        The members could not be read ({unread}).</p>}
      {!roster ? (!unread && <p className="settings-hint">Reading the members…</p>) : <>
        {GROUPS.map(([state, title]) => {
          const members = roster.members.filter((m) => m.state === state);
          return members.length > 0 && (
            <section key={state} className={`team-member-group state-${state}`}>
              <h5>{title} ({members.length})</h5>
              <ul className="team-member-list">{members.map(row)}</ul>
            </section>
          );
        })}
        <p className="settings-hint">{roster.members.length} of {roster.member_limit} members.
          Suspending someone ends their sessions at once; re-enabling them does not bring the old
          sessions back.</p>
      </>}
      <div className="settings-field">
        <label htmlFor="team-add">Add someone by GitHub username</label>
        <div className="settings-input-row">
          <input id="team-add" type="text" placeholder="github-username" value={login}
                 onChange={(e) => { setLogin(e.target.value); setFound(null); setOutcome(null); }} />
          <button className="settings-btn" disabled={busy || !login.trim()} onClick={resolve}>
            Look up</button>
        </div>
      </div>
      {found && (
        <div className="team-confirm">
          {found.on_roster
            ? <p>{found.login} is already on the team.</p>
            : <>
                <p>Add <strong>{found.login}</strong>{found.name ? ` (${found.name})` : ""}, GitHub
                  account #{found.github_id}?{addRefused ? "" : " They can sign in once added."}</p>
                <div className="settings-input-row">
                  <select value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
                    <option value="member">Member</option>
                    <option value="admin">Administrator</option>
                  </select>
                  <button className="settings-btn primary" disabled={busy || !roster}
                          onClick={() => add(found)}>Add</button>
                  <button className="settings-btn"
                          onClick={() => { setFound(null); setOutcome(null); }}>Cancel</button>
                </div>
              </>}
        </div>
      )}
      {said("add")}
    </div>
  );
}
