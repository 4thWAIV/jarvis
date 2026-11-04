"""Human messaging on this JARVIS, put together once: the store, the file
snapshots, the approval guard, the delivery loop, the transfer presentation
and the arrival announcer, and the page's routes over them.

Each part is its stream's module; this one only connects them, so each has
exactly one instance and one source for who this JARVIS is:

    store          messaging_store.SqliteMessagingStore (the JARVIS database)
    files          messaging_files.FileSnapshots, sending through the
                   current pairing's transport
    locations      attachment_locations.AttachmentLocations: where a
                   received file is, and its agent leases (cleanup keeps
                   what they hold)
    approvals      message_approvals.MessageApprovals, the one guard
    delivery       messaging_client.Delivery. Its transport for each pairing
                   is the team connection's, wrapped in GuardedTransport, so
                   nothing reaches the team server's send route that the
                   guard has not released.
    presentation   transfer_presentation.ServePresentation: the
                   `human_transfer` frames and the announcement lease. An
                   incoming transfer also wakes the announcer.
    announcer      arrival_announcer.ArrivalAnnouncer, through server.py's
                   notice path
    local          messaging_api.LocalMessaging, behind /api/team/messages
    bundle records bundle_status.OpenabilityStatus (the recipient's
                   openability, its receipts sent by delivery),
                   bundle_preflight.SenderPreflight (a draft's readiness,
                   stored with the revision reviewed) and
                   bundle_tasks.BundleTasks (task, companions, repair, the
                   review checklist and the files a reply cites, which
                   `local` proposes through it on every save), each bound
                   in its module
    bundles        bundle_inspection.BundleInspection over the received
                   files (the same instance as `received`), behind
                   bundle_api's routes and the brain's bundle tools
    human, tools   message_approvals.HumanMessaging and MessagingTools: the
                   capture path and the brain's draft and review tools

Who this JARVIS is comes from the paired team connection alone
(`Delivery.pairing`) and `data_paths.node_id()`. The routes are mounted
when the app is made (`mount`); the parts are built at startup, so the
store opens in the data directory in force then. There is no switch: the
loops run while this JARVIS is signed in to a team server that offers
messaging.v1, and stop when it is signed out (`_follow`, every
FOLLOW_EVERY_SEC); signed out, the routes still answer, saying so.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Optional
from uuid import UUID

from fastapi import FastAPI

from jarvis import attachment_locations, bundle_api, bundle_preflight, bundle_status, bundle_tasks
from jarvis import data_paths
from jarvis import draft_files
from jarvis import messaging_api
from jarvis import messaging_files
from jarvis import messaging_ports as ports
from jarvis.arrival_announcer import ArrivalAnnouncer, Wording, serve_labels
from jarvis.bundle_cache import DerivedCache
from jarvis.bundle_inspection import BundleInspection, Owner
from jarvis.message_approvals import (GuardedTransport, HumanMessaging, Identity,
                                      MessageApprovals, MessagingTools, Reviews)
from jarvis.messaging_client import Delivery, Pairing, TeamCalls, TeamTransport
from jarvis.messaging_store import SqliteMessagingStore
from jarvis.transfer_presentation import ServePresentation

log = logging.getLogger("jarvis.messaging_runtime")

# How often the announcer looks for arrivals still due when none woke it
# (another node's lease may have lapsed).
ANNOUNCE_POLL_SEC = 5.0
# How often the roster is read again, and abandoned files cleared.
ROSTER_EVERY_SEC = 300.0
CLEANUP_EVERY_SEC = 3600.0
HOUSEKEEPING_SEC = 60.0
# How often the team connection is looked at to start or stop the loops.
FOLLOW_EVERY_SEC = 5.0

Notify = Callable[[str, str, dict], Awaitable[None]]


class _CurrentTransport:
    """The current pairing's transport, looked up on every call, for the
    parts made once that outlive a pairing (presentation, labels)."""

    def __init__(self, now: Callable[[], ports.ServeTransport]):
        self._now = now

    def __getattr__(self, name: str):
        return getattr(self._now(), name)


class _Presenting:
    """The dispatcher delivery and approval present through: the
    presentation draws it, and an incoming one wakes the announcer."""

    def __init__(self, presentation: ServePresentation, arrived: Callable[[], None]):
        self._presentation, self._arrived = presentation, arrived

    def present(self, event: ports.TransferEvent) -> None:
        self._presentation.present(event)
        if event.direction == "incoming":
            self._arrived()


class MessagingRuntime:
    def __init__(self, *, connection: TeamCalls, captures: ports.ReviewCaptures,
                 send_frame: Callable[[dict], None], notify: Notify, wording: Wording,
                 network_raw: Callable[[], Optional[bytes]],
                 node_id: Callable[[], str] = data_paths.node_id,
                 clock: ports.Clock = ports.utcnow):
        self.connection, self.send_frame = connection, send_frame
        try:
            self.loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:                          # built outside the server (a script)
            self.loop = None
        self.node = UUID(str(node_id()))
        self.store = SqliteMessagingStore()
        self.store.forget_captures()
        self.files = messaging_files.FileSnapshots(
            self.store, self._transport_now, on_change=self.hint,
            held=lambda server_id, owner: self.locations.held(server_id, owner))
        self.locations = attachment_locations.AttachmentLocations(
            self.files, node_id=lambda: str(self.node))
        self.approvals = MessageApprovals(self.store, self.files, ids=ports.RandomIds(),
                                          clock=clock, bundles=lambda: self.delivery.bundles_offered())
        current = _CurrentTransport(self._transport_now)
        self.presentation = ServePresentation(transport=current, node_id=self.node,
                                              send=send_frame, clock=clock)
        self._arrived = asyncio.Event()
        presenting = _Presenting(self.presentation, self._arrived.set)
        cache = DerivedCache()
        self.received = BundleInspection(bundle_tasks.MessageFiles(self.files), cache,
                                         observer=bundle_status.observe)
        self.openability = bundle_status.OpenabilityStatus(
            self.store, members=bundle_status.members_of(self.received), clock=clock,
            on_change=lambda: self.soon(self.delivery.wake))
        self.delivery = Delivery(store=self.store, connection=connection, guard=self.approvals,
                                 snapshots=self.files, presentation=presenting,
                                 node_id=lambda: self.node, clock=clock,
                                 on_change=self.hint, transport_for=self._guarded,
                                 openability=self.openability)
        self.reviews = Reviews(self.approvals, captures, self.members)
        self.preflight = bundle_preflight.SenderPreflight(
            self.store, BundleInspection(bundle_preflight.DraftFiles(self.files, self.store),
                                         cache),
            network=lambda: _snapshot(network_raw), clock=clock)
        self.local = messaging_api.LocalMessaging(
            store=self.store, delivery=self.delivery, guard=self.approvals,
            reviews=self.reviews, snapshots=self.files, clock=clock, preflight=self.preflight)
        self.tasks = bundle_tasks.BundleTasks(
            self.store, self.local, self.files, drafts=self.preflight.inspection,
            messages=self.received, status=self.openability.status, on_loop=self.on_loop)
        self.local.propose_cited = self.tasks.propose_cited
        bundle_status.bind(self.openability, also=(self.tasks.observe,))
        bundle_preflight.bind(self.preflight)
        bundle_tasks.bind(self.tasks)
        self.human = HumanMessaging(
            approvals=self.approvals, identity=self.identity,
            availability=self.delivery.known_availability, recipients=self.members,
            released=lambda approved: self.delivery.wake(), presentation=presenting)
        self.tools = MessagingTools(
            self.human, self.reviews,
            lambda who, message_id, save, agent: self.local.write_draft(message_id, save,
                                                                        agent),
            files=draft_files.DraftFileAttacher(
                self.store, self.files, self.delivery.pairing,
                lambda message_id, save: self.local.write_draft(message_id, save, True)))
        self.announcer = ArrivalAnnouncer(
            store=self.store, presentation=self.presentation, scope=self._scope,
            labels=lambda: serve_labels(current, network_raw), notify=notify,
            wording=wording, clock=clock)
        self._tasks: list[asyncio.Task] = []
        self._follower: Optional[asyncio.Task] = None

    # -- who and through what ------------------------------------------------------------

    def _guarded(self, connection: TeamCalls, pairing: Pairing) -> GuardedTransport:
        return GuardedTransport(TeamTransport(connection, pairing), self.approvals,
                                pairing.server_id, pairing.owner_github_id)

    def _transport_now(self) -> ports.ServeTransport:
        pairing = self.delivery.pairing()
        if pairing is None:
            raise ports.TransportError("session_missing", status=401)
        return self.delivery.transport(pairing)

    def identity(self) -> Optional[Identity]:
        pairing = self.delivery.pairing()
        if pairing is None:
            return None
        return Identity(pairing.server_id, pairing.owner_github_id, self.node)

    def _scope(self) -> Optional[tuple[str, int, UUID]]:
        who = self.identity()
        if who is None or self.delivery.known_availability() != "available":
            return None
        return who.server_id, who.owner_github_id, who.node_id

    def members(self):
        return self.local.members()

    def files_context(self) -> Optional[messaging_files.FilesContext]:
        who = self.identity()
        if who is None:
            return None
        return messaging_files.FilesContext(self.files, who.server_id, who.owner_github_id)

    def soon(self, fn: Callable[[], None]) -> None:
        """Run `fn` on the event loop: at once when called on it, else
        handed to it (a bundle read or edit runs in a worker thread)."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self.loop is None or running is self.loop:
            fn()
        else:
            self.loop.call_soon_threadsafe(fn)

    def on_loop(self, fn):
        """`fn()`'s result, run on the event loop and waited for from a
        worker thread (a draft edit closes captures and sends hints, which
        belong to the loop); called on the loop, it just runs."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self.loop is None or running is self.loop:
            return fn()

        async def call():
            return fn()
        return asyncio.run_coroutine_threadsafe(call(), self.loop).result()

    def bundle_context(self) -> Optional[bundle_api.BundleContext]:
        who = self.identity()
        if who is None:
            return None
        return bundle_api.BundleContext(self.received, Owner(who.server_id, who.owner_github_id))

    def location_context(self) -> Optional[attachment_locations.LocationContext]:
        who = self.identity()
        if who is None:
            return None
        return attachment_locations.LocationContext(self.locations, who.server_id,
                                                    who.owner_github_id)

    def hint(self) -> None:
        """Tell every open page the queue changed (it reads it again)."""
        self.soon(lambda: self.send_frame({"type": ports.MESSAGES_HINT}))

    # -- the loops ------------------------------------------------------------------------

    async def _announce(self) -> None:
        while True:
            self._arrived.clear()
            try:
                await self.announcer.announce_due()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("messaging: announcing failed (%s)", type(e).__name__)
            try:
                await asyncio.wait_for(self._arrived.wait(), timeout=ANNOUNCE_POLL_SEC)
            except asyncio.TimeoutError:
                pass

    async def _housekeeping(self) -> None:
        roster_at = cleanup_at = float("-inf")
        while True:
            try:
                now = time.monotonic()
                who = self.identity()
                available = who is not None and \
                    await self.delivery.availability() == "available"
                if available and (now - roster_at >= ROSTER_EVERY_SEC or not self.members()):
                    await self.local.recipients()
                    roster_at = now
                if who is not None and now - cleanup_at >= CLEANUP_EVERY_SEC:
                    removed = await asyncio.to_thread(self.files.cleanup, *who.ns)
                    if removed:
                        log.info("messaging: %d abandoned file(s) removed", removed)
                    cleanup_at = now
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("messaging: housekeeping failed (%s)",
                            getattr(e, "code", type(e).__name__))
            await asyncio.sleep(HOUSEKEEPING_SEC)

    def replay_frames(self) -> list[dict]:
        return self.presentation.replay_frames()

    def running(self) -> bool:
        """Whether the loops (delivery, announcer, housekeeping) are running."""
        return bool(self._tasks)

    async def follow_once(self) -> None:
        """Start the loops once this JARVIS is signed in to a team server
        that offers messaging.v1; stop them once it is signed out. Every
        page reads the Queue again when they start or stop."""
        if self.delivery.pairing() is None:
            if self.running():
                await self._stop_loops()
                log.info("messaging: stopped (signed out of the team server)")
                self.hint()
        elif not self.running() and await self.delivery.availability() == "available":
            self._start_loops()
            log.info("messaging: started (signed in to the team server)")
            self.hint()

    async def _follow(self) -> None:
        while True:
            try:
                await self.follow_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("messaging: following the team connection failed (%s)",
                            type(e).__name__)
            await asyncio.sleep(FOLLOW_EVERY_SEC)

    def start(self) -> None:
        """Follow the team connection: the loops run while it is signed in
        (`follow_once`)."""
        if self._follower is None:
            self._follower = asyncio.create_task(self._follow())

    def _start_loops(self) -> None:
        if self._tasks:
            return
        self.delivery.start()
        self._tasks = [asyncio.create_task(self._announce()),
                       asyncio.create_task(self._housekeeping())]

    async def stop(self) -> None:
        follower, self._follower = self._follower, None
        if follower is not None:
            follower.cancel()
            try:
                await follower
            except (asyncio.CancelledError, Exception):
                pass
        await self._stop_loops()

    async def _stop_loops(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await self.delivery.stop()


def _snapshot(network_raw: Callable[[], Optional[bytes]]) -> Optional[dict]:
    """The stored team network snapshot as JSON, or None."""
    try:
        snapshot = json.loads(network_raw() or b"null")
    except ValueError:
        return None
    return snapshot if isinstance(snapshot, dict) else None


class _NotStarted:
    """The page's routes before the runtime is built: messaging is not
    available, and nothing else answers."""

    def __init__(self, runtime: Callable[[], Optional[MessagingRuntime]]):
        self._runtime = runtime

    def __getattr__(self, name: str):
        runtime = self._runtime()
        if runtime is None:
            raise messaging_api.Refused("messaging_unavailable", availability="not_started")
        return getattr(runtime.local, name)


def mount(app: FastAPI, runtime: Callable[[], Optional[MessagingRuntime]],
          show: Optional[bundle_api.Show] = None) -> None:
    """The page's messaging routes, and the files and bundle routes beside
    them, on the app as it is made; each request finds the runtime built at
    startup. `show` hands a bundle_view frame to every open page."""
    messaging_api.mount(app, _NotStarted(runtime))

    def context() -> Optional[messaging_files.FilesContext]:
        found = runtime()
        return found.files_context() if found is not None else None
    # Its routes themselves, as every other JARVIS route is registered (an
    # included router would be one opaque entry among the app's routes).
    app.router.routes.extend(messaging_files.router(context).routes)

    def bundles() -> Optional[bundle_api.BundleContext]:
        found = runtime()
        return found.bundle_context() if found is not None else None
    bundle_api.mount(app, bundles, show)

    def locations() -> Optional[attachment_locations.LocationContext]:
        found = runtime()
        return found.location_context() if found is not None else None
    app.router.routes.extend(attachment_locations.router(locations).routes)
