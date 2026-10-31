"""The team server's HTTP surface.

Public: GET /version, /health/live, /health/ready; the browser pages
(/, /device, /static/app.js); GET /auth/github/start and /callback.
Product API: /api/v1/... (see openapi.json); in staging only, the test
sign-in (`staging_sessions`), which is not in the contract. Pairing
start/exchange authenticate with their own transaction credentials;
everything else needs a product session — a Bearer token (a paired JARVIS)
or the browser's session cookie, whose mutations also need the server's
Origin and the session's CSRF token.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pymongo.errors import PyMongoError

from teamserver import (attachment_storage, attachments, build_info, clock, db as dbmod,
                        login, messaging, network, pages, pairing, sessions,
                        staging_sessions, team as team_mod)
from teamserver.config import Config, ConfigError, load
from teamserver.errors import Problem, forbidden, unauthenticated
from teamserver.github import GitHub, RealGitHub
from teamserver.messaging_models import MESSAGE_REQUEST_MAX_BYTES
from teamserver.models import (DeviceDecided, DeviceDecision, DeviceExchange,
                               DevicePending, DeviceSession, DeviceStart,
                               DeviceStarted, ErrorBody, Live, LoggedOut, Me,
                               Member, MemberChange, MemberChanged,
                               MessagingSwitch, MessagingSwitchChange,
                               NetworkSnapshot, NodeUpdate, Ready, Resolved,
                               ResolveRequest, Roster, ServerHealth,
                               SessionsRevoked, Status, Team, Version)
from teamserver.ratelimit import RateLimiter
from teamserver.secrets_util import csrf_token, normalize_user_code, same

log = logging.getLogger("teamserver")
BINDING_COOKIE = "ts_oauth_binding"
MAX_BODY = 16 * 1024
INIT_RETRY_SEC = 5
CSP = ("default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
       "img-src 'self'; connect-src 'self'; form-action 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")
ERRORS = {401: {"model": ErrorBody}, 403: {"model": ErrorBody},
          409: {"model": ErrorBody}, 429: {"model": ErrorBody},
          503: {"model": ErrorBody}}


# -- the body limit -------------------------------------------------------------

TOO_LARGE = {"error": {"code": "too_large", "message": "Request too large."}}


class _TooLarge(Exception):
    pass


def body_limit(method: str, path: str) -> Optional[int]:
    """The most bytes a request body may carry: the message send has its
    own larger limit, a file's byte upload (None) counts its own bytes,
    everything else MAX_BODY."""
    if attachments.is_upload(method, path):
        return None
    if method == "POST" and path == messaging.PREFIX:
        return MESSAGE_REQUEST_MAX_BYTES
    return MAX_BODY


class BodyLimit:
    """Refuses a body over `body_limit` with 413 whether it declares its
    length or not: a Content-Length over the limit (or not a number) before
    anything is read, and a chunked body as soon as the bytes read pass it,
    before the route has the whole of it in memory."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limit = (body_limit(scope["method"], scope["path"])
                 if scope["type"] == "http" else None)
        if limit is None:
            return await self.app(scope, receive, send)
        refuse = JSONResponse(TOO_LARGE, status_code=413)
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > limit):
            return await refuse(scope, receive, send)
        read, over, answered = 0, False, False

        async def counted():
            nonlocal read, over
            message = await receive()
            if message["type"] == "http.request":
                read += len(message.get("body", b""))
                if read > limit:
                    over = True
                    raise _TooLarge()
            return message

        async def guarded(message):
            nonlocal answered
            if over:
                return      # whatever the route made of the cut-off body
            answered = message["type"] == "http.response.start" or answered
            await send(message)

        try:
            await self.app(scope, counted, guarded)
        except Exception:
            if not over:
                raise
        if over and not answered:
            await refuse(scope, receive, send)


# -- views -------------------------------------------------------------------

def member_view(m: dict) -> Member:
    return Member(**{k: m.get(k) for k in Member.model_fields})


def me_view(m: dict) -> Me:
    return Me(**{k: m.get(k) for k in Me.model_fields})


def team_view(t: dict) -> Team:
    return Team(name=t["name"], server_id=t["server_id"],
                member_count=len(t["members"]),
                member_limit=team_mod.MEMBER_LIMIT)


def device_session_view(token: str, session: dict, team: dict,
                        member: dict) -> DeviceSession:
    return DeviceSession(status="approved", session_token=token,
                         session_expires_at=session["expires_at"],
                         server_id=team["server_id"], team=team_view(team),
                         me=me_view(member))


# -- the app -----------------------------------------------------------------

def create_app(config: Optional[Config] = None, github: Optional[GitHub] = None,
               limiter: Optional[RateLimiter] = None) -> FastAPI:
    problems: list[str] = []
    if config is None:
        try:
            config = load()
        except ConfigError as e:
            problems = e.problems

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        st.client = None
        st.db = None
        st.server_id = None
        st.initialized = False
        tasks = []
        if problems:
            log.error("configuration invalid: %s", "; ".join(problems))
        else:
            st.client = dbmod.connect(config.mongodb_uri)
            st.db = dbmod.database(st.client, config.mongodb_database)
            tasks = [asyncio.create_task(_init_until_done(app)),
                     asyncio.create_task(network.watch(st.health, st.db)),
                     asyncio.create_task(_start_attachments(app)),
                     asyncio.create_task(_watch_switch(app))]
        yield
        for task in tasks:
            task.cancel()
        if st.client is not None:
            await st.client.close()

    app = FastAPI(title="JARVIS team server", version=build_info.VERSION,
                  lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.config = config
    app.state.config_problems = problems
    app.state.github = github or (RealGitHub(config) if config else None)
    app.state.limiter = limiter or RateLimiter()
    app.state.health = network.HealthMonitor()
    cookie_name = "__Host-ts_session" if (config and config.secure) else "ts_session"

    # -- middleware ----------------------------------------------------------

    # Added first, so it runs inside guard_and_log and its 413 gets the headers.
    app.add_middleware(BodyLimit)

    @app.middleware("http")
    async def guard_and_log(request: Request, call_next):
        started = time.monotonic()
        response: Response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers.setdefault("Cache-Control", "no-store")
        if config and config.secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        # Path only: the query of /auth/github/callback carries the OAuth code.
        log.info("%s %s %s %.0fms", request.method, request.url.path,
                 response.status_code, (time.monotonic() - started) * 1000)
        return response

    def wants_html(request: Request) -> bool:
        p = request.url.path
        return not (p.startswith("/api/") or p.startswith("/health") or p == "/version")

    @app.exception_handler(Problem)
    async def on_problem(request: Request, exc: Problem):
        headers = {}
        if exc.status == 429 and "retry_after" in exc.extra:
            headers["Retry-After"] = str(exc.extra["retry_after"])
        if wants_html(request):
            return HTMLResponse(pages.message("Team server", exc.message),
                                status_code=exc.status, headers=headers)
        return JSONResponse(exc.body(), status_code=exc.status, headers=headers)

    @app.exception_handler(PyMongoError)
    async def on_database(request: Request, exc: PyMongoError):
        log.error("database error on %s: %s", request.url.path, type(exc).__name__)
        return await on_problem(request, Problem(
            503, "dependency_unavailable", "The team server cannot reach its database."))

    @app.exception_handler(network.DatabaseDown)
    async def on_network_database(request: Request, exc: network.DatabaseDown):
        return JSONResponse(request.app.state.health.report().model_dump(mode="json"),
                            status_code=503)

    @app.exception_handler(RequestValidationError)
    async def on_invalid(request: Request, exc: RequestValidationError):
        fields = sorted({".".join(str(p) for p in e.get("loc", ())[1:]) or "body"
                         for e in exc.errors()})
        return JSONResponse({"error": {"code": "invalid_request",
                                       "message": "Invalid request.",
                                       "fields": fields}}, status_code=422)

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, exc: Exception):
        log.error("unhandled error on %s: %s", request.url.path, type(exc).__name__)
        return JSONResponse({"error": {"code": "internal", "message": "Internal error."}},
                            status_code=500)

    # -- dependencies --------------------------------------------------------

    def database(request: Request):
        st = request.app.state
        if st.db is None or not st.initialized:
            raise Problem(503, "not_ready", "The team server is starting up.")
        return st.db

    async def principal(request: Request) -> sessions.Principal:
        db = database(request)
        auth = request.headers.get("authorization")
        if auth:
            scheme, _, token = auth.partition(" ")
            if scheme.lower() != "bearer":
                raise unauthenticated("session_missing", "Use a Bearer session token.")
            return await sessions.authenticate(db, token.strip())
        token = request.cookies.get(cookie_name)
        p = await sessions.authenticate(db, token)
        if request.method not in ("GET", "HEAD"):
            if request.headers.get("origin") != config.origin:
                raise forbidden("bad_origin", "This request did not come from the team server's page.")
            supplied = request.headers.get("x-csrf-token") or ""
            if not same(supplied, csrf_token(p.token)):
                raise forbidden("csrf_failed", "Reload the page and try again.")
        return p

    async def admin(request: Request,
                    p: sessions.Principal = Depends(principal)) -> sessions.Principal:
        if not p.is_admin:
            raise forbidden("admin_required", "Only administrators can do that.")
        request.app.state.limiter.hit("admin", request)
        return p

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(cookie_name, token, max_age=int(
            sessions.SESSION_LIFETIME.total_seconds()), path="/",
            secure=config.secure, httponly=True, samesite="lax")

    async def device(request: Request) -> sessions.Principal:
        """A paired JARVIS's session, for the network route: a database
        that is down or not yet initialized answers with the server's health
        (DatabaseDown), never with an error body."""
        try:
            with request.app.state.health.watching():
                p = await principal(request)
        except Problem as e:
            if e.code == "not_ready":
                raise network.DatabaseDown() from e
            raise
        if p.session["kind"] != "device":
            raise forbidden("device_required", "Only a paired JARVIS publishes a node.")
        return p

    async def optional_principal(request: Request) -> Optional[sessions.Principal]:
        try:
            return await principal(request)
        except Problem as e:
            if e.status in (401,):
                return None
            raise

    # -- public --------------------------------------------------------------

    @app.get("/version", response_model=Version)
    async def version(request: Request):
        return Version(service=build_info.SERVICE, version=build_info.VERSION,
                       commit=build_info.commit(), api_version=build_info.API_VERSION,
                       server_id=request.app.state.server_id,
                       capabilities=[*build_info.CAPABILITIES, network.CAPABILITY,
                                     *messaging.capabilities(messaging.switched_on(request))])

    @app.get("/health/live", response_model=Live)
    async def live():
        return Live(status="ok")

    @app.get("/health/ready", response_model=Ready, responses={503: {"model": Ready}})
    async def ready(request: Request):
        st = request.app.state
        def no(reason: str):
            return JSONResponse(Ready(ready=False, reason=reason).model_dump(),
                                status_code=503)
        if st.config_problems:
            return no("config_invalid")
        try:
            if not await st.health.connected(st.db):
                return no("database_unavailable")
            if not st.initialized:
                await asyncio.wait_for(_init_once(app), timeout=8)
            team = await asyncio.wait_for(team_mod.get(st.db), timeout=3)
        except (PyMongoError, asyncio.TimeoutError, OSError):
            return no("database_unavailable")
        if team is None:
            if not config.bootstrap_configured:
                return no("recovery_required")
            return Ready(ready=True, reason="awaiting_bootstrap")
        return Ready(ready=True)

    # -- browser pages and GitHub login ---------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def home(request: Request, welcome: Optional[str] = None):
        p = await optional_principal(request)
        if p is None:
            return HTMLResponse(pages.signed_out(None))
        boot = p.team.get("bootstrap") or {}
        just_created = bool(welcome) and boot.get("admin_github_id") == p.github_id
        return HTMLResponse(pages.signed_in(p.team["name"], p.member["login"],
                                            p.member["role"], csrf_token(p.token),
                                            just_created=just_created))

    @app.get("/static/app.js", include_in_schema=False)
    async def app_js():
        return Response(pages.APP_JS, media_type="application/javascript",
                        headers={"Cache-Control": "public, max-age=300"})

    @app.get("/auth/github/start", include_in_schema=False)
    async def github_start(request: Request, next: Optional[str] = None):
        request.app.state.limiter.hit("login", request)
        state, verifier, binding = await login.begin(database(request), next)
        url = request.app.state.github.authorize_url(state, verifier)
        response = RedirectResponse(url, status_code=302)
        response.set_cookie(BINDING_COOKIE, binding, max_age=int(login.LIFETIME.total_seconds()),
                            path="/auth/github", secure=config.secure,
                            httponly=True, samesite="lax")
        return response

    @app.get("/auth/github/callback", include_in_schema=False)
    async def github_callback(request: Request, code: Optional[str] = None,
                              state: Optional[str] = None,
                              error: Optional[str] = None):
        request.app.state.limiter.hit("login", request)
        db = database(request)
        verifier, next_path = await login.complete(
            db, state, request.cookies.get(BINDING_COOKIE))
        if error or not code or len(code) > 256:
            raise Problem(400, "github_login_cancelled", "The GitHub sign-in was cancelled.")
        identity = await request.app.state.github.identify(code, verifier)
        existed = await team_mod.get(db) is not None
        try:
            team, member = await team_mod.admit(db, config, request.app.state.server_id,
                                                identity)
        except Problem as e:
            if e.status == 403:
                log.info("login refused: %s", e.code)
            raise
        token, _ = await sessions.issue(db, team, member, "browser", auth_at=clock.now())
        target = next_path
        if not existed and target == "/":
            target = "/?welcome=1"
        response = RedirectResponse(target, status_code=303)
        set_session_cookie(response, token)
        response.delete_cookie(BINDING_COOKIE, path="/auth/github",
                               secure=config.secure, httponly=True, samesite="lax")
        return response

    @app.get("/device", response_class=HTMLResponse, include_in_schema=False)
    async def device_page(request: Request, code: Optional[str] = None):
        user_code = normalize_user_code(code or "")
        if not user_code:
            return HTMLResponse(pages.enter_code())
        p = await optional_principal(request)
        if p is None:
            return RedirectResponse(f"/auth/github/start?next=/device?code={user_code}",
                                    status_code=302)
        pending = await pairing.pending_by_code(database(request), user_code)
        if pending is None:
            return HTMLResponse(pages.message(
                "No such device", "No device is waiting with that code. It may have "
                "expired; start again from JARVIS."), status_code=404)
        return HTMLResponse(pages.confirm_device(user_code, pending["device_name"],
                                                 p.member["login"], csrf_token(p.token)))

    # -- product API -----------------------------------------------------------

    @app.get("/api/v1/me", response_model=Me, responses=ERRORS)
    async def get_me(p: sessions.Principal = Depends(principal)):
        return me_view(p.member)

    @app.get("/api/v1/team", response_model=Team, responses=ERRORS)
    async def get_team(p: sessions.Principal = Depends(principal)):
        return team_view(p.team)

    @app.get("/api/v1/status", response_model=Status, responses=ERRORS)
    async def get_status(request: Request, p: sessions.Principal = Depends(principal)):
        account_key = (await team_mod.network_account_key(database(request), p.team)
                       if p.session["kind"] == "device" else None)
        return Status(server_time=clock.now(), server_id=p.team["server_id"],
                      session_expires_at=p.session["expires_at"],
                      session_kind=p.session["kind"],
                      team=team_view(p.team), me=me_view(p.member),
                      network_account_key=account_key)

    @app.post("/api/v1/logout", response_model=LoggedOut, responses=ERRORS)
    async def logout(request: Request, p: sessions.Principal = Depends(principal)):
        await sessions.revoke(database(request), p.token)
        response = JSONResponse(LoggedOut(revoked=True).model_dump())
        response.delete_cookie(cookie_name, path="/", secure=config.secure,
                               httponly=True, samesite="lax")
        return response

    @app.get("/api/v1/team/members", response_model=Roster, responses=ERRORS)
    async def list_members(p: sessions.Principal = Depends(admin)):
        members = sorted(p.team["members"].values(),
                         key=lambda m: (m["role"] != "admin", m["login"].lower()))
        return Roster(revision=p.team["revision"], member_limit=team_mod.MEMBER_LIMIT,
                      members=[member_view(m) for m in members])

    @app.post("/api/v1/team/members/resolve", response_model=Resolved, responses=ERRORS)
    async def resolve_member(request: Request, body: ResolveRequest,
                             p: sessions.Principal = Depends(admin)):
        identity = await request.app.state.github.user_by_login(body.login)
        if identity is None:
            raise Problem(404, "github_user_not_found", "No GitHub user has that name.")
        return Resolved(github_id=identity.id, login=identity.login, name=identity.name,
                        avatar_url=identity.avatar_url,
                        html_url=f"https://github.com/{identity.login}",
                        on_roster=team_mod.key(identity.id) in p.team["members"])

    @app.put("/api/v1/team/members/{github_id}", response_model=MemberChanged,
             responses=ERRORS)
    async def change_member(request: Request, github_id: int, body: MemberChange,
                            p: sessions.Principal = Depends(admin)):
        if github_id <= 0:
            raise Problem(422, "invalid_request", "Invalid GitHub ID.")
        identity = None
        if team_mod.key(github_id) not in p.team["members"]:
            identity = await request.app.state.github.user_by_id(github_id)
        team = await team_mod.mutate(
            database(request),
            team_mod.change_member(p.github_id, github_id, identity, body.role, body.status),
            expected_revision=body.expected_revision)
        m = team["members"].get(team_mod.key(github_id))
        return MemberChanged(revision=team["revision"],
                             member=member_view(m) if m else None)

    @app.post("/api/v1/team/members/{github_id}/revoke-sessions",
              response_model=SessionsRevoked, responses=ERRORS)
    async def revoke_member_sessions(request: Request, github_id: int,
                                     p: sessions.Principal = Depends(admin)):
        team = await team_mod.mutate(database(request),
                                     team_mod.revoke_member_sessions(p.github_id, github_id))
        m = team["members"][team_mod.key(github_id)]
        return SessionsRevoked(revision=team["revision"],
                               session_generation=m["session_generation"])

    def switch_view(switch: messaging.Switch) -> MessagingSwitch:
        return MessagingSwitch(enabled=switch.enabled, changed_at=switch.changed_at,
                               changed_by_github_id=switch.changed_by_github_id)

    @app.get("/api/v1/team/messaging", response_model=MessagingSwitch, responses=ERRORS)
    async def get_messaging_switch(request: Request,
                                   p: sessions.Principal = Depends(principal)):
        return switch_view(await messaging.read_switch(database(request)))

    @app.put("/api/v1/team/messaging", response_model=MessagingSwitch, responses=ERRORS)
    async def set_messaging_switch(request: Request, body: MessagingSwitchChange,
                                   p: sessions.Principal = Depends(admin)):
        switch = await messaging.set_switch(database(request), p.github_id, body.enabled)
        request.app.state.messaging_switch = switch
        return switch_view(switch)

    @app.post("/api/v1/device-authorizations", response_model=DeviceStarted,
              responses=ERRORS)
    async def device_start(request: Request, body: DeviceStart):
        request.app.state.limiter.hit("pairing_start", request)
        doc, poll_token = await pairing.start(database(request), body.device_name,
                                              body.code_challenge)
        base = config.public_base_url
        return DeviceStarted(user_code=doc["user_code"],
                             verification_uri=base + "/device",
                             verification_uri_complete=f"{base}/device?code={doc['user_code']}",
                             poll_token=poll_token, expires_at=doc["expires_at"],
                             interval=pairing.POLL_INTERVAL_SEC)

    @app.post("/api/v1/device-authorizations/approve", response_model=DeviceDecided,
              responses=ERRORS)
    async def device_decide(request: Request, body: DeviceDecision,
                            p: sessions.Principal = Depends(principal)):
        request.app.state.limiter.hit("pairing_decide", request)
        if p.session["kind"] != "browser":
            raise forbidden("browser_required", "Approve devices from the team server page.")
        user_code = normalize_user_code(body.user_code)
        if not user_code:
            raise Problem(404, "pairing_not_found", "No pending pairing has that code.")
        doc = await pairing.decide(database(request), p, user_code,
                                   body.decision == "approve")
        return DeviceDecided(status=doc["status"], device_name=doc["device_name"])

    @app.post("/api/v1/device-authorizations/exchange", response_model=DeviceSession,
              responses={202: {"model": DevicePending}, 400: {"model": ErrorBody},
                         410: {"model": ErrorBody}, **ERRORS})
    async def device_exchange(request: Request, body: DeviceExchange):
        request.app.state.limiter.hit("pairing_exchange", request)
        result = await pairing.exchange(database(request), body.poll_token,
                                        body.code_verifier)
        if result["status"] == "authorization_pending":
            return JSONResponse(DevicePending(**result).model_dump(), status_code=202)
        return device_session_view(result["token"], result["session"],
                                   result["team"], result["member"])

    @app.post("/api/v1/network/nodes/self", response_model=NetworkSnapshot,
              responses={**ERRORS, 503: {"model": ServerHealth}})
    async def publish_node(request: Request, body: NodeUpdate,
                           p: sessions.Principal = Depends(device)):
        return await network.publish(database(request), p.member, p.team, body,
                                     request.app.state.health)

    def attachment_service(request: Request) -> attachments.AttachmentService:
        return messaging.started(request, "attachments")

    messaging.mount(app, device=device)
    app.include_router(attachments.router(device=device, service=attachment_service))

    if config and config.test_sessions_enabled:
        # Every method, and no body parameter: FastAPI would otherwise answer
        # 405 or 422 before the key is checked, telling a caller without the
        # key that the route exists. Without the key, everything is the 404.
        @app.api_route(staging_sessions.PATH, methods=staging_sessions.METHODS,
                       response_model=DeviceSession, include_in_schema=False)
        async def test_device_session(request: Request):
            staging_sessions.check_key(request)
            if request.method != "POST":
                raise staging_sessions.not_found()
            body = staging_sessions.parse(await request.body())
            token, session, team, member = await staging_sessions.issue(
                database(request), body.member, body.device_name)
            log.info("staging test session issued for %s", body.member)
            return device_session_view(token, session, team, member)

    return app


# -- initialization -----------------------------------------------------------

async def _init_once(app: FastAPI) -> None:
    st = app.state
    st.server_id = await dbmod.init(st.db)
    await team_mod.drain_audit(st.db)
    await messaging.seed_switch(st.db, st.config.messaging_enabled)
    st.messaging_switch = await messaging.read_switch(st.db)
    team = await team_mod.get(st.db)
    cfg = st.config
    if team is None and not cfg.bootstrap_configured:
        log.error("no team data and no bootstrap configured: recovery required "
                  "(restore the database or configure INITIAL_ADMIN_GITHUB_ID)")
    elif team is not None and cfg.initial_admin_github_id is not None:
        log.warning("INITIAL_ADMIN_GITHUB_ID is still set after bootstrap; it is "
                    "ignored and should be removed")
    st.initialized = True


def _file_settings(config: Optional[Config]) -> attachments.AttachmentSettings:
    return (config and config.attachment_settings) or attachments.DEFAULT_SETTINGS


async def _watch_switch(app: FastAPI) -> None:
    """The team's messaging switch, read again from the database every few
    seconds once it is initialized. Cancelled with the lifespan."""
    while not app.state.initialized:
        await asyncio.sleep(0.05)
    await messaging.watch_switch(app.state)


async def _start_attachments(app: FastAPI) -> None:
    """The messaging service, the file service and its recovery worker, built
    once the database is initialized, whether or not the team's switch is on
    (it is read on every request); until then their routes answer
    `not_ready`. Cancelled with the lifespan."""
    st = app.state
    while not st.initialized:
        await asyncio.sleep(0.05)
    s = _file_settings(st.config)
    st.attachment_storage = attachment_storage.GridFSAttachmentStorage(
        st.db, clock=clock.now, expiry_sec=s.staging_expiry_sec,
        quota_bytes=s.staging_quota_bytes)
    repository = messaging.MongoMessages(st.db)
    st.messaging = messaging.Messaging(repository, attachments=st.attachment_storage,
                                       package_max_bytes=s.package_max_bytes)
    st.attachments = attachments.AttachmentService(st.attachment_storage, repository,
                                                   settings=s)
    await attachments.watch(st.attachment_storage, repository,
                            worker_id=f"{socket.gethostname()}-{uuid.uuid4()}")


async def _init_until_done(app: FastAPI) -> None:
    while not app.state.initialized:
        try:
            await _init_once(app)
            log.info("database initialized")
        except PyMongoError as e:
            log.warning("database not ready: %s", type(e).__name__)
            await asyncio.sleep(INIT_RETRY_SEC)
