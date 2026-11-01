"""Update this checkout of JARVIS from GitHub.

    .venv/bin/python -m jarvis.update            check, update, restart
    .venv/bin/python -m jarvis.update --check    only say where it stands

With the server running it asks the server to update itself
(`POST /api/update` with the tool token), so the server restarts on the new
code; with the server stopped it only updates. Either way the work is
self_update's, the same functions the page and the brain use. The server is
looked for first at the address it recorded at startup
(`server_address.running()`, read while its process is alive), else at
JARVIS_URL, else at JARVIS_TOOL_URL's address (the one the brain's tool
channel uses), else on 127.0.0.1:8340 over https when the certificates are
there and http when they are not. Only a refused connection at one of the
fallbacks counts as the server being stopped; a recorded server whose
process is alive but cannot be reached, or anything that answers wrongly,
stops the command without updating.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from jarvis import data_paths, self_update, server_address
from jarvis.jarvis_mcp import _ssl_context_for

# pip and npm can take minutes; the server answers once they are done.
SERVER_WAIT_SEC = 3 * self_update.STEP_TIMEOUT_SEC


def server_url() -> str:
    """Where to look when no server has recorded its address."""
    explicit = os.environ.get("JARVIS_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    tool = urllib.parse.urlsplit(os.environ.get("JARVIS_TOOL_URL", "").strip())
    if tool.scheme and tool.netloc:
        return f"{tool.scheme}://{tool.netloc}"
    certs = all((data_paths.CONFIG_DIR / name).exists() for name in ("cert.pem", "key.pem"))
    return f"{'https' if certs else 'http'}://127.0.0.1:{server_address.DEFAULT_PORT}"


def ask_server(url: str) -> dict | None:
    """The server's update outcome, or None when the connection there is
    refused (no server listening). Any other failure exits: something is
    there, and the checkout must not be changed under it."""
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
        # A refusal (409) or a failed step (500) still carries the outcome;
        # any other answer (the web boundary's 403, a 404 from a server
        # without /api/update) is not one.
        body = e.read().decode("utf-8", "replace")
        try:
            answered = json.loads(body)
        except ValueError:
            answered = None
        if e.code in (409, 500) and isinstance(answered, dict) and "status" in answered:
            return answered
        raise SystemExit(f"JARVIS answered {e.code}: {body.strip()[:200]}") from e
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ConnectionRefusedError):
            return None
        raise SystemExit(f"Something is at {url} but did not answer as JARVIS ({reason}); "
                         "not updating under it.") from e


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

    recorded = server_address.running()
    if recorded is not None:
        answered = ask_server(recorded.url)
        if answered is None:
            raise SystemExit(
                f"JARVIS (process {recorded.pid}) recorded {recorded.url} as its "
                "address but refused the connection there; not updating under it.")
    else:
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
