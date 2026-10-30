"""The local record of human messaging: drafts, approved envelopes and their
sends, the messages this owner has, their receipts, the owner's work items,
the sync position of this node, receipts and work commands still owed to
the team server, and which arrivals are due to be announced.

SQLite in the main database (`data_paths.db_path()`), every row namespaced
by the team server's `server_id` and the owner's GitHub id, so a JARVIS
paired again, to another server or as another person, never mixes or
releases one owner's work as another's. The tables are `human_*` (the
`messages` table is the sessions' one, run_store.py) and have their own
schema version (`human_messaging_schema`), migrated forward in order.

Each write the rules pair is one transaction: an approved envelope with its
send (`save_approved`), an accepted send with its message, receipts and work
item (`record_accepted`), a sync page with its continuation (`store_page`).
Mutable records keep the higher revision they have seen (receipts per
message, work state per thread); messages are immutable; nothing absent
from a scan is ever pruned. Records are kept as the contract's own JSON
(`model_dump_json`), with the columns the queries need beside them.

The store publishes nothing: whoever wrote announces the change after this
returns, so a hint never runs ahead of a commit.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator, Optional
from uuid import UUID

from jarvis import data_paths
from jarvis import messaging_ports as ports
from jarvis import messaging_wire as wire

SCHEMA_VERSION = 1

# Each step takes the schema from the version before it to its own. Only
# ever appended to; a step never drops a table that holds correspondence.
_MIGRATIONS: dict[int, str] = {
    1: """
        CREATE TABLE IF NOT EXISTS human_drafts (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, message_id TEXT NOT NULL,
            thread_id TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, message_id));
        CREATE INDEX IF NOT EXISTS human_drafts_thread
            ON human_drafts (server_id, owner, thread_id);
        CREATE TABLE IF NOT EXISTS human_sends (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, message_id TEXT NOT NULL,
            content_revision INTEGER NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, message_id));
        CREATE TABLE IF NOT EXISTS human_approved (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, message_id TEXT NOT NULL,
            content_revision INTEGER NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, message_id, content_revision));
        CREATE TABLE IF NOT EXISTS human_messages (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, message_id TEXT NOT NULL,
            thread_id TEXT NOT NULL, incoming INTEGER NOT NULL DEFAULT 0, body TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, message_id));
        CREATE INDEX IF NOT EXISTS human_messages_thread
            ON human_messages (server_id, owner, thread_id);
        CREATE TABLE IF NOT EXISTS human_receipts (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, message_id TEXT NOT NULL,
            revision INTEGER NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, message_id));
        CREATE TABLE IF NOT EXISTS human_work (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, thread_id TEXT NOT NULL,
            revision INTEGER NOT NULL, inactive INTEGER NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, thread_id));
        CREATE TABLE IF NOT EXISTS human_owed (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, message_id, kind));
        CREATE TABLE IF NOT EXISTS human_sync (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, node_id TEXT NOT NULL,
            continuation TEXT,
            PRIMARY KEY (server_id, owner, node_id));
        CREATE TABLE IF NOT EXISTS human_commands (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, thread_id TEXT NOT NULL,
            body TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, thread_id));
        CREATE TABLE IF NOT EXISTS human_announcements (
            server_id TEXT NOT NULL, owner INTEGER NOT NULL, message_id TEXT NOT NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (server_id, owner, message_id));
    """,
}

# Sends whose state the delivery loop owns from here on: an edit or a
# discard of their revision is refused until they settle.
_OUT = frozenset({"submitting", "accept_unknown", "accepted"})


def _sort_messages(messages: list[ports.LocalMessage]) -> list[ports.LocalMessage]:
    return sorted(messages, key=lambda lm: (lm.message.accepted_at, str(lm.message.message_id)))


class SqliteMessagingStore:
    """`LocalMessagingStore` over SQLite. Also: `set_capture` (the review's
    capture, which is not an edit), `transition_send` (a send's state by
    compare-and-set, for the delivery loop and the page racing on one send)
    and `submitting` (sends a crash left mid-attempt)."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else data_paths.db_path()
        self._migrate()

    # -- connection and schema ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction: committed when the block ends, rolled back if
        it raises."""
        with closing(self._connect()) as conn:
            with conn:
                yield conn

    def _migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS human_messaging_schema "
                         "(version INTEGER NOT NULL)")
            row = conn.execute("SELECT version FROM human_messaging_schema").fetchone()
            version = row["version"] if row else 0
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"human messaging schema {version} is newer than this "
                                   f"JARVIS ({SCHEMA_VERSION}); it is left as it is")
            for step in range(version + 1, SCHEMA_VERSION + 1):
                conn.executescript(_MIGRATIONS[step])
            if row is None:
                conn.execute("INSERT INTO human_messaging_schema (version) VALUES (?)",
                             (SCHEMA_VERSION,))
            else:
                conn.execute("UPDATE human_messaging_schema SET version = ?", (SCHEMA_VERSION,))

    def schema_version(self) -> int:
        with self._tx() as conn:
            return conn.execute("SELECT version FROM human_messaging_schema").fetchone()[0]

    # -- drafts -------------------------------------------------------------------------

    @staticmethod
    def _draft_row(conn, server_id, owner, message_id) -> Optional[ports.Draft]:
        row = conn.execute("SELECT body FROM human_drafts WHERE server_id=? AND owner=? "
                           "AND message_id=?", (server_id, owner, str(message_id))).fetchone()
        return None if row is None else ports.Draft.model_validate_json(row["body"])

    @staticmethod
    def _write_draft(conn, server_id, owner, draft: ports.Draft) -> None:
        conn.execute("INSERT OR REPLACE INTO human_drafts VALUES (?,?,?,?,?)",
                     (server_id, owner, str(draft.message_id), str(draft.thread_id),
                      draft.model_dump_json()))

    @staticmethod
    def _send_row(conn, server_id, owner, message_id) -> Optional[ports.LocalSend]:
        row = conn.execute("SELECT body FROM human_sends WHERE server_id=? AND owner=? "
                           "AND message_id=?", (server_id, owner, str(message_id))).fetchone()
        return None if row is None else ports.LocalSend.model_validate_json(row["body"])

    def _write_send(self, conn, server_id, owner, send: ports.LocalSend) -> None:
        """The send, and the draft's copy of it when the draft is on that
        revision."""
        conn.execute("INSERT OR REPLACE INTO human_sends VALUES (?,?,?,?,?,?)",
                     (server_id, owner, str(send.message_id), send.content_revision, send.state,
                      send.model_dump_json()))
        draft = self._draft_row(conn, server_id, owner, send.message_id)
        if draft is not None and draft.send.content_revision == send.content_revision:
            self._write_draft(conn, server_id, owner, draft.model_copy(update={"send": send}))

    def create_draft(self, server_id: str, owner_github_id: int,
                     draft: ports.Draft) -> ports.Draft:
        with self._tx() as conn:
            existing = self._draft_row(conn, server_id, owner_github_id, draft.message_id)
            if existing is not None:
                raise wire.ContractError("revision_conflict", latest=existing)
            self._write_draft(conn, server_id, owner_github_id, draft)
            self._write_send(conn, server_id, owner_github_id, draft.send)
        return draft

    def _current(self, conn, server_id, owner, message_id, expected) -> ports.Draft:
        current = self._draft_row(conn, server_id, owner, message_id)
        if current is None:
            raise wire.ContractError("draft_not_found")
        if current.draft_revision != expected:
            raise wire.ContractError("revision_conflict", latest=current)
        return current

    def save_draft(self, server_id: str, owner_github_id: int, draft: ports.Draft,
                   expected_draft_revision: int) -> ports.Draft:
        """The draft as edited, its revision moved on, and its send record:
        an edit after review replaces the send of the reviewed revision, so a
        revision approved but not yet sent can never go out after it. A send
        already out (or accepted) is never replaced by a stale edit."""
        with self._tx() as conn:
            self._current(conn, server_id, owner_github_id, draft.message_id,
                          expected_draft_revision)
            stored = self._send_row(conn, server_id, owner_github_id, draft.message_id)
            if stored is not None and stored.state in _OUT and stored != draft.send:
                raise ports.SendRefused(stored.state, "edit")
            saved = draft.model_copy(update={"draft_revision": expected_draft_revision + 1})
            self._write_draft(conn, server_id, owner_github_id, saved)
            self._write_send(conn, server_id, owner_github_id, saved.send)
        return self.draft(server_id, owner_github_id, draft.message_id)

    def set_capture(self, server_id: str, owner_github_id: int, message_id: UUID,
                    capture_id: Optional[int]) -> ports.Draft:
        """Which capture the review opened (or None once it closed). Not an
        edit: the draft revision stays."""
        with self._tx() as conn:
            draft = self._draft_row(conn, server_id, owner_github_id, message_id)
            if draft is None:
                raise wire.ContractError("draft_not_found")
            draft = draft.model_copy(update={"capture_id": capture_id})
            self._write_draft(conn, server_id, owner_github_id, draft)
        return draft

    def draft(self, server_id: str, owner_github_id: int,
              message_id: UUID) -> Optional[ports.Draft]:
        with self._tx() as conn:
            return self._draft_row(conn, server_id, owner_github_id, message_id)

    def discard_draft(self, server_id: str, owner_github_id: int, message_id: UUID,
                      expected_draft_revision: int) -> None:
        with self._tx() as conn:
            current = self._current(conn, server_id, owner_github_id, message_id,
                                    expected_draft_revision)
            stored = self._send_row(conn, server_id, owner_github_id, message_id)
            if stored is not None and stored.state in _OUT | {"approved"}:
                raise ports.SendRefused(stored.state, "cancel")
            conn.execute("DELETE FROM human_drafts WHERE server_id=? AND owner=? AND "
                         "message_id=?", (server_id, owner_github_id, str(current.message_id)))
            conn.execute("DELETE FROM human_sends WHERE server_id=? AND owner=? AND "
                         "message_id=?", (server_id, owner_github_id, str(current.message_id)))

    # -- approval and sending ----------------------------------------------------------------

    def save_approved(self, approved: ports.ApprovedSend) -> ports.LocalSend:
        req = approved.request
        send = ports.LocalSend(message_id=req.message_id, content_revision=req.content_revision,
                               state="approved", content_digest=req.content_digest)
        with self._tx() as conn:
            conn.execute("INSERT OR REPLACE INTO human_approved VALUES (?,?,?,?,?)",
                         (approved.server_id, approved.owner_github_id, str(req.message_id),
                          req.content_revision, approved.model_dump_json()))
            self._write_send(conn, approved.server_id, approved.owner_github_id, send)
        return send

    def approved(self, server_id: str, owner_github_id: int, message_id: UUID,
                 content_revision: int) -> Optional[ports.ApprovedSend]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT body FROM human_approved WHERE server_id=? AND owner=? AND "
                "message_id=? AND content_revision=?",
                (server_id, owner_github_id, str(message_id), content_revision)).fetchone()
        return None if row is None else ports.ApprovedSend.model_validate_json(row["body"])

    def set_send(self, server_id: str, owner_github_id: int, send: ports.LocalSend) -> None:
        with self._tx() as conn:
            self._write_send(conn, server_id, owner_github_id, send)

    def send(self, server_id: str, owner_github_id: int,
             message_id: UUID) -> Optional[ports.LocalSend]:
        with self._tx() as conn:
            return self._send_row(conn, server_id, owner_github_id, message_id)

    def transition_send(self, server_id: str, owner_github_id: int, expected: ports.LocalSend,
                        new: ports.LocalSend) -> bool:
        """Write `new` only if the stored send is still exactly `expected`."""
        with self._tx() as conn:
            if self._send_row(conn, server_id, owner_github_id, expected.message_id) != expected:
                return False
            self._write_send(conn, server_id, owner_github_id, new)
        return True

    def _sends(self, server_id, owner, states) -> list[ports.LocalSend]:
        with self._tx() as conn:
            rows = conn.execute(
                f"SELECT body FROM human_sends WHERE server_id=? AND owner=? AND state IN "
                f"({','.join('?' * len(states))}) ORDER BY message_id",
                (server_id, owner, *sorted(states))).fetchall()
        return [ports.LocalSend.model_validate_json(r["body"]) for r in rows]

    def unresolved_sends(self, server_id: str, owner_github_id: int) -> list[ports.LocalSend]:
        return self._sends(server_id, owner_github_id, ports.SEND_UNRESOLVED)

    def submitting(self, server_id: str, owner_github_id: int) -> list[ports.LocalSend]:
        return self._sends(server_id, owner_github_id, {"submitting"})

    def _keep(self, conn, server_id, owner, m: wire.AcceptedMessage,
              receipts: wire.ReceiptFacts, work: wire.WorkState) -> None:
        conn.execute("INSERT OR IGNORE INTO human_messages VALUES (?,?,?,?,0,?)",
                     (server_id, owner, str(m.message_id), str(m.thread_id), m.model_dump_json()))
        self._keep_receipts(conn, server_id, owner, receipts)
        self._keep_work(conn, server_id, owner, work)

    @staticmethod
    def _keep_receipts(conn, server_id, owner, facts: wire.ReceiptFacts) -> None:
        conn.execute(
            "INSERT INTO human_receipts VALUES (?,?,?,?,?) ON CONFLICT "
            "(server_id, owner, message_id) DO UPDATE SET revision=excluded.revision, "
            "body=excluded.body WHERE excluded.revision > human_receipts.revision",
            (server_id, owner, str(facts.message_id), facts.revision, facts.model_dump_json()))

    @staticmethod
    def _keep_work(conn, server_id, owner, state: wire.WorkState) -> None:
        conn.execute(
            "INSERT INTO human_work VALUES (?,?,?,?,?,?) ON CONFLICT "
            "(server_id, owner, thread_id) DO UPDATE SET revision=excluded.revision, "
            "inactive=excluded.inactive, body=excluded.body "
            "WHERE excluded.revision > human_work.revision",
            (server_id, owner, str(state.thread_id), state.revision,
             int(state.placement == "inactive"), state.model_dump_json()))

    def _first_incoming(self, conn, server_id, owner, message_id) -> bool:
        changed = conn.execute(
            "UPDATE human_messages SET incoming=1 WHERE server_id=? AND owner=? AND "
            "message_id=? AND incoming=0", (server_id, owner, str(message_id))).rowcount
        if not changed:
            return False
        conn.execute("INSERT OR IGNORE INTO human_owed VALUES (?,?,?,?)",
                     (server_id, owner, str(message_id), "received"))
        conn.execute("INSERT OR REPLACE INTO human_announcements VALUES (?,?,?,?)",
                     (server_id, owner, str(message_id), "due"))
        return True

    def record_accepted(self, server_id: str, owner_github_id: int,
                        result: wire.SendResult) -> bool:
        m = result.message
        with self._tx() as conn:
            self._keep(conn, server_id, owner_github_id, m, result.receipts, result.work_state)
            sent = self._send_row(conn, server_id, owner_github_id, m.message_id)
            if sent is not None:
                self._write_send(conn, server_id, owner_github_id, sent.model_copy(update={
                    "state": "accepted", "failure": None, "next_attempt_at": None}))
            return m.recipient_github_id == owner_github_id and \
                self._first_incoming(conn, server_id, owner_github_id, m.message_id)

    # -- incoming and sync ---------------------------------------------------------------------

    def store_page(self, server_id: str, owner_github_id: int, node_id: UUID,
                   page: wire.SyncPage) -> list[UUID]:
        return self.store_page_changes(server_id, owner_github_id, node_id, page)[0]

    def store_page_changes(self, server_id: str, owner_github_id: int, node_id: UUID,
                           page: wire.SyncPage) -> tuple[list[UUID], bool]:
        """`store_page`, and whether any record changed (a rescan of what is
        already here changes nothing, and so publishes no hint)."""
        new: list[UUID] = []
        with self._tx() as conn:
            before = conn.total_changes
            for e in page.entries:
                self._keep(conn, server_id, owner_github_id, e.message, e.receipts, e.work_state)
                if e.message.recipient_github_id == owner_github_id and self._first_incoming(
                        conn, server_id, owner_github_id, e.message.message_id):
                    new.append(e.message.message_id)
            changed = conn.total_changes != before
            conn.execute("INSERT OR REPLACE INTO human_sync VALUES (?,?,?,?)",
                         (server_id, owner_github_id, str(node_id), page.continuation))
        return new, changed

    def sync_position(self, server_id: str, owner_github_id: int,
                      node_id: UUID) -> Optional[str]:
        with self._tx() as conn:
            row = conn.execute("SELECT continuation FROM human_sync WHERE server_id=? AND "
                               "owner=? AND node_id=?",
                               (server_id, owner_github_id, str(node_id))).fetchone()
        return None if row is None else row["continuation"]

    def restart_sync(self, server_id: str, owner_github_id: int, node_id: UUID) -> None:
        """Forget this node's continuation (`sync_restart`): the next page
        starts a new scan. Nothing stored is pruned."""
        with self._tx() as conn:
            conn.execute("DELETE FROM human_sync WHERE server_id=? AND owner=? AND node_id=?",
                         (server_id, owner_github_id, str(node_id)))

    def owe_receipt(self, server_id: str, owner_github_id: int, message_id: UUID,
                    kind: wire.ReceiptKind) -> None:
        with self._tx() as conn:
            conn.execute("INSERT OR IGNORE INTO human_owed VALUES (?,?,?,?)",
                         (server_id, owner_github_id, str(message_id), kind))

    def receipts_owed(self, server_id: str, owner_github_id: int) -> list[ports.OwedReceipt]:
        with self._tx() as conn:
            rows = conn.execute("SELECT message_id, kind FROM human_owed WHERE server_id=? AND "
                                "owner=? ORDER BY rowid", (server_id, owner_github_id)).fetchall()
        return [ports.OwedReceipt(message_id=UUID(r["message_id"]), kind=r["kind"]) for r in rows]

    def settle_receipts(self, server_id: str, owner_github_id: int,
                        sent: list[ports.OwedReceipt], result: wire.ReceiptBatchResult) -> None:
        with self._tx() as conn:
            for facts in result.facts:
                self._keep_receipts(conn, server_id, owner_github_id, facts)
            for owed in sent:
                conn.execute("DELETE FROM human_owed WHERE server_id=? AND owner=? AND "
                             "message_id=? AND kind=?",
                             (server_id, owner_github_id, str(owed.message_id), owed.kind))

    # -- work state ----------------------------------------------------------------------------------

    def _work_row(self, conn, server_id, owner, thread_id) -> Optional[wire.WorkState]:
        row = conn.execute("SELECT body FROM human_work WHERE server_id=? AND owner=? AND "
                           "thread_id=?", (server_id, owner, str(thread_id))).fetchone()
        return None if row is None else wire.WorkState.model_validate_json(row["body"])

    def save_work_state(self, server_id: str, owner_github_id: int,
                        state: wire.WorkState) -> wire.WorkState:
        with self._tx() as conn:
            self._keep_work(conn, server_id, owner_github_id, state)
            return self._work_row(conn, server_id, owner_github_id, state.thread_id)

    def work_state(self, server_id: str, owner_github_id: int,
                   thread_id: UUID) -> Optional[wire.WorkState]:
        with self._tx() as conn:
            return self._work_row(conn, server_id, owner_github_id, thread_id)

    def queue_command(self, server_id: str, owner_github_id: int, thread_id: UUID,
                      request: wire.WorkCommandRequest) -> None:
        with self._tx() as conn:
            conn.execute("INSERT OR REPLACE INTO human_commands VALUES (?,?,?,?)",
                         (server_id, owner_github_id, str(thread_id), request.model_dump_json()))

    def pending_commands(self, server_id: str, owner_github_id: int
                         ) -> list[tuple[UUID, wire.WorkCommandRequest]]:
        with self._tx() as conn:
            rows = conn.execute("SELECT thread_id, body FROM human_commands WHERE server_id=? "
                                "AND owner=? ORDER BY rowid",
                                (server_id, owner_github_id)).fetchall()
        return [(UUID(r["thread_id"]), wire.WorkCommandRequest.model_validate_json(r["body"]))
                for r in rows]

    def settle_command(self, server_id: str, owner_github_id: int, thread_id: UUID) -> None:
        with self._tx() as conn:
            conn.execute("DELETE FROM human_commands WHERE server_id=? AND owner=? AND "
                         "thread_id=?", (server_id, owner_github_id, str(thread_id)))

    # -- announcements ---------------------------------------------------------------------------------

    def announcements_due(self, server_id: str, owner_github_id: int) -> list[UUID]:
        with self._tx() as conn:
            rows = conn.execute("SELECT message_id FROM human_announcements WHERE server_id=? "
                                "AND owner=? AND state='due' ORDER BY rowid",
                                (server_id, owner_github_id)).fetchall()
        return [UUID(r["message_id"]) for r in rows]

    def set_announcement(self, server_id: str, owner_github_id: int, message_id: UUID,
                         state: ports.AnnouncementLocal) -> None:
        with self._tx() as conn:
            conn.execute("INSERT OR REPLACE INTO human_announcements VALUES (?,?,?,?)",
                         (server_id, owner_github_id, str(message_id), state))

    # -- reads ---------------------------------------------------------------------------------------------

    @staticmethod
    def _local_message(conn, server_id, owner, row) -> ports.LocalMessage:
        m = wire.AcceptedMessage.model_validate_json(row["body"])
        facts = conn.execute("SELECT body FROM human_receipts WHERE server_id=? AND owner=? AND "
                             "message_id=?", (server_id, owner, row["message_id"])).fetchone()
        outgoing = m.sender_github_id == owner
        return ports.LocalMessage(
            message=m, direction="outgoing" if outgoing else "incoming",
            self_message=outgoing and m.recipient_github_id == owner,
            receipts=wire.ReceiptFacts.model_validate_json(facts["body"]))

    def message(self, server_id: str, owner_github_id: int,
                message_id: UUID) -> Optional[ports.LocalMessage]:
        with self._tx() as conn:
            row = conn.execute("SELECT message_id, body FROM human_messages WHERE server_id=? "
                               "AND owner=? AND message_id=?",
                               (server_id, owner_github_id, str(message_id))).fetchone()
            return None if row is None else self._local_message(conn, server_id,
                                                                 owner_github_id, row)

    def _item(self, conn, server_id, owner, state: wire.WorkState) -> ports.LocalItem:
        thread = str(state.thread_id)
        rows = conn.execute("SELECT message_id, body FROM human_messages WHERE server_id=? AND "
                            "owner=? AND thread_id=?", (server_id, owner, thread)).fetchall()
        messages = _sort_messages([self._local_message(conn, server_id, owner, r) for r in rows])
        drafts = [ports.Draft.model_validate_json(r["body"]) for r in conn.execute(
            "SELECT body FROM human_drafts WHERE server_id=? AND owner=? AND thread_id=? "
            "ORDER BY rowid", (server_id, owner, thread)).fetchall()]
        drafts = [d for d in drafts if d.send.state != "accepted"]
        editable = [d for d in drafts if d.send.state in ("draft", "awaiting_approval")]
        command = conn.execute("SELECT body FROM human_commands WHERE server_id=? AND owner=? "
                               "AND thread_id=?", (server_id, owner, thread)).fetchone()
        return ports.LocalItem(
            work_state=state, messages=messages, drafts=drafts,
            pending_command=(wire.WorkCommandRequest.model_validate_json(command["body"])
                             if command else None),
            dismiss_blockers=ports.dismiss_blockers(
                unsent_text=any(d.text or d.attachments for d in editable),
                send_states=[d.send.state for d in drafts],
                uploads_pending=any(ports.uploads_pending(d.files) for d in drafts)))

    def item(self, server_id: str, owner_github_id: int,
             thread_id: UUID) -> Optional[ports.LocalItem]:
        with self._tx() as conn:
            state = self._work_row(conn, server_id, owner_github_id, thread_id)
            return None if state is None else self._item(conn, server_id, owner_github_id, state)

    def _items(self, conn, server_id, owner, inactive: bool) -> list[ports.LocalItem]:
        rows = conn.execute("SELECT body FROM human_work WHERE server_id=? AND owner=? AND "
                            "inactive=?", (server_id, owner, int(inactive))).fetchall()
        states = sorted((wire.WorkState.model_validate_json(r["body"]) for r in rows),
                        key=wire.item_order_key, reverse=True)
        return [self._item(conn, server_id, owner, s) for s in states]

    def snapshot(self, server_id: str, owner_github_id: int, *,
                 availability: ports.Availability, stale: bool) -> ports.QueueSnapshot:
        with self._tx() as conn:
            items = self._items(conn, server_id, owner_github_id, inactive=False)
            history = conn.execute("SELECT COUNT(*) FROM human_work WHERE server_id=? AND "
                                   "owner=? AND inactive=1",
                                   (server_id, owner_github_id)).fetchone()[0]
            unsent = [ports.Draft.model_validate_json(r["body"]) for r in conn.execute(
                "SELECT d.body FROM human_drafts d WHERE d.server_id=? AND d.owner=? AND "
                "d.thread_id = d.message_id AND NOT EXISTS (SELECT 1 FROM human_work w WHERE "
                "w.server_id=d.server_id AND w.owner=d.owner AND w.thread_id=d.thread_id) "
                "ORDER BY d.rowid", (server_id, owner_github_id)).fetchall()]
        return ports.QueueSnapshot(server_id=server_id, owner_github_id=owner_github_id,
                                   availability=availability, items=items, unsent=unsent,
                                   history_count=history, stale=stale)

    def history(self, server_id: str, owner_github_id: int, continuation: Optional[str],
                limit: int) -> ports.HistoryPage:
        with self._tx() as conn:
            items = self._items(conn, server_id, owner_github_id, inactive=True)
        start = int(continuation) if continuation and continuation.isdigit() else 0
        end = start + max(1, min(limit, ports.HISTORY_PAGE_MAX))
        return ports.HistoryPage(items=items[start:end],
                                 continuation=str(end) if end < len(items) else None)
