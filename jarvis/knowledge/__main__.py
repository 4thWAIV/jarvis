"""`python -m jarvis.knowledge serve | rebuild`.

`serve` runs the engine until its stdin pipe closes or it is sent SIGTERM.
`rebuild` builds every index, vector and card again from the documents;
it never drops a document (the engine holds records and their history).
"""
from __future__ import annotations

import argparse
import logging
import sys


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the engine")
    serve.add_argument("--no-stdin-watch", action="store_true",
                       help="do not exit when stdin closes (for running by hand)")
    sub.add_parser("rebuild", help="build the indexes, vectors and cards again; no document is dropped")
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
