"""Automated application acceptance against a deployed STAGING team server.

    MONGODB_URI=... MONGODB_DATABASE=..._staging ACCEPT_BASE_URL=https://... \\
    ACCEPT_EXPECT_COMMIT=<sha> [ACCEPT_RAILWAY_LINK_DIR=<dir linked to staging>] \\
    python acceptance.py

What it needs and why: a real GitHub sign-in cannot be automated, so this
seeds a SYNTHETIC team straight into the staging database (through the
service's own bootstrap and roster code, with GitHub IDs above 9,000,000,000
that no GitHub account has) and mints sessions for its members the way the
service does. Every check after that goes over HTTPS to the deployed
service. The synthetic team and everything it created are deleted at the
end, leaving the database awaiting the real first administrator.

Refuses to run against a database whose name does not end in `_staging`, or
when a real team already exists. Prints statuses and codes only; no
credential, token or connection string is printed.

Restore check (needs docker and mongodump): the staging database is dumped
(connection string passed on stdin), restored into a throwaway local MongoDB
container, and the service image is run against the restored copy to show
the team and its sessions survived, then that `revoke-all-sessions` ends
them. Nothing is ever restored to the shared cluster.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import timedelta
from pathlib import Path

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parent))

from teamserver import clock, db as dbmod, recover, sessions, team as team_mod  # noqa: E402
from teamserver.config import Config  # noqa: E402
from teamserver.github import Identity  # noqa: E402
from teamserver.secrets_util import csrf_token, digest, new_secret, pkce_challenge  # noqa: E402

SYNTH = 9_000_000_000
A1 = Identity(id=SYNTH + 1, login="acceptance-admin-1", name="Acceptance Admin 1")
A2 = Identity(id=SYNTH + 2, login="acceptance-admin-2", name="Acceptance Admin 2")
M = Identity(id=SYNTH + 3, login="acceptance-member", name="Acceptance Member")
REAL_LOOKUP = "octocat"  # a public GitHub account, for resolve/add from Railway
LOCAL_IMAGE = "ts-acceptance:local"

results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, evidence: str) -> bool:
    results.append((ok, name, evidence))
    print(f"{'PASS' if ok else 'FAIL'} {name}: {evidence}", flush=True)
    return ok


def code_of(r: httpx2.Response) -> str:
    try:
        return (r.json().get("error") or {}).get("code") or ""
    except Exception:
        return ""


class _Wait429(httpx2.HTTPTransport):
    """The service rate-limits administrative calls (60 a minute per
    address); this run makes more than that, so it waits as told."""

    def handle_request(self, request):
        while True:
            response = super().handle_request(request)
            if response.status_code != 429:
                return response
            response.read()
            time.sleep(int(response.headers.get("retry-after", "5")))


class _AsyncWait429(httpx2.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        while True:
            response = await super().handle_async_request(request)
            if response.status_code != 429:
                return response
            await response.aread()
            await asyncio.sleep(int(response.headers.get("retry-after", "5")))


class Harness:
    def __init__(self):
        self.base = os.environ["ACCEPT_BASE_URL"].rstrip("/")
        self.dbname = os.environ["MONGODB_DATABASE"]
        self.expect_commit = os.environ.get("ACCEPT_EXPECT_COMMIT", "")
        self.link_dir = os.environ.get("ACCEPT_RAILWAY_LINK_DIR", "")
        self.client = dbmod.connect(os.environ["MONGODB_URI"])
        self.db = dbmod.database(self.client, self.dbname)
        self.http = httpx2.Client(base_url=self.base, timeout=20, follow_redirects=False,
                                  transport=_Wait429())
        self.started = clock.now()
        self.tokens: dict[str, str] = {}

    # -- helpers -----------------------------------------------------------

    def bearer(self, who: str) -> dict:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def browser_post(self, token: str, path: str, json: dict, origin: str | None = None):
        name = "__Host-ts_session" if self.base.startswith("https://") else "ts_session"
        cookie = f"{name}={token}"
        return self.http.post(path, json=json, headers={
            "Cookie": cookie, "Origin": origin or self.base, "X-CSRF-Token": csrf_token(token)})

    async def mint(self, who: str, identity: Identity, kind: str = "device") -> None:
        team = await team_mod.get(self.db)
        member = team["members"][team_mod.key(identity.id)]
        token, _ = await sessions.issue(self.db, team, member, kind, auth_at=clock.now(),
                                        device_name="acceptance")
        self.tokens[who] = token

    def revision(self, who: str = "a1") -> int:
        return self.http.get("/api/v1/team/members", headers=self.bearer(who)).json()["revision"]

    def put(self, who: str, github_id: int, **body):
        body.setdefault("expected_revision", self.revision(who))
        return self.http.put(f"/api/v1/team/members/{github_id}", headers=self.bearer(who), json=body)

    # -- phases ---------------------------------------------------------------

    async def guard(self) -> None:
        if not self.dbname.endswith("_staging"):
            raise SystemExit("refused: acceptance runs only against a *_staging database")
        team = await team_mod.get(self.db)
        if team is not None and (team.get("bootstrap") or {}).get("admin_github_id") != A1.id:
            raise SystemExit("refused: a real team exists in this database")
        if team is not None:
            await self.cleanup()  # a previous run's leftovers

    def public_checks(self) -> None:
        v = self.http.get("/version").json()
        check("version matches deployed commit",
              not self.expect_commit or v["commit"] == self.expect_commit,
              f"commit={v['commit']} api={v['api_version']} capabilities={v['capabilities']}")
        check("capabilities omit messaging and presence",
              not any("messag" in c or "presence" in c for c in v["capabilities"]),
              ",".join(v["capabilities"]))
        self.server_id = v["server_id"]
        r = self.http.get("/health/ready")
        check("bootstrap readiness", r.status_code == 200 and r.json().get("reason") == "awaiting_bootstrap",
              f"{r.status_code} {r.json()}")
        r = self.http.get("/health/live")
        check("liveness", r.status_code == 200, str(r.status_code))
        r = self.http.get("/auth/github/start", params={"next": "/"})
        loc = r.headers.get("location", "")
        q = httpx2.URL(loc).params
        check("login start is PKCE S256, identity-only, fixed callback, browser-bound",
              r.status_code == 302 and loc.startswith("https://github.com/login/oauth/authorize")
              and q.get("code_challenge_method") == "S256" and "scope" not in q
              and q.get("redirect_uri") == self.base + "/auth/github/callback"
              and "ts_oauth_binding" in r.headers.get("set-cookie", "")
              and "secure" in r.headers.get("set-cookie", "").lower(),
              f"{r.status_code} redirect_uri ok={q.get('redirect_uri') == self.base + '/auth/github/callback'}")
        gh = httpx2.get(loc, timeout=20, follow_redirects=False)
        check("GitHub accepts the authorize request (client ID and callback)",
              gh.status_code in (200, 302) and "redirect_uri_mismatch" not in gh.headers.get("location", ""),
              f"github {gh.status_code}")
        r = self.http.get("/auth/github/callback", params={"code": "x", "state": "forged"})
        check("invalid OAuth state creates no session",
              r.status_code == 400 and "ts_session" not in r.headers.get("set-cookie", ""),
              str(r.status_code))
        statuses = {p: self.http.get(p).status_code for p in
                    ("/api/v1/me", "/api/v1/team", "/api/v1/team/members", "/api/v1/status")}
        statuses["bogus bearer"] = self.http.get("/api/v1/status",
                                                 headers={"Authorization": "Bearer " + new_secret()}).status_code
        check("unauthenticated requests rejected", set(statuses.values()) == {401}, str(statuses))
        hdr = self.http.get("/version").headers
        check("security headers", "frame-ancestors 'none'" in hdr.get("content-security-policy", "")
              and hdr.get("strict-transport-security") and hdr.get("cache-control") == "no-store",
              "csp, hsts, no-store present")

    async def seed(self) -> None:
        cfg = Config(port=0, public_base_url=self.base, mongodb_uri="", mongodb_database=self.dbname,
                     github_client_id="", github_client_secret="", team_name="Acceptance (synthetic)",
                     initial_admin_github_id=A1.id)
        team = await team_mod.bootstrap(self.db, cfg, self.server_id, A1)
        await team_mod.mutate(self.db, team_mod.change_member(A1.id, A2.id, A2, "admin", None))
        await team_mod.mutate(self.db, team_mod.change_member(A1.id, M.id, M, "member", None))
        for ident in (A2, M):  # their "first sign-in"
            await team_mod.admit(self.db, cfg, self.server_id, ident)
        team = await team_mod.get(self.db)
        await self.mint("a1_browser", A1, "browser")
        await self.mint("a1", A1)
        await self.mint("a2", A2)
        await self.mint("m", M)
        check("synthetic team seeded", len(team["members"]) == 3,
              f"revision={team['revision']} members=3 server_id matches={team['server_id'] == self.server_id}")

    async def pairing_checks(self) -> None:
        def start():
            verifier = new_secret()
            r = self.http.post("/api/v1/device-authorizations",
                               json={"device_name": "Acceptance Mac", "code_challenge": pkce_challenge(verifier)})
            return r.json(), verifier

        def exchange(s, verifier):
            return self.http.post("/api/v1/device-authorizations/exchange",
                                  json={"poll_token": s["poll_token"], "code_verifier": verifier})

        def approve(s, decision="approve", token=None, origin=None):
            return self.browser_post(token or self.tokens["a1_browser"],
                                     "/api/v1/device-authorizations/approve",
                                     {"user_code": s["user_code"], "decision": decision}, origin)

        s, v = start()
        r = exchange(s, v)
        check("pending pairing answers 202", r.status_code == 202, str(r.status_code))
        r = self.http.post("/api/v1/device-authorizations/approve",
                           json={"user_code": s["user_code"], "decision": "approve"},
                           headers=self.bearer("a1"))
        check("device session cannot approve devices", r.status_code == 403 and code_of(r) == "browser_required",
              f"{r.status_code} {code_of(r)}")
        r = self.http.post("/api/v1/device-authorizations/approve",
                           json={"user_code": s["user_code"], "decision": "approve"},
                           headers={"Cookie": ("__Host-ts_session=" if self.base.startswith("https://")
                                               else "ts_session=") + self.tokens["a1_browser"],
                                    "Origin": self.base})
        check("approval without CSRF token refused", r.status_code == 403 and code_of(r) == "csrf_failed",
              f"{r.status_code} {code_of(r)}")
        r = approve(s, origin="https://evil.example")
        check("approval from another origin refused", r.status_code == 403 and code_of(r) == "bad_origin",
              f"{r.status_code} {code_of(r)}")
        r = approve(s)
        check("explicit approval", r.status_code == 200 and r.json()["status"] == "approved", str(r.status_code))
        r = exchange(s, new_secret())
        r2 = exchange(s, v)
        check("wrong verifier creates no session and kills the pairing",
              r.status_code == 400 and r2.status_code == 400, f"{r.status_code} then {r2.status_code}")

        s, v = start()
        approve(s)
        r = exchange(s, v)
        ok = r.status_code == 200 and r.json()["server_id"] == self.server_id
        check("approved pairing issues a device session", ok, f"{r.status_code}")
        if ok:
            self.tokens["paired"] = r.json()["session_token"]
            st = self.http.get("/api/v1/status", headers=self.bearer("paired"))
            check("paired session reads status", st.status_code == 200 and st.json()["session_kind"] == "device",
                  str(st.status_code))
        r = exchange(s, v)
        check("replayed exchange refused", r.status_code == 400 and code_of(r) == "invalid_grant",
              f"{r.status_code} {code_of(r)}")

        s, v = start()
        approve(s, "deny")
        r = exchange(s, v)
        check("denied pairing", r.status_code == 403 and code_of(r) == "access_denied", str(r.status_code))
        r = self.http.post("/api/v1/device-authorizations/exchange",
                           json={"poll_token": new_secret(), "code_verifier": v})
        check("unknown polling credential refused", r.status_code == 400, str(r.status_code))

        s, v = start()
        approve(s)
        await self.db.device_authorizations.update_one(
            {"user_code": s["user_code"]}, {"$set": {"expires_at": clock.now() - timedelta(seconds=1)}})
        r = exchange(s, v)
        check("expired pairing refused (timestamp, not TTL)", r.status_code == 410, str(r.status_code))

    async def membership_checks(self) -> None:
        r = self.http.put(f"/api/v1/team/members/{SYNTH + 99}", headers=self.bearer("m"),
                          json={"expected_revision": 1})
        r2 = self.http.get("/api/v1/team/members", headers=self.bearer("m"))
        check("non-administrators cannot change or list membership",
              r.status_code == 403 and r2.status_code == 403, f"{r.status_code} {r2.status_code}")
        r = self.http.post("/api/v1/team/members/resolve", headers=self.bearer("a1"),
                           json={"login": REAL_LOOKUP})
        check("administrator resolves a GitHub username from Railway", r.status_code == 200,
              f"{r.status_code} id={r.json().get('github_id') if r.status_code == 200 else '-'}")
        if r.status_code == 200:
            gid = r.json()["github_id"]
            rev = self.revision()
            r = self.put("a1", gid, expected_revision=rev)
            check("add an approved member", r.status_code == 200 and r.json()["member"]["state"] == "approved",
                  str(r.status_code))
            r = self.put("a1", gid, expected_revision=rev)
            check("stale revision is 409", r.status_code == 409 and code_of(r) == "revision_conflict",
                  f"{r.status_code} {code_of(r)}")
            r = self.put("a1", gid, status="removed")
            check("remove before first sign-in", r.status_code == 200 and r.json()["member"] is None,
                  str(r.status_code))
        r = self.put("a1", M.id, status="suspended")
        r2 = self.http.get("/api/v1/status", headers=self.bearer("m"))
        check("suspension ends sessions at the next request", r.status_code == 200 and r2.status_code == 401,
              f"put {r.status_code}, member status {r2.status_code} {code_of(r2)}")
        self.put("a1", M.id, status="approved")
        r2 = self.http.get("/api/v1/status", headers=self.bearer("m"))
        check("re-enabling does not revive old sessions", r2.status_code == 401, str(r2.status_code))
        r = self.http.post(f"/api/v1/team/members/{A2.id}/revoke-sessions", headers=self.bearer("a1"))
        r2 = self.http.get("/api/v1/me", headers=self.bearer("a2"))
        check("administrator revokes a member's sessions", r.status_code == 200 and r2.status_code == 401,
              f"{r.status_code} then {r2.status_code}")
        await self.mint("a2", A2)
        await self.db.sessions.update_one({"_id": digest(self.tokens["a1"])},
                                          {"$set": {"auth_at": clock.now() - timedelta(minutes=20)}})
        r = self.http.post(f"/api/v1/team/members/{M.id}/revoke-sessions", headers=self.bearer("a1"))
        check("membership changes need no recent GitHub sign-in", r.status_code == 200,
              f"{r.status_code} {code_of(r)}")
        r = self.put("a1", A1.id, role="member")
        check("an administrator can step down while another remains", r.status_code == 200, str(r.status_code))
        r = self.put("a2", A2.id, role="member")
        check("the last active administrator cannot step down", r.status_code == 409 and code_of(r) == "last_admin",
              f"{r.status_code} {code_of(r)}")
        r = self.put("a2", A2.id, status="suspended")
        check("the last active administrator cannot be suspended", r.status_code == 409, str(r.status_code))
        r = self.put("a2", A1.id, role="admin")
        check("promote", r.status_code == 200 and r.json()["member"]["role"] == "admin", str(r.status_code))

    async def concurrency_checks(self) -> None:
        ok_rounds = 0
        rounds = 10
        seen: dict[str, int] = {}
        async with httpx2.AsyncClient(base_url=self.base, timeout=30,
                                      transport=_AsyncWait429()) as http:
            for _ in range(rounds):
                rev = self.revision()
                reqs = [http.put(f"/api/v1/team/members/{A2.id}", headers=self.bearer("a1"),
                                 json={"expected_revision": rev, "role": "member"}),
                        http.put(f"/api/v1/team/members/{A1.id}", headers=self.bearer("a2"),
                                 json={"expected_revision": rev, "role": "member"})]
                rs = await asyncio.gather(*reqs)
                codes = sorted(r.status_code for r in rs)
                team = await team_mod.get(self.db)
                admins = team_mod.active_admins(team["members"])
                seen[str(codes)] = seen.get(str(codes), 0) + 1
                # The loser fails on the stale revision (409) or, when the
                # winner's change landed first, on no longer being an
                # administrator (403).
                if codes in ([200, 403], [200, 409]) and len(admins) == 1:
                    ok_rounds += 1
                # restore two admins for the next round
                keeper = "a1" if A1.id in admins else "a2"
                other = A2.id if keeper == "a1" else A1.id
                self.put(keeper, other, role="admin")
            check("concurrent demotions on one revision: exactly one applies, one admin kept",
                  ok_rounds == rounds, f"{ok_rounds}/{rounds} rounds with one winner and 1 active admin; "
                                       f"status pairs {seen}")
            # Without a shared revision: each reads fresh and both race.
            bad = 0
            for _ in range(rounds):
                ra, rb = self.revision("a1"), self.revision("a2")
                rs = await asyncio.gather(
                    http.put(f"/api/v1/team/members/{A2.id}", headers=self.bearer("a1"),
                             json={"expected_revision": ra, "role": "member"}),
                    http.put(f"/api/v1/team/members/{A1.id}", headers=self.bearer("a2"),
                             json={"expected_revision": rb, "role": "member"}))
                team = await team_mod.get(self.db)
                admins = team_mod.active_admins(team["members"])
                if len(admins) < 1:
                    bad += 1
                keeper = "a1" if A1.id in admins else "a2"
                other = A2.id if keeper == "a1" else A1.id
                self.put(keeper, other, role="admin")
            check("racing administrator changes never leave zero administrators", bad == 0,
                  f"{rounds} rounds, rounds with no admin: {bad}")

    async def session_checks(self) -> None:
        await self.mint("expiring", M)
        await self.db.sessions.update_one({"_id": digest(self.tokens["expiring"])},
                                          {"$set": {"expires_at": clock.now() - timedelta(seconds=1)}})
        r = self.http.get("/api/v1/me", headers=self.bearer("expiring"))
        check("expired session fails even before TTL cleanup",
              r.status_code == 401 and code_of(r) == "session_expired", f"{r.status_code} {code_of(r)}")
        if "paired" in self.tokens:
            r = self.http.post("/api/v1/logout", headers=self.bearer("paired"))
            r2 = self.http.get("/api/v1/me", headers=self.bearer("paired"))
            check("logout revokes the session", r.status_code == 200 and r2.status_code == 401,
                  f"{r.status_code} then {r2.status_code}")

    async def restart_check(self) -> None:
        if not self.link_dir:
            check("restart persistence", False, "skipped: no ACCEPT_RAILWAY_LINK_DIR")
            return
        before = await team_mod.get(self.db)
        # stdin closed: given a terminal-less stdin, the CLI otherwise waits.
        p = subprocess.run(["railway", "restart", "-s", "team-server", "-y", "--json"],
                           cwd=self.link_dir, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            check("restart persistence", False, f"railway restart exit {p.returncode}")
            return
        # Wait for the restarted instance: readiness 200 again after a gap,
        # or 90 seconds of steady 200s if the restart was too quick to see.
        deadline = time.time() + 240
        saw_gap, ready, steady_since = False, False, None
        while time.time() < deadline:
            try:
                code = self.http.get("/health/ready").status_code
            except httpx2.HTTPError:
                code = None
            if code != 200:
                saw_gap, steady_since = True, None
            elif saw_gap:
                ready = True
                break
            else:
                steady_since = steady_since or time.time()
                if time.time() - steady_since > 90:
                    ready = True
                    break
            time.sleep(2)
        v = self.http.get("/version").json()
        after = await team_mod.get(self.db)
        st = self.http.get("/api/v1/status", headers=self.bearer("a1"))
        check("restart persistence: team, server ID and sessions survive",
              ready and v["server_id"] == self.server_id and after["revision"] == before["revision"]
              and after["members"].keys() == before["members"].keys() and st.status_code == 200,
              f"restart observed={saw_gap} ready={ready} server_id same={v['server_id'] == self.server_id} "
              f"revision {before['revision']}->{after['revision']} session {st.status_code}")
        r = self.http.get("/health/ready").json()
        check("restart did not re-run bootstrap", r == {"ready": True, "reason": None}, str(r))

    async def restore_check(self) -> None:
        if not shutil.which("docker") or not shutil.which("mongodump"):
            check("isolated restore", False, "skipped: docker or mongodump missing")
            return
        work = Path(tempfile.mkdtemp(prefix="ts-restore-"))
        name = "ts-restore-" + new_secret()[:8].lower().replace("_", "x").replace("-", "x")
        net = name + "-net"
        try:
            p = subprocess.run(["mongodump", "--config", "/dev/stdin", "--db", self.dbname,
                                "--out", str(work), "--quiet"],
                               input=f"uri: {os.environ['MONGODB_URI']}\n", capture_output=True,
                               text=True, timeout=300)
            if p.returncode != 0:
                check("isolated restore", False, f"mongodump exit {p.returncode}")
                return
            subprocess.run(["docker", "network", "create", net], capture_output=True, check=True)
            subprocess.run(["docker", "run", "-d", "--name", name + "-db", "--network", net,
                            "-v", f"{work}:/dump:ro", "mongo:latest"], capture_output=True, check=True)
            for _ in range(30):
                if subprocess.run(["docker", "exec", name + "-db", "mongosh", "--quiet", "--eval",
                                   "db.adminCommand('ping').ok"], capture_output=True).returncode == 0:
                    break
                time.sleep(1)
            p = subprocess.run(["docker", "exec", name + "-db", "mongorestore", "--quiet",
                                "--nsInclude", f"{self.dbname}.*", "/dump"], capture_output=True, timeout=300)
            if p.returncode != 0:
                check("isolated restore", False, f"mongorestore exit {p.returncode}")
                return
            port = "18181"
            subprocess.run(["docker", "run", "-d", "--name", name + "-svc", "--network", net,
                            "-p", f"127.0.0.1:{port}:8080",
                            "-e", "PORT=8080", "-e", f"PUBLIC_BASE_URL=http://127.0.0.1:{port}",
                            "-e", "TEAM_SERVER_DEV_INSECURE=1",
                            "-e", f"MONGODB_URI=mongodb://{name}-db:27017",
                            "-e", f"MONGODB_DATABASE={self.dbname}",
                            "-e", "GITHUB_CLIENT_ID=restore-check", "-e", "GITHUB_CLIENT_SECRET=restore-check",
                            LOCAL_IMAGE], capture_output=True, check=True)
            local = httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=10)
            ready = None
            for _ in range(40):
                try:
                    ready = local.get("/health/ready")
                    if ready.status_code == 200:
                        break
                except httpx2.HTTPError:
                    pass
                time.sleep(1)
            v = local.get("/version").json()
            team = local.get("/api/v1/team", headers=self.bearer("a1"))
            check("restored copy preserves team, bootstrap state and server ID",
                  ready is not None and ready.json() == {"ready": True, "reason": None}
                  and v["server_id"] == self.server_id and team.status_code == 200,
                  f"ready={ready.json() if ready is not None else None} server_id same="
                  f"{v['server_id'] == self.server_id} team read {team.status_code}")
            p = subprocess.run(["docker", "exec", "-e", f"MONGODB_URI=mongodb://{name}-db:27017",
                                "-e", f"MONGODB_DATABASE={self.dbname}", name + "-svc",
                                "python", "-m", "teamserver.recover", "revoke-all-sessions",
                                "--reason", "acceptance restore check"], capture_output=True, text=True)
            after = local.get("/api/v1/team", headers=self.bearer("a1"))
            check("recovery on the restored copy invalidates old sessions",
                  p.returncode == 0 and after.status_code == 401 and code_of(after) == "session_revoked",
                  f"recover exit {p.returncode}; old session then {after.status_code} {code_of(after)}")
            p = subprocess.run(["docker", "exec", "-e", f"MONGODB_URI=mongodb://{name}-db:27017",
                                "-e", f"MONGODB_DATABASE={self.dbname}", name + "-svc",
                                "python", "-m", "teamserver.recover", "recover-admin",
                                "--github-id", str(A1.id), "--confirm-login", A1.login,
                                "--reason", "acceptance"], capture_output=True, text=True)
            check("admin recovery refuses an ID GitHub does not verify", p.returncode == 1,
                  f"exit {p.returncode}")
            live = self.http.get("/api/v1/status", headers=self.bearer("a1"))
            check("restore check never touched staging", live.status_code == 200, f"staging session {live.status_code}")
        finally:
            subprocess.run(["docker", "rm", "-f", name + "-svc", name + "-db"], capture_output=True)
            subprocess.run(["docker", "network", "rm", net], capture_output=True)
            shutil.rmtree(work, ignore_errors=True)

    async def cleanup(self) -> None:
        ids = [A1.id, A2.id, M.id]
        await self.db.team.delete_one({"_id": dbmod.TEAM_ID,
                                       "bootstrap.admin_github_id": A1.id})
        await self.db.sessions.delete_many({"github_id": {"$in": ids}})
        await self.db.device_authorizations.delete_many({"device_name": "Acceptance Mac"})
        # No real team exists (guard), so every login transaction and audit
        # entry here is this run's.
        await self.db.oauth_states.delete_many({})
        await self.db.audit.delete_many({})

    async def run(self) -> int:
        await self.guard()
        try:
            self.public_checks()
            await self.seed()
            await self.pairing_checks()
            await self.membership_checks()
            await self.concurrency_checks()
            await self.session_checks()
            await self.restart_check()
            await self.restore_check()
        except Exception as e:
            check("harness ran to completion", False, f"{type(e).__name__} at "
                  f"{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        finally:
            await self.cleanup()
            left = await team_mod.get(self.db)
            r = self.http.get("/health/ready")
            check("cleanup: staging is back to awaiting bootstrap",
                  left is None and r.json().get("reason") == "awaiting_bootstrap", f"{r.status_code} {r.json()}")
            await self.client.close()
        failed = [n for ok, n, _ in results if not ok]
        print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed", flush=True)
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(Harness().run()))
