"""Send Tim a message, about the Work item it belongs to.

    python -m jarvis.message --work <item id> "<what it says>" [--package <zip>] [--as <session id>]

Every message to Tim is one thing (docs/planning/one-channel/
messages-by-work-item.md): it is from you (your agent's own onboarded key
signs the request, as work_report.py's do: `--as <session id>` or
JARVIS_AGENT_SESSION, else a work-item key in this folder or above, which
may only send about its own item), about one Work item (`--work`), says
something short, and may carry a package (`--package`, a zip made with
`python -m jarvis.make_package`). It lands on that item's Messages; with no
`--work` it goes to his Inbox, which JARVIS refuses while you have a piece
on an open item (it names the item).

A decision or a question is never sent bare: it goes inside a package that
shows him what he needs to decide. A message that asks something (a
sentence ending in "?") without `--package` is refused.

Prints the message's id. Exits 1 when JARVIS refuses it, saying why. A
message JARVIS does not answer in time is asked about by its key and sent
again only when it did not land (`workflow_report.report`), so it never
arrives twice.
"""
from __future__ import annotations

import argparse
import base64
import sys
import urllib.parse
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.workflow_report import report, signed_client  # noqa: E402

ROUTE = "/api/messages"

# What every old way of asking him a decision now answers (work_report.py's
# decision and withdraw, workflow_report.py's decision_needed, and their
# routes): a decision reaches him only as a package showing its background.
ASK_WITH_A_PACKAGE = (
    "Decisions are no longer asked here. Every decision goes to Tim as a decision package "
    "showing him what he needs to decide: python -m jarvis.make_package --handoff <handoff.json> "
    "--engine <claude|codex> --session <your session id> --work <item id> "
    "(docs/making-a-package.md). It arrives as a message on that item, and his answers come "
    "back to your session. To take one back, send a newer package of the same name (it "
    "replaces the open one) or a message saying so: python -m jarvis.message --work <item id> "
    "\"<what changed>\". Nothing was sent.")


def body(says: str, package: Path | None) -> dict:
    """The request's body: what it says, and the package's bytes in base64."""
    out = {"says": says}
    if package is not None:
        out["package"] = base64.b64encode(package.read_bytes()).decode("ascii")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.message",
                                     description="Send Tim a message about a Work item.")
    parser.add_argument("says", help="what it says, short")
    parser.add_argument("--work", default="",
                        help="the Work item it is about (required while you have a piece "
                             "on an open item)")
    parser.add_argument("--package", type=Path, default=None,
                        help="a package zip it carries (made with python -m jarvis.make_package)")
    parser.add_argument("--as", dest="as_session", default="",
                        help="your session id: the request is signed with its key")
    parser.add_argument("--cert", type=Path)
    args = parser.parse_args(argv)
    if args.package is not None and not args.package.is_file():
        print(f"There is no zip at {args.package}.", file=sys.stderr)
        return 1
    path = ROUTE + (f"?work={urllib.parse.quote(args.work)}" if args.work else "")
    with signed_client(args.cert, args.as_session, "work") as client:
        try:
            sent = report(client, path, body(args.says, args.package))
        except SystemExit as refused:
            print(refused, file=sys.stderr)
            return 1
    message = sent["message"]
    print(message["id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
