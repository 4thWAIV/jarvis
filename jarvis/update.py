"""Update this checkout of JARVIS from GitHub.

    .venv/bin/python -m jarvis.update            check, update, restart
    .venv/bin/python -m jarvis.update --check    only say where it stands

With the server running it asks the server to update itself
(`POST /api/update` with the tool token), so the server restarts on the new
code; with the server stopped it only updates. Either way the work is
self_update's, the same functions the page and the brain use. The server is
looked for at JARVIS_URL, else on 127.0.0.1:8340 over https when the
certificates are there and http when they are not.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from jarvis import data_paths, self_update
from jarvis.jarvis_mcp import _ssl_context_for

DEFAULT_PORT = 8340
# pip and npm can take minutes; the server answers once they are done.
SERVER_WAIT_SEC = 3 * self_update.STEP_TIMEOUT_SEC


def server_url() -> str:
    explicit = os.environ.get("JARVIS_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    certs = all((data_paths.CONFIG_DIR / name).exists() for name in ("cert.pem", "key.pem"))
    return f"{'https' if certs else 'http'}://127.0.0.1:{DEFAULT_PORT}"


def ask_server(url: str) -> dict | None:
    """The server's update outcome, or None when no server answers there."""
    endpoint = f"{url}/api/update"
    req = urllib.request.Request(
        endpoint, data=b"{}", method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {data_paths.ensure_tool_token()}"})
    try:
        with urllib.request.urlopen(req, timeout=SERVER_WAIT_SEC,
                                    context=_ssl_context_for(endpoint)) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        # A refusal (409) or a failed step (500) still carries the outcome.
        try:
            return json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            raise SystemExit(f"JARVIS answered {e.code}.") from e
    except (urllib.error.URLError, ConnectionError):
        return None


def _print(head: str, details: list[str]) -> None:
    print(head)
    for line in details:
        print(f"  {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jarvis.update",
                                     description="Update JARVIS from GitHub.")
    parser.add_argument("--check", action="store_true", help="only report; change nothing")
    args = parser.parse_args(argv)

    if args.check:
        status = self_update.check()
        _print(*self_update.describe(status))
        return 0 if status.source == self_update.GITHUB and not status.error else 1

    answered = ask_server(server_url())
    if answered is not None:
        out = self_update.Outcome.from_json(answered)
        _print(*self_update.describe_outcome(out))
        if out.restart:
            print("JARVIS is restarting on the new code.")
    else:
        out = self_update.update()
        _print(*self_update.describe_outcome(out))
        if out.restart:
            print("JARVIS is not running; start it to use the new code.")
    return 0 if out.ok else 1


if __name__ == "__main__":
    sys.exit(main())
