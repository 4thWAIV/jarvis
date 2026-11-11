"""`python -m jarvis.knowledge serve | rebuild | copy | verify | export-sqlite`.

`serve` runs the engine until its stdin pipe closes or it is sent SIGTERM.
`rebuild` builds every index, vector and card again from the documents;
it never drops a document (the engine holds records and their history).
`copy --db <file>`, `verify --db <file>` and `export-sqlite --out <file>`
are the move kit (`jarvis/knowledge_move.py`): SQLite into the engine,
checked table by table, and back. They reach the running engine at its
recorded address, or open `--engine-dir <dir>` in this process (tests and
offline use; refused while an engine runs on it).
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import sys

MOVE_DEADLINE_SEC = 300.0


def rebuild(directory) -> dict:
    """Rebuild every index, vector and card from the documents, never
    dropping one: the engine holds JARVIS's records and their history now.
    A snapshot is taken first; the general, keyword and extra indexes are
    built again by compacting every generation into one; the vector files
    are dropped, so every document is embedded again; the warm cards go."""
    from jarvis.knowledge.engine import Engine
    from jarvis.knowledge.vectors import VECS
    engine = Engine.open(directory)
    try:
        engine.compact_in_child = False
        engine.store.compact_in_child = False
        snap = engine.store.snapshot()
        dropped = engine.store.drop_external(VECS)
        engine.rebuild_indexes()
        generations = len(engine.store.manifest.generations())
    finally:
        engine.close()
    (directory / "cache.warm").unlink(missing_ok=True)
    return {"snapshot": snap.name, "vector_files": dropped, "generations": generations}


@contextlib.contextmanager
def _move_uds(engine_dir):
    """The UDS interface for the move kit: an engine opened here over
    `engine_dir`, or the running engine at its recorded address."""
    from jarvis.knowledge.uds import EngineUDS, in_process
    if engine_dir is not None:
        from pathlib import Path
        from jarvis.knowledge.engine import EngineLocked, lock_is_free
        directory = Path(engine_dir)
        if directory.exists() and not lock_is_free(directory):
            raise EngineLocked(f"an engine is running on {directory}; leave out --engine-dir to use it")
        with in_process(directory) as (_, uds):
            uds.deadline = MOVE_DEADLINE_SEC
            yield uds
        return
    from jarvis.knowledge import address
    from jarvis.knowledge_client import HTTPTransport
    if address.running() is None:
        raise RuntimeError("no knowledge engine is running; start JARVIS or give --engine-dir")
    yield EngineUDS(HTTPTransport(lambda: getattr(address.running(), "pid", None)),
                    deadline=MOVE_DEADLINE_SEC)


def _move(args) -> int:
    from pathlib import Path
    from jarvis import knowledge_move as move
    try:
        with _move_uds(args.engine_dir) as uds:
            if args.command == "copy":
                r = move.copy(Path(args.db), uds, batch=args.batch)
                if r.state == "already":
                    print(f"the engine already holds this move of {args.db}; nothing was written")
                else:
                    print(f"copied {r.rows} rows of {r.tables} tables from {args.db} in {r.seconds:.1f} s "
                          f"({r.writes} writes); source SHA-256 {r.source_sha256}")
                return 0
            if args.command == "verify":
                report = move.verify(Path(args.db), uds)
                print(report.words())
                return 0 if report.ok else 1
            done = move.export_sqlite(uds, None, Path(args.out),
                                      schema_from=Path(args.schema_from) if args.schema_from else None)
            print(f"wrote {done['rows']} rows of {done['tables']} tables to {done['path']} "
                  f"in {done['seconds']:.1f} s")
            return 0
    except Exception as e:      # every refusal is said plainly, never a traceback
        print(f"{args.command}: {e}", file=sys.stderr)
        return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the engine")
    serve.add_argument("--no-stdin-watch", action="store_true",
                       help="do not exit when stdin closes (for running by hand)")
    sub.add_parser("rebuild", help="build the indexes, vectors and cards again; no document is dropped")
    for name, words in (("copy", "copy a SQLite file into the engine (never writes the file)"),
                        ("verify", "check the engine holds a SQLite file's rows, table by table"),
                        ("export-sqlite", "write the moved tables back to a new SQLite file")):
        cmd = sub.add_parser(name, help=words)
        if name == "export-sqlite":
            cmd.add_argument("--out", required=True, help="the new file; an existing one is refused")
            cmd.add_argument("--schema-from", help="take the CREATE statements from this SQLite file")
        else:
            cmd.add_argument("--db", required=True, help="the SQLite file")
        if name == "copy":
            cmd.add_argument("--batch", type=int, default=1000, help="writes a transaction (at most 1000)")
        cmd.add_argument("--engine-dir", help="open this engine folder here instead of the running engine")
    compact = sub.add_parser("compact", help="the compaction child (run by the engine)")
    compact.add_argument("plan")
    compact.add_argument("result")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.command == "serve":
        from jarvis.knowledge import serve as engine_serve
        return engine_serve.run(watch_stdin=not args.no_stdin_watch)
    if args.command == "compact":
        from pathlib import Path
        from jarvis.knowledge.store import run_compaction_child
        return run_compaction_child(Path(args.plan), Path(args.result))
    if args.command in ("copy", "verify", "export-sqlite"):
        return _move(args)
    if args.command == "rebuild":
        from jarvis import data_paths
        from jarvis.knowledge.engine import EngineLocked, lock_is_free
        directory = data_paths.knowledge_dir()
        if directory.exists() and not lock_is_free(directory):
            print(str(EngineLocked("stop the engine before a rebuild")), file=sys.stderr)
            return 3
        report = rebuild(directory)
        print(f"rebuilt the indexes of {directory} from its {report['generations']} generation(s); "
              f"snapshot {report['snapshot']} taken first; {report['vector_files']} vector file(s) "
              "dropped to be embedded again. No document was dropped.")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
