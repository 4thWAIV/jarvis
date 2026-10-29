import asyncio
import functools
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest
import pytest_asyncio

from tests.fixtures.local_config import read_local_skips

# server.py loads its .env the moment it is imported, and test modules import
# it at collection, before any fixture runs. Pointing JARVIS_ENV_FILE at a file
# that does not exist, here, means even that first import never reads the
# developer's real settings. Each test then gets its own file below.
NO_DOTENV_UNDER_TEST = Path(tempfile.gettempdir()) / "jarvis-tests" / "no-dotenv" / ".env"
os.environ["JARVIS_ENV_FILE"] = str(NO_DOTENV_UNDER_TEST)


def pytest_report_header(config):
    """Say up front when this machine's own file is skipping tests."""
    skips = read_local_skips()
    if skips:
        return (f"tests/local-config.toml skips: tests={skips.tests} "
                f"markers={skips.markers}")


def pytest_collection_modifyitems(config, items):
    """Skip what tests/local-config.toml lists. No file, no skips."""
    try:
        skips = read_local_skips()
    except ValueError as e:
        raise pytest.UsageError(str(e)) from e
    if not skips:
        return
    mark = pytest.mark.skip(reason=skips.reason)
    for item in items:
        if skips.skips(item.nodeid, {m.name for m in item.iter_markers()}):
            item.add_marker(mark)


@pytest.fixture(autouse=True)
def _start_from_the_shipped_untrusted_actions_default(monkeypatch):
    """Every test starts from the shipped default: the refusal OFF.

    `server.py` copies the repository's `.env` into the environment at import,
    so a developer who set JARVIS_REFUSE_UNTRUSTED_ACTIONS there would flip
    every test's default. A test that wants the refusal on sets it itself
    (`refusal_on` below); the obsolete JARVIS_ALLOW_UNTRUSTED_ACTIONS is
    cleared too.

    Set to empty rather than deleted: the `.env` loader only fills in keys
    that are missing (`os.environ.setdefault`), and the many tests that
    `importlib.reload(server)` would put a deleted key straight back. Empty
    reads as off.
    """
    monkeypatch.setenv("JARVIS_REFUSE_UNTRUSTED_ACTIONS", "")
    monkeypatch.setenv("JARVIS_ALLOW_UNTRUSTED_ACTIONS", "")


@pytest.fixture
def refusal_on(monkeypatch):
    """Opt in to the untrusted-content refusal for one test or module."""
    monkeypatch.setenv("JARVIS_REFUSE_UNTRUSTED_ACTIONS", "1")


@pytest.fixture(autouse=True)
def _never_spawn_a_real_brain(monkeypatch):
    """jarvis.server.lifespan builds the brain but must not start `claude` under test,
    nor read the keychain and call Anthropic for the usage limits."""
    monkeypatch.setenv("JARVIS_BRAIN_AUTOSTART", "0")
    monkeypatch.setenv("JARVIS_USAGE_POLL", "0")


@pytest.fixture(autouse=True)
def _never_read_the_user_s_codex_sessions(monkeypatch, tmp_path):
    """The session watcher reads the user's Codex sessions out of ~/.codex
    (codex_sessions.py). Under test it reads an empty Codex home instead."""
    monkeypatch.setenv("JARVIS_CODEX_SESSIONS_HOME", str(tmp_path / "codex-sessions-home"))


@pytest.fixture(autouse=True)
def _never_post_a_real_notification(monkeypatch, request):
    """No test may spam the developer's Notification Centre.

    Patched on the `notifier` module object itself rather than on `server`, so
    the `importlib.reload(server_module)` that several test fixtures do cannot
    hand the real implementation back. test_notifier.py is exempt: it tests
    notify() itself and mocks the subprocess boundary directly.
    """
    if request.module.__name__.endswith("test_notifier"):
        return
    from jarvis import notifier

    async def _blocked(*args, **kwargs):
        raise AssertionError("a test tried to post a real macOS notification; "
                             "mock notifier.notify")

    monkeypatch.setattr(notifier, "notify", _blocked)


@pytest.fixture(autouse=True)
def _never_touch_the_real_projects_folder(monkeypatch, tmp_path):
    """No test may create a directory in the user's real ~/Projects.

    `create_project` writes into JARVIS_PROJECTS_DIR (default ~/Projects) and
    will create that root if it is missing, so the default is redirected into
    a tmp_path for every test — the same reasoning as JARVIS_DATA_DIR. A test
    that wants its own root still sets the variable itself; this only fills in
    a safe default.
    """
    monkeypatch.setenv("JARVIS_PROJECTS_DIR", str(tmp_path / "projects-root"))


@pytest.fixture(autouse=True)
def _never_write_to_the_live_dotenv(monkeypatch, tmp_path):
    """No test may write into the developer's live `.env`.

    Found the hard way: the settings endpoints write straight into the
    repository's own .env, so a test that posted a preference silently
    rewrote the developer's real configuration — and a test written to
    prove `.env` line injection injected the line for real. Same reasoning
    as JARVIS_DATA_DIR; a test that wants its own file still sets the
    variable itself.
    """
    monkeypatch.setenv("JARVIS_ENV_FILE", str(tmp_path / "dotenv" / ".env"))


class _MemorySecrets:
    """A Keychain stand-in: tests never read or write the user's Keychain."""

    def __init__(self):
        self.items: dict[str, str] = {}

    def get(self, account):
        return self.items.get(account)

    def set(self, account, secret):
        self.items[account] = secret

    def delete(self, account):
        self.items.pop(account, None)


@pytest.fixture(autouse=True)
def _never_touch_the_real_keychain(monkeypatch):
    """The team connection's credential store is the login Keychain
    (team_client.KeychainStore, through `security`). Every test gets a fresh
    connection over an in-memory store instead."""
    from jarvis import team_client
    monkeypatch.setattr(team_client, "connection",
                        team_client.TeamConnection(store=_MemorySecrets()))
    monkeypatch.setattr(team_client, "KeychainStore", _MemorySecrets)


@pytest.fixture(autouse=True)
def _never_write_to_the_live_data_dir(monkeypatch, tmp_path):
    """No test may write into the user's real `data/`.

    Most tests already set JARVIS_DATA_DIR (and still do — this only fills in
    a safe default), but a test that merely drives the brain writes there too
    now that a rate-limit event is persisted: without this, running the suite
    overwrote the live usage reading with a fixture's fake one.
    """
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data-dir"))
    # Nor load the real speech model: with no override it is looked for in
    # that empty data folder, so the server starts with speech input failed.
    monkeypatch.delenv("JARVIS_STT_MODEL_DIR", raising=False)


@pytest_asyncio.fixture(autouse=True)
async def _no_run_left_mid_flight():
    """No test may end with a run's driver still starting its child.

    The CI hang, twice, on the macOS runner's Python 3.12 and never on 3.13:
    a test spawned a run, asserted on the row, and returned in the same
    millisecond — while `RunExecutor._drive` was still inside
    `asyncio.create_subprocess_exec`, before the child existed. The loop's
    teardown then cancelled that task mid-spawn, and 3.12's subprocess
    transport never completes a cancellation delivered there: the suite sat
    in `_cancel_all_tasks` until GitHub killed the job 25 minutes later.
    3.13 completes it, which is why no local run ever showed it.

    So every driver alive at the end of a test is waited for here, inside
    the test's own loop, before the runner closes it. A test's fake `claude`
    exits in milliseconds, so the wait is normally nothing; a driver that
    is queued or reading forever is cancelled only after it has had time to
    get past the spawn, which is the one place cancellation must not land.
    """
    yield
    me = asyncio.current_task()

    def _alive(qualname: str) -> list:
        return [t for t in asyncio.all_tasks()
                if t is not me and not t.done()
                and getattr(t.get_coro(), "__qualname__", "") == qualname]

    # First, the exact place: asyncio's own pipe-connection task, which
    # exists only between fork and "the child is up". Whoever spawned it
    # (a run driver, the brain, a fake `osascript`) is parked on it. Let it
    # finish — milliseconds — and yield once so the spawner moves on.
    connecting = _alive("BaseSubprocessTransport._connect_pipes")
    if connecting:
        await asyncio.wait(connecting, timeout=5)
        await asyncio.sleep(0)
    # Then a run driver still going: give it time to end on its own (a
    # test's fake claude exits at once) before it is cancelled somewhere
    # safe to cancel.
    drivers = _alive("RunExecutor._drive")
    if not drivers:
        return
    _done, pending = await asyncio.wait(drivers, timeout=10)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending, timeout=5)


# The voice page, built once for the whole run and served from a temporary
# folder on loopback with 30 agents at /api/agents. Every page test module
# shares it: building it once per module was 16 builds of the same page.
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
PAGE_AGENTS = {"agents": [{"session_id": str(i), "name": f"agent {i}", "active": True}
                          for i in range(30)]}


@pytest.fixture(scope="session")
def page_url(tmp_path_factory):
    if not shutil.which("npx") or not (FRONTEND / "node_modules").is_dir():
        pytest.skip("node and the frontend's node_modules are needed to build the page")
    out = tmp_path_factory.mktemp("voice-page")
    built = subprocess.run(
        ["npx", "vite", "build", "--outDir", str(out), "--emptyOutDir", "--logLevel", "error"],
        cwd=FRONTEND, capture_output=True, text=True, timeout=300)
    assert built.returncode == 0, built.stderr
    (out / "api").mkdir()
    (out / "api" / "agents").write_text(json.dumps(PAGE_AGENTS))

    handler = functools.partial(_QuietHandler, directory=str(out))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/index.html"
    httpd.shutdown()


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass
