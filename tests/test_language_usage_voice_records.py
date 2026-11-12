"""The language, usage and voice records moved whole: their eight tables
copied into the engine as `langdoc`, `usage` and `settings` documents and
the voice record's day files, verified equal table by table, written back
as the same rows, and read by the stores as they read their own."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from jarvis import (data_paths, knowledge_move as move, language_documents, records, usage_history,
                    voice_control, voice_diagnostics)

TABLES = ("language_documents", "language_document_revisions", "language_publications",
          "usage_readings", "usage_hourly", "voice_input_settings", "voice_samples", "voice_events")
MAPPINGS = (language_documents.MAPPINGS + usage_history.MAPPINGS + voice_control.MAPPINGS
            + voice_diagnostics.MAPPINGS)
T = 1_800_000_000.0
DAY = 86400
# Values the stores read as they are (a CHECK, JSON text), by column.
CHOSEN = {
    "stop_mode": ["keep", "discard", "keep"], "switch_mode": ["stop", "continue", "stop"],
    "capture_sounds": [0, 1, None], "speech_muted": [1, 0, None],
    "user_key": ["local", "github:7", "nobody/else"],
    "provider": ["anthropic", "openai/x", "anthropic"], "window": ["seven_day"] * 3,
    "document_id": ["document-0", "document-1", "document-0"],
    "notices_by_source": ['{"status": 1}', "{}", None], "cause_values": ['{"load": 2}', "{}", None],
    "top_processes": ['[{"name": "x", "cpu": 1.5}]', "[]", None],
}


def _schema() -> list[dict]:
    recorded = json.loads((Path(data_paths.PACKAGE_DIR) / "sqlite_schema.json").read_text())
    return [o for o in recorded["schema"]["objects"] if o["tbl_name"] in TABLES and o["sql"]]


def _value(table: str, column: str, decl: str, i: int, nullable: bool):
    if table == "language_documents" and column == "id":
        column = "document_id"
    if column in CHOSEN:
        return CHOSEN[column][i]
    if i == 2 and nullable:
        return None
    if column in ("at", "hour", "created_at", "updated_at", "last_at"):
        return T + i * DAY + 0.25                    # a day apart: several day files and documents
    if "INT" in decl:
        return 1000 * i + 7
    if any(x in decl for x in ("REAL", "FLOA", "DOUB")):
        return i + 0.5
    return f"{table}.{column}.{i} /Users/tim/{i}"


def _filled(db: Path) -> Path:
    """Every column of every table filled, a NULL in each nullable one."""
    with closing(sqlite3.connect(db)) as conn:
        for o in _schema():
            conn.execute(o["sql"])
        for o in _schema():
            if o["type"] != "table":
                continue
            cols = [c for c in conn.execute(f"PRAGMA table_xinfo({o['name']})") if c[6] == 0]
            for i in range(3):
                values = [_value(o["name"], c[1], (c[2] or "").upper(), i, not c[3] and not c[5]) for c in cols]
                conn.execute(f"INSERT OR IGNORE INTO {o['name']} ({', '.join(c[1] for c in cols)}) "
                             f"VALUES ({', '.join('?' for _ in cols)})", values)
        conn.commit()
        empty = [t for t in TABLES if not conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]]
        nullable = {t: [c[1] for c in conn.execute(f"PRAGMA table_xinfo({t})") if not c[3] and not c[5]]
                    for t in TABLES}
        nulls = [t for t, cols in nullable.items() if cols and not conn.execute(
            f"SELECT count(*) FROM {t} WHERE " + " OR ".join(f"{c} IS NULL" for c in cols)).fetchone()[0]]
    assert not empty and not nulls
    return db


def _tables(db: Path) -> dict:
    with closing(sqlite3.connect(db)) as conn:
        return {t: sorted(map(repr, conn.execute(f"SELECT rowid, * FROM {t}"))) for t in TABLES}


def _copied(tmp_path) -> Path:
    db = _filled(tmp_path / "source.db")
    move.copy(db, records.uds(), list(MAPPINGS), files_root=data_paths.data_dir())
    return db


def test_the_eight_tables_round_trip_through_the_engine(tmp_path):
    db = _copied(tmp_path)
    uds, files = records.uds(), data_paths.data_dir()
    report = move.verify(db, uds, list(MAPPINGS), files_root=files)
    assert report.ok, report.words()
    out = tmp_path / "back.db"
    move.export_sqlite(uds, list(MAPPINGS), out, files_root=files)
    assert _tables(out) == _tables(db)
    # The documents and files are where the plan puts them.
    assert records.get("/language/document-0.langdoc") is not None
    assert records.get("/people/7/settings/voice-input.settings").data["user_key"] == "github:7"
    assert records.get("/people/local/settings/voice-input.settings") is not None
    assert records.get(usage_history.path_of("openai/x", usage_history.day_of(T + DAY))) is not None
    assert sorted(p.name.split(".", 1)[1] for p in data_paths.voice_dir().rglob("*.jsonl")) == \
        ["events.jsonl"] * 3 + ["samples.jsonl"] * 3


def test_what_the_stores_write_after_the_move_exports_as_rows(tmp_path):
    """Rows written after the move carry no rowid or id of their own: the
    export gives each the next after the largest, and every column back."""
    _copied(tmp_path)
    voice_control.change(stop="discard", user="github:8")
    usage_history.append("anthropic", {"seven_day": {"utilization": 12.0, "resets_at": T, "status": "ok",
                                                     "observed_at": T + 5}})
    assert voice_diagnostics.record_event(voice_diagnostics.Event(at=T + 9, kind="ready")) == 2008
    out = tmp_path / "back.db"
    move.export_sqlite(records.uds(), list(MAPPINGS), out, files_root=data_paths.data_dir())
    with closing(sqlite3.connect(out)) as conn:
        assert conn.execute("SELECT user_key, stop_mode, rowid FROM voice_input_settings "
                            "WHERE user_key = 'github:8'").fetchone() == ("github:8", "discard", 4)
        assert conn.execute("SELECT max(id), count(*) FROM usage_readings").fetchone() == (2008, 4)
        assert conn.execute("SELECT utilization FROM usage_readings WHERE id = 2008").fetchone() == (12.0,)
        assert conn.execute("SELECT kind FROM voice_events WHERE id = 2008").fetchone() == ("ready",)


def test_the_stores_read_the_moved_records(tmp_path):
    _copied(tmp_path)
    got = voice_control.read("github:7")
    assert (got["saved"], got["stop"], got["switch"], got["sounds"], got["speech_muted"]) == \
        (True, "discard", "continue", True, False)
    assert voice_control.read("local")["sounds"] is False
    assert [r["revision"] for r in language_documents.revisions("document-0")] == [
        "language_document_revisions.revision.0 /Users/tim/0",
        "language_document_revisions.revision.2 /Users/tim/2"]
    assert language_documents.publication("document-1", "language_publications.revision.1 /Users/tim/1") == {
        "document_id": "document-1", "revision": "language_publications.revision.1 /Users/tim/1",
        "archive_sha256": "language_publications.archive_sha256.1 /Users/tim/1",
        "archive_path": "language_publications.archive_path.1 /Users/tim/1", "created_at": T + DAY + 0.25}
    days = usage_history._days("anthropic", T - 1, T + 3 * DAY)
    assert [r["at"] for r in usage_history._readings(days, T - 1, T + 3 * DAY)] == [T + 0.25, T + 2 * DAY + 0.25]
    samples, events = voice_diagnostics.history(T - 1, T + 3 * DAY)
    assert [s.at for s in samples] == [T + 0.25, T + DAY + 0.25, T + 2 * DAY + 0.25]
    assert samples[0].notices_by_source == {"status": 1} and samples[2].notices_by_source == {}
    assert [(e.id, e.top_processes) for e in events] == [
        (7, [{"name": "x", "cpu": 1.5}]), (1007, []), (2007, [])]
