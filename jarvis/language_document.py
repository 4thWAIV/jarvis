"""An agent's canonical architecture documents, through JARVIS.

    python -m jarvis.language_document [--cert CERT] --as <session id> <operation>
        [--source <document.yaml>] [--json '<arguments>' | --json-file <arguments.json>]

The same operations JARVIS's brain has as its `language_document` tool, on
the same route (POST /api/language-documents, language_document_api.py),
each request signed with the agent's own onboarded key (`--as <session
id>` or JARVIS_AGENT_SESSION, else a work-item key in this folder or
above), which needs the `packages` right. The route answers a page at a
time; this follows every continuation and prints the whole JSON envelope
(`{"ok": true, "result": ...}` or `{"ok": false, "error": ...}`). Exits 0
when it is ok, 1 when JARVIS refused the operation or the request, or a
validated revision does not check (it is kept as a draft, never sent).

- `capabilities`: the kit in force and the constructs the arch language covers.
- `validate`: check a proposed revision. `--source` reads the YAML from a
  file; `--json` gives the rest: work_id, scope_revision, author, coverage
  (constructs, declarations, unresolved, outside_language), and for a
  correction document_id and parent_revision; optional evidence.
- `retrieve` / `render`: one exact revision (document_id, revision; render
  also source_revision).
- `ground`: a delivered package's exact canonical source and its authored
  explanation, by package_id (optional document_id, revision,
  source_revision as expected values, inbox_id, declaration).

An agent does not publish: the checked revision goes to Tim with
`python -m jarvis.make_package --document <document_id> --revision
<revision> ...` (docs/making-a-package.md, "Architecture documents").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.workflow_report import _post, signed_client  # noqa: E402

ROUTE = "/api/language-documents"
OPERATIONS = ("capabilities", "validate", "retrieve", "render", "ground")
# A validate runs the kit's check and render; a ground reads a package.
TIMEOUT_SEC = 120.0
# Continuations a whole envelope may take (a page is about 700 characters).
MAX_PAGES = 20000


def arguments_of(args) -> dict:
    """The operation's arguments: --json or --json-file, with --source's
    file as `source`."""
    given: dict = {}
    if args.json is not None and args.json_file is not None:
        raise SystemExit("Give --json or --json-file, not both.")
    raw = args.json if args.json is not None else (
        args.json_file.read_text(encoding="utf-8") if args.json_file is not None else None)
    if raw is not None:
        try:
            given = json.loads(raw)
        except ValueError as error:
            raise SystemExit(f"The arguments are not JSON: {error}") from None
        if not isinstance(given, dict):
            raise SystemExit("The arguments must be one JSON object.")
    if args.source is not None:
        if args.operation != "validate":
            raise SystemExit("--source is for validate.")
        given["source"] = args.source.read_text(encoding="utf-8")
    return given


def call(client, operation: str, arguments: dict) -> dict:
    """One operation, every continuation followed: the whole envelope."""
    page = _post(client, ROUTE, {"operation": operation, "arguments": arguments})
    text = page.get("data", "")
    for _ in range(MAX_PAGES):
        if not page.get("next"):
            return json.loads(text)
        page = _post(client, ROUTE, {"operation": "continue", "continuation": page["next"]})
        text += page.get("data", "")
    raise SystemExit("The envelope did not end; run the operation again.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.language_document",
                                     description="Canonical architecture documents, signed with "
                                                 "your agent's key.")
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--as", dest="as_session", default="",
                        help="your session id: every request is signed with its key")
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("--source", type=Path, help="validate: the YAML document")
    parser.add_argument("--json", help="the operation's arguments, one JSON object")
    parser.add_argument("--json-file", type=Path, help="the same, from a file")
    args = parser.parse_args(argv)
    arguments = arguments_of(args)
    with signed_client(args.cert, args.as_session, "packages") as client:
        client.timeout = TIMEOUT_SEC
        try:
            envelope = call(client, args.operation, arguments)
        except SystemExit as refused:
            print(refused, file=sys.stderr)
            return 1
    print(json.dumps(envelope, indent=2, ensure_ascii=False))
    if not envelope.get("ok"):
        print((envelope.get("error") or {}).get("cause", "JARVIS refused it."), file=sys.stderr)
        return 1
    check = (envelope.get("result") or {}).get("check")
    if args.operation == "validate" and not (check or {}).get("ok"):
        # Kept as a proposed draft, but it cannot be sent: say why.
        print("It does not check, so it cannot be sent: "
              + "; ".join((check or {}).get("problems") or ["see check.diagnostics"]),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
