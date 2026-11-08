"""Durable Work feedback delivery through the existing agent message queue.

The event and delivery obligation commit together. A watcher poll creates
one message and links it in one transaction. The existing provider adapters
send it and their transcript tracking supplies pickup; a queue entry alone
never claims that an agent is working.
"""
from contextlib import closing
import json
import logging
import time
from jarvis import run_store, session_steer, work_store

log = logging.getLogger(__name__)


def schedule(conn, item_id, seq, detail):
    key = detail.get("key")
    rows = conn.execute(
        "SELECT DISTINCT who_ref FROM work_pieces WHERE item_id=? AND who_kind='agent'"
        + (" AND key=?" if key else ""), (item_id, key) if key else (item_id,)).fetchall()
    # Keep an unresolved obligation when ownership cannot be routed. Never
    # silently report that an unknown owner received the request.
    owners = [r[0] for r in rows if r[0]] or [""]
    for owner in owners:
        conn.execute("INSERT OR IGNORE INTO work_deliveries(event_seq,item_id,session_id) "
                     "VALUES(?,?,?)", (seq, item_id, owner))


def deliveries(item_id):
    with closing(work_store._connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT d.*, m.status AS message_status, m.failure, m.delivered_at, m.started_at "
            "FROM work_deliveries d LEFT JOIN messages m ON m.id=d.message_id "
            "WHERE d.item_id=? ORDER BY d.event_seq", (item_id,))]


def _status(conn, row, status, reason, now):
    if row["status"] == status and row["reason"] == reason:
        return
    conn.execute("UPDATE work_deliveries SET status=?,reason=? WHERE event_seq=? AND session_id=?",
                 (status, reason, row["event_seq"], row["session_id"]))
    summary = f"Owner notification: {status}. {reason}"[:300]
    work_store._insert_event(conn, row["item_id"], work_store.UPDATE, summary,
                            {"delivery_event": row["event_seq"], "session": row["session_id"],
                             "message_id": row["message_id"], "delivery_status": status}, now)
    conn.execute("UPDATE work_items SET latest_update=?,revision=revision+1,updated_at=? WHERE id=?",
                 (summary, now, row["item_id"]))


def poll(snapshot, now=None):
    """Called before send_queued. Safe across restart and concurrent polls."""
    now = time.time() if now is None else now
    try:
        with closing(work_store._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT * FROM work_deliveries WHERE status NOT IN "
                                "('answered','cancelled')").fetchall()
            for row in rows:
                if row["message_id"] is None:
                    session = snapshot.by_id(row["session_id"])
                    if session is None or session.state == "gone":
                        _status(conn, row, "blocked", "The responsible agent is unavailable; delivery will retry when it returns.", now)
                        continue
                    e = conn.execute("SELECT * FROM work_events WHERE seq=?", (row["event_seq"],)).fetchone()
                    item = conn.execute("SELECT title,closed_at,state FROM work_items WHERE id=?", (row["item_id"],)).fetchone()
                    if item["closed_at"] is not None or item["state"] == work_store.DONE:
                        _status(conn, row, "cancelled", "The item was closed or accepted before delivery.", now)
                        continue
                    detail = json.loads(e["detail"])
                    text = session_steer.STEER_SIGNATURE + (
                        f"Work item: {item['title']} ({row['item_id']}). Feedback event {row['event_seq']}.\n"
                        f"{detail.get('words') or detail.get('answer') or e['summary']}\n"
                        "Read the current Work item and its holds. Record acknowledgment on that item, "
                        "then continue the authorized work, or record the exact blocker. "
                        "Keep unrelated pieces waiting on their existing decisions. Do not claim Working without live evidence.")
                    cur = conn.execute("INSERT INTO messages(session_id,provider,agent_name,project,text,status,staged_at) "
                                       "VALUES(?,?,?,?,?,'queued',?)", (session.session_id, session.provider,
                                       session.voice_name, session.project, text, now))
                    conn.execute("UPDATE work_deliveries SET message_id=? WHERE event_seq=? AND session_id=?",
                                 (cur.lastrowid, row["event_seq"], row["session_id"]))
                    row = dict(row); row["message_id"] = cur.lastrowid
                    _status(conn, row, "queued", "Waiting for the agent's next free turn; not yet picked up.", now)
                else:
                    m = conn.execute("SELECT * FROM messages WHERE id=?", (row["message_id"],)).fetchone()
                    status = m["status"] if m else "failed"
                    reason = (m["failure"] or "") if m else "The queued message is missing."
                    if status == "working":
                        status, reason = "picked_up", "The session transcript shows the turn started; this is not token-spending evidence."
                    elif status in ("sent", "delivered"):
                        reason = "Awaiting the agent's turn on this request."
                    elif status == "undelivered":
                        reason = "The provider accepted the send, but the request has not appeared in the session transcript."
                    _status(conn, row, status, reason, now)
            changed = conn.total_changes > 0
            conn.commit()
            return changed
    except Exception:
        log.exception("Work notification delivery failed; durable obligations retained for retry")
        return False
