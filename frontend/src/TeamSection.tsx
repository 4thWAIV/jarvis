/**
 * The Settings view's Team section: connect JARVIS to a team server
 * (team_server/), see who you are there and whether JARVIS can reach it, and
 * — for an administrator — manage the team's members. Messages between
 * people have no switch: they work whenever this JARVIS is signed in.
 *
 * Everything goes through JARVIS's own backend (/api/team*, team_api.py),
 * which holds the session in the Keychain; this page only ever sees status.
 * Connecting opens the team server's approval page in a new tab, where you
 * sign in with GitHub and approve this Mac with the code shown here. The
 * requests and the sign-in are teamSignIn.ts's, the one path the reminder
 * at the top of the centre area (TeamSignInReminder.tsx) takes too.
 *
 * The backend reads the server's status every 30 seconds whether or not this
 * view is open; while it is open, this view re-reads the backend's copy.
 *
 * Its words are the catalog's (left.team.*), in the interface's language.
 * What the backend or the team server says in its own words (a refusal, the
 * last error) is shown as it came, marked as original detail; names, logins
 * and addresses are never translated.
 */

import { useEffect, useState } from "react";
import { useLiveRead } from "./liveRead";
import { formatDateTime, useT, type Translate } from "./locale";
import { StatusDot, type Dot } from "./StatusDot";
import { placed } from "./TeamServerTile";
import { connectTeam, openApproval, teamCall as call } from "./teamSignIn";

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
interface Resolved { github_id: number; login: string; name?: string | null; html_url: string;
                     on_roster: boolean }

const readStatus = () => call<TeamStatus>("GET", "/api/team");

const STATE_TEXT = {
  disconnected: ["off", "left.team.state.disconnected"],
  pairing: ["yellow", "left.team.state.pairing"],
  connecting: ["yellow", "left.team.state.connecting"],
  connected: ["green", "left.team.state.connected"],
  stale: ["yellow", "left.team.state.stale"],
  signed_out: ["red", "left.team.state.signedOut"],
} as const satisfies Record<TeamStatus["state"], readonly [Dot, string]>;

/** What a disconnect did on the team server (team_client.disconnect's
 *  `remote`), in words. */
const DISCONNECTED = {
  none: "left.team.disconnected.none",
  revoked: "left.team.disconnected.revoked",
  unreachable: "left.team.disconnected.unreachable",
} as const;

function when(t: Translate, iso: string | null): string {
  if (!iso) return t("left.team.never");
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? t("left.team.unknown")
    : formatDateTime(d, { dateStyle: "medium", timeStyle: "short" });
}

const roleWords = (t: Translate, role: "admin" | "member") =>
  t(role === "admin" ? "left.team.role.admin" : "left.team.role.member");

/** The backend's or the team server's own words, shown as they came. */
function Original({ text, className = "settings-hint" }: { text: string; className?: string }) {
  return <p className={className} data-original-detail="">{text}</p>;
}

export function TeamSection() {
  const t = useT();
  const live = useLiveRead(readStatus);
  const status = live.value;
  const refresh = live.refresh;
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  // What the last action said: the page's own words by key, or the
  // backend's as they came.
  const [note, setNote] = useState<{ remote: keyof typeof DISCONNECTED } | { original: string } | null>(null);

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
    try { await work(); } catch (e) { setNote({ original: (e as Error).message }); }
    setBusy(false);
    refresh();
  };

  const connect = (serverUrl: string) => act(() => connectTeam(serverUrl));
  const disconnect = () => act(async () => {
    const r = await call<{ remote: string; message: string }>("POST", "/api/team/disconnect");
    setNote(r.remote in DISCONNECTED ? { remote: r.remote as keyof typeof DISCONNECTED } : { original: r.message });
  });

  if (!status) {
    return <p className="settings-hint read-failure">
      {live.error ? t("left.team.unreadable", { error: live.error }) : t("left.team.reading")}</p>;
  }
  const [dot, stateKey] = STATE_TEXT[status.state];
  const connected = status.server_url !== null && status.state !== "pairing"
                    && status.state !== "disconnected";

  const signedIn = connected && status.state !== "signed_out";
  return (
    <div className="team">
      {!signedIn && <p className="settings-hint">{t("left.team.turnsOn")}</p>}
      <div className="status-row">
        <StatusDot status={dot} /><span>{t(stateKey)}</span>
        {connected && <span className="status-detail">
          {t("left.team.lastContact", { when: when(t, status.last_contact_at) })}</span>}
      </div>
      {status.last_error && <Original text={status.last_error} />}
      {note && ("remote" in note ? <p className="settings-hint">{t(DISCONNECTED[note.remote])}</p>
                                 : <Original text={note.original} />)}

      {status.state === "disconnected" && (
        <div className="settings-field">
          <label htmlFor="team-url">{t("left.team.address")}</label>
          <div className="settings-input-row">
            <input data-ui-action="input" id="team-url" type="text" placeholder={t("left.team.addressPlaceholder")}
                   value={url} onChange={(e) => setUrl(e.target.value)} />
            <button data-ui-action="sign-in" className="settings-btn primary" disabled={busy || !url.trim()}
                    onClick={() => connect(url.trim())}>{t("left.team.signInGithub")}</button>
          </div>
          <p className="settings-hint">{t("left.team.optional")}</p>
        </div>
      )}

      {status.pairing && (
        <div className="team-pairing">
          <p>{t("left.team.approve")}</p>
          <div className="team-code">{status.pairing.user_code}</div>
          <div className="settings-actions">
            <button data-ui-action="sign-in" className="settings-btn"
                    onClick={() => openApproval(status.pairing!.verification_uri_complete)}>
              {t("left.team.openApproval")}</button>
            <button data-ui-action="sign-in" className="settings-btn" disabled={busy}
                    onClick={() => act(() => call("POST", "/api/team/cancel"))}>{t("left.team.cancel")}</button>
          </div>
        </div>
      )}

      {connected && (
        <>
          <div className="sysinfo-grid">
            <div className="sysinfo-row"><span className="sysinfo-label">{t("left.common.server")}</span>
              <span>{status.server_url}</span></div>
            {status.team && <div className="sysinfo-row"><span className="sysinfo-label">{t("left.team.team")}</span>
              <span>{t("left.team.teamSize", { team: status.team.name, count: status.team.member_count,
                                               limit: status.team.member_limit })}</span></div>}
            {status.me && <div className="sysinfo-row"><span className="sysinfo-label">{t("left.team.you")}</span>
              <span>{t("left.team.youAre", { login: status.me.login, role: roleWords(t, status.me.role) })}</span></div>}
          </div>
          <div className="settings-actions">
            {status.state === "signed_out" && status.server_url && (
              <button data-ui-action="sign-in" className="settings-btn primary" disabled={busy}
                      onClick={() => connect(status.server_url!)}>{t("left.team.signInAgain")}</button>)}
            <button data-ui-action="sign-in" className="settings-btn" disabled={busy} onClick={disconnect}>
              {t("left.team.disconnect")}</button>
          </div>
          {status.me?.role === "admin" && status.state !== "signed_out" && (
            <MembersPanel me={status.me} />)}
        </>
      )}
    </div>
  );
}

/** The roster's groups, by membership state (team_server/teamserver/team.py):
 *  `approved` is someone added who has not signed in yet. */
const GROUPS = [
  ["active", "left.team.group.active"],
  ["approved", "left.team.group.approved"],
  ["suspended", "left.team.group.suspended"],
] as const satisfies readonly (readonly [Member["state"], string])[];

/** Where an outcome is said: beside the Add controls, or in one member's row. */
type Where = "add" | number;
/** `say` gives the page's own words in the language in force; `reason` is
 *  the team server's (or the backend's) as it came. */
interface Outcome { where: Where; refused: boolean; say: (t: Translate) => string; reason?: string }

/** One member change's outcome, said where it was asked for. A refusal says
 *  first what did not happen, then the team server's reason, as it came. */
function Said({ outcome }: { outcome: Outcome }) {
  const t = useT();
  return outcome.refused
    ? <p className="team-refused" role="alert">{outcome.say(t)}{outcome.reason
        ? <> <span data-original-detail="">{outcome.reason}</span></> : null}</p>
    : <p className="team-done">{outcome.say(t)}</p>;
}

function MembersPanel({ me }: { me: Me }) {
  const t = useT();
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

  /** Make one change. `notDone` says what did not happen, beside `where`,
   *  if the team server refuses it; the work returns what to say once it is
   *  done, if anything. The roster is read again either way. */
  type Say = (t: Translate) => string;
  const run = async (where: Where, notDone: Say, work: () => Promise<Say | void>) => {
    setBusy(true);
    setOutcome(null);
    try {
      const done = await work();
      if (done) setOutcome({ where, refused: false, say: done });
    } catch (e) {
      setOutcome({ where, refused: true, say: notDone, reason: (e as Error).message });
    }
    setBusy(false);
    await load();
  };
  const change = (m: Member, body: { role?: string; status?: string }, notDone: Say) =>
    run(m.github_id, notDone, async () => {
      await call("PUT", `/api/team/members/${m.github_id}`,
                 { expected_revision: roster!.revision, ...body });
    });
  const endSessions = (m: Member) => run(m.github_id, (tr) => tr("left.team.notEnded", { login: m.login }),
    async () => { await call("POST", `/api/team/members/${m.github_id}/revoke-sessions`); });
  const asked = login.trim();
  const resolve = () => run("add", (tr) => tr("left.team.notLookedUp", { login: asked }), async () => {
    setFound(null);
    setFound(await call<Resolved>("POST", "/api/team/members/resolve", { login: login.trim() }));
  });
  // A refused Add keeps the person and the role, so Add can be pressed again.
  const add = (who: Resolved) => run("add", (tr) => tr("left.team.notAdded", { login: who.login }), async () => {
    await call("PUT", `/api/team/members/${who.github_id}`,
               { expected_revision: roster!.revision, role });
    setFound(null);
    setLogin("");
    setRole("member");
    return (tr: Translate) => tr("left.team.added", { login: who.login });
  });
  const said = (where: Where) => outcome?.where === where
    && <Said outcome={outcome} />;
  const addRefused = outcome?.where === "add" && outcome.refused;

  const row = (m: Member) => (
    <li key={m.github_id} className="team-member">
      <span className="team-member-name">{m.login}{m.name ? ` — ${m.name}` : ""}</span>
      <span className="team-member-meta">{roleWords(t, m.role)}</span>
      <span className="team-member-actions">
        {m.role === "member"
          ? <button data-ui-action="domain" className="settings-btn" disabled={busy}
                    onClick={() => change(m, { role: "admin" },
                                          (tr) => tr("left.team.notMadeAdmin", { login: m.login }))}>
              {t("left.team.makeAdmin")}</button>
          : <button data-ui-action="domain" className="settings-btn" disabled={busy}
                    onClick={() => change(m, { role: "member" },
                                          (tr) => tr("left.team.notMadeMember", { login: m.login }))}>
              {t("left.team.makeMember")}</button>}
        {m.state === "suspended"
          ? <button data-ui-action="domain" className="settings-btn" disabled={busy}
                    onClick={() => change(m, { status: "approved" },
                                          (tr) => tr("left.team.notReenabled", { login: m.login }))}>
              {t("left.team.reenable")}</button>
          : <button data-ui-action="domain" className="settings-btn" disabled={busy || m.github_id === me.github_id}
                    onClick={() => change(m, { status: "suspended" },
                                          (tr) => tr("left.team.notSuspended", { login: m.login }))}>
              {t("left.team.suspend")}</button>}
        {m.state === "approved" &&
          <button data-ui-action="domain" className="settings-btn" disabled={busy}
                  onClick={() => change(m, { status: "removed" },
                                        (tr) => tr("left.team.notRemoved", { login: m.login }))}>
            {t("left.team.remove")}</button>}
        {m.state === "active" && m.github_id !== me.github_id &&
          <button data-ui-action="domain" className="settings-btn" disabled={busy}
                  onClick={() => endSessions(m)}>{t("left.team.endSessions")}</button>}
      </span>
      {said(m.github_id)}
    </li>
  );

  return (
    <div className="team-members">
      <h4>{t("left.team.members")}</h4>
      {unread && <p className="settings-hint read-failure">
        {t("left.team.membersUnreadable", { error: unread })}</p>}
      {!roster ? (!unread && <p className="settings-hint">{t("left.team.membersReading")}</p>) : <>
        {GROUPS.map(([state, title]) => {
          const members = roster.members.filter((m) => m.state === state);
          return members.length > 0 && (
            <section key={state} className={`team-member-group state-${state}`}>
              <h5>{t(title, { count: members.length })}</h5>
              <ul className="team-member-list">{members.map(row)}</ul>
            </section>
          );
        })}
        <p className="settings-hint">{t("left.team.rosterNote", { count: roster.members.length,
                                                                  limit: roster.member_limit })}</p>
      </>}
      <div className="settings-field">
        <label htmlFor="team-add">{t("left.team.addLabel")}</label>
        <div className="settings-input-row">
          <input data-ui-action="input" id="team-add" type="text" placeholder={t("left.team.addPlaceholder")} value={login}
                 onChange={(e) => { setLogin(e.target.value); setFound(null); setOutcome(null); }} />
          <button data-ui-action="domain" className="settings-btn" disabled={busy || !login.trim()} onClick={resolve}>
            {t("left.team.lookUp")}</button>
        </div>
      </div>
      {found && (
        <div className="team-confirm">
          {found.on_roster
            ? <p>{t("left.team.onTeam", { login: found.login })}</p>
            : <>
                <p>{placed((mark) => found.name
                     ? t("left.team.confirmAddNamed", { login: mark, name: found.name, id: String(found.github_id) })
                     : t("left.team.confirmAdd", { login: mark, id: String(found.github_id) }),
                   <strong>{found.login}</strong>)}
                  {addRefused ? null : <> {t("left.team.canSignIn")}</>}</p>
                <div className="settings-input-row">
                  <select data-ui-action="input" value={role} onChange={(e) => setRole(e.target.value as "member" | "admin")}>
                    <option value="member">{t("left.team.roleMember")}</option>
                    <option value="admin">{t("left.team.roleAdmin")}</option>
                  </select>
                  <button data-ui-action="domain" className="settings-btn primary" disabled={busy || !roster}
                          onClick={() => add(found)}>{t("left.team.add")}</button>
                  <button data-ui-action="input" className="settings-btn"
                          onClick={() => { setFound(null); setOutcome(null); }}>{t("left.team.cancel")}</button>
                </div>
              </>}
        </div>
      )}
      {said("add")}
    </div>
  );
}
