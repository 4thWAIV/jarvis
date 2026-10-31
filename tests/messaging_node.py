"""One JARVIS backend as its own process, for the messaging integration
scenarios (tests/test_messaging_integration.py): the real server app and
its messaging, paired to a local team server, in its own data directory.

    python -m tests.messaging_node        (from the checkout's root)

Its environment names the data directory, the env file and the team
server and its own loopback port (JARVIS_DATA_DIR, JARVIS_ENV_FILE,
JARVIS_TEAM_DEV_SERVERS, JARVIS_PORT,
JARVIS_BRAIN_AUTOSTART=0); the saved team connection is already in the data
directory. The device session's credential arrives as one line on stdin
and is kept in memory only (never the Keychain, never an argument).

Nothing here reaches outside the test: the session watcher (which reads
the user's own Claude Code and Codex sessions), the preflight checks and
the brain account refresh are not started.
"""
from __future__ import annotations

import os
import sys

from tests.fixtures.memory_secrets import MemorySecrets


class _Secrets(MemorySecrets):
    """The credential given on stdin, in memory only."""

    def __init__(self, secret: str):
        super().__init__()
        self.secret = secret

    def get(self, account):
        return self.items.get(account, self.secret)

    def delete(self, account):
        self.items[account] = ""
        self.secret = ""


async def _nothing(*args, **kwargs):
    return None


def main() -> None:
    secret = sys.stdin.readline().strip()
    from jarvis import team_client
    team_client.connection = team_client.TeamConnection(store=_Secrets(secret))
    team_client.KeychainStore = None          # nothing may reach the real Keychain
    from jarvis import server
    server.start_session_watcher = _nothing
    server.stop_session_watcher = _nothing
    server._run_preflight = _nothing
    server.brain_accounts.refresh = _nothing
    import uvicorn
    uvicorn.run(server.app, host="127.0.0.1", port=int(os.environ["JARVIS_PORT"]),
                log_level="warning")


if __name__ == "__main__":
    main()
