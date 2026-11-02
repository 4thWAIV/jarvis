"""Measurement only: where a page run's first test spends its setup.

A pytest plugin, loaded by CI's Page tests step alone with
`-p ci_first_test_profile` (PYTHONPATH=scripts). It changes no test and no
fixture: it wraps them and writes a timeline. Output is JSON lines in
$CI_PROFILE_OUT (nothing is written when it is unset).

Recorded, with seconds since the plugin loaded:
- the session's own steps: configure, collection, session start;
- every test's setup, call and teardown phases;
- every fixture's setup (name, scope, exclusive duration), in every test;
- for the first test of the session and the first of each module ("detail"
  tests): every subprocess started (argv head, wall time) and every
  Playwright call (driver start and stop, launch, new_page, goto, clicks,
  waits, evaluate), with its start and duration;
- for the first test of the session only, Playwright's own browser log
  (DEBUG=pw:browser* into DEBUG_FILE), so a slow launch splits into spawn
  and ready;
- for the first test of the session only, the busiest processes every two
  seconds (ps), so a slow step can be set beside what else was running.
"""

import functools
import inspect
import json
import os
import subprocess
import threading
import time

import pytest

_T0 = time.perf_counter()
_OUT = os.environ.get("CI_PROFILE_OUT")
_state = {"test": None, "detail": False, "modules": set(), "first": True}
_REAL_RUN = subprocess.run


def _now() -> float:
    return round(time.perf_counter() - _T0, 4)


def _emit(**row) -> None:
    if not _OUT:
        return
    row.setdefault("t", _now())
    row.setdefault("test", _state["test"])
    with open(_OUT, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def _timed(kind, label, fn):
    """Wrap fn (sync or async) so a detail test records each call."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def w(*a, **k):
            if not _state["detail"]:
                return await fn(*a, **k)
            start = _now()
            t = time.perf_counter()
            try:
                return await fn(*a, **k)
            finally:
                _emit(kind=kind, what=label, start=start,
                      dur=round(time.perf_counter() - t, 4))
        return w

    @functools.wraps(fn)
    def s(*a, **k):
        if not _state["detail"]:
            return fn(*a, **k)
        start = _now()
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            argv = a[0] if a else k.get("args")
            head = argv[:4] if isinstance(argv, (list, tuple)) else str(argv)[:80]
            _emit(kind=kind, what=label, argv=head, start=start,
                  dur=round(time.perf_counter() - t, 4))
    return s


def _wrap_playwright() -> None:
    try:
        from playwright.async_api import _context_manager as cm
        from playwright.async_api._generated import (
            Browser, BrowserContext, BrowserType, Locator, Page)
    except Exception as e:                       # pragma: no cover
        _emit(kind="note", what=f"playwright not wrapped: {e}")
        return
    for name in ("__aenter__", "__aexit__"):
        setattr(cm.PlaywrightContextManager, name,
                _timed("pw", f"driver.{name}", getattr(cm.PlaywrightContextManager, name)))
    for cls in (BrowserType, Browser, BrowserContext, Page, Locator):
        for name, fn in list(vars(cls).items()):
            if name.startswith("_") or not inspect.iscoroutinefunction(fn):
                continue
            setattr(cls, name, _timed("pw", f"{cls.__name__}.{name}", fn))


def _wrap_subprocess() -> None:
    subprocess.run = _timed("proc", "subprocess.run", subprocess.run)
    real_init = subprocess.Popen.__init__

    @functools.wraps(real_init)
    def init(self, *a, **k):
        if _state["detail"]:
            argv = a[0] if a else k.get("args")
            head = argv[:4] if isinstance(argv, (list, tuple)) else str(argv)[:80]
            _emit(kind="proc", what="Popen", argv=head)
        return real_init(self, *a, **k)
    subprocess.Popen.__init__ = init


def _sample_processes(stop: threading.Event) -> None:
    """The busiest processes every two seconds until stop is set."""
    while not stop.is_set():
        try:
            ps = _REAL_RUN(["ps", "-Ao", "pcpu,pmem,etime,comm", "-r"],
                           capture_output=True, text=True, timeout=10)
            _emit(kind="ps", top=ps.stdout.splitlines()[:12])
        except Exception as e:                   # pragma: no cover
            _emit(kind="ps", error=str(e))
        stop.wait(2)


def pytest_configure(config):
    _emit(kind="session", what="configure")
    _wrap_playwright()
    _wrap_subprocess()


def pytest_collection(session):
    _emit(kind="session", what="collection-start")


def pytest_collection_finish(session):
    _emit(kind="session", what="collection-finish", n=len(session.items))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    module = item.nodeid.split("::")[0]
    _state["test"] = item.nodeid
    _state["detail"] = module not in _state["modules"]
    _state["modules"].add(module)
    first = _state["first"]
    _state["first"] = False
    if first and _OUT:
        # Playwright's driver reads DEBUG and DEBUG_FILE when it starts,
        # which is inside this test; restored after it.
        os.environ["DEBUG"] = "pw:browser*"
        os.environ["DEBUG_FILE"] = os.path.join(os.path.dirname(_OUT), "first-test-pw-browser.log")
        stop = threading.Event()
        threading.Thread(target=_sample_processes, args=(stop,), daemon=True).start()
    _emit(kind="test", what="protocol-start", detail=_state["detail"])
    try:
        yield
    finally:
        if first and _OUT:
            stop.set()
            os.environ.pop("DEBUG", None)
            os.environ.pop("DEBUG_FILE", None)
        _emit(kind="test", what="protocol-end")
        _state["test"] = None
        _state["detail"] = False


def _phase(when):
    @pytest.hookimpl(hookwrapper=True)
    def hook(item, *a, **k):
        start = _now()
        t = time.perf_counter()
        yield
        _emit(kind="phase", what=when, start=start, dur=round(time.perf_counter() - t, 4))
    return hook


pytest_runtest_setup = _phase("setup")
pytest_runtest_call = _phase("call")
pytest_runtest_teardown = _phase("teardown")


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    start = _now()
    t = time.perf_counter()
    yield
    _emit(kind="fixture", what=fixturedef.argname, scope=fixturedef.scope,
          start=start, dur=round(time.perf_counter() - t, 4))
