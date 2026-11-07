"""`python -m jarvis.knowledge serve | rebuild`.

`serve` runs the engine until its stdin pipe closes or it is sent SIGTERM.
`rebuild` drops the engine's derived state; ingest makes it again (I1).
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the engine")
    serve.add_argument("--no-stdin-watch", action="store_true",
                       help="do not exit when stdin closes (for running by hand)")
    sub.add_parser("rebuild", help="drop the derived state; ingest makes it again")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.command == "serve":
        from jarvis.knowledge import serve as engine_serve
        return engine_serve.run(watch_stdin=not args.no_stdin_watch)
    if args.command == "rebuild":
        from jarvis import data_paths
        from jarvis.knowledge.engine import EngineLocked, lock_is_free
        directory = data_paths.knowledge_dir()
        if directory.exists() and not lock_is_free(directory):
            print(str(EngineLocked("stop the engine before a rebuild")), file=sys.stderr)
            return 3
        shutil.rmtree(directory, ignore_errors=True)
        print(f"dropped {directory}; the next ingest pass rebuilds it")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
