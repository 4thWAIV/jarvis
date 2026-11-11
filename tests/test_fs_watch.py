"""fs_watch: the FSEvents watcher (real FSEvents on macOS), the backup
scan, git-ignored skipping and when a backup scan is due."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time

import pytest

from jarvis import fs_watch
from jarvis.fs_watch import Event, ScanClock, Stat, scan, stat_of

WAIT_SEC = 5.0

mac_only = pytest.mark.skipif(not fs_watch.available(),
                              reason="FSEvents needs macOS with CoreServices")


class Seen:
    """Collects batches from a watcher and waits for a condition."""

    def __init__(self):
        self.events: list[Event] = []
        self.cond = threading.Condition()

    def __call__(self, events):
        with self.cond:
            self.events.extend(events)
            self.cond.notify_all()

    def wait_for(self, test, timeout=WAIT_SEC) -> float:
        began = time.monotonic()
        with self.cond:
            ok = self.cond.wait_for(lambda: any(test(e) for e in self.events), timeout)
        assert ok, f"not seen within {timeout}s: {self.events}"
        return time.monotonic() - began


@pytest.fixture
def root(tmp_path):
    return fs_watch.canonical(str(tmp_path))


@pytest.fixture
def watching(root):
    seen = Seen()
    watcher = fs_watch.Watcher([root], seen, latency=0.05)
    watcher.start()
    time.sleep(0.1)      # the stream is live before anything changes
    yield seen, watcher
    watcher.stop()


@mac_only
def test_create_change_rename_delete_are_each_seen(root, watching):
    seen, watcher = watching
    a, b = os.path.join(root, "a.txt"), os.path.join(root, "b.txt")
    measured = {}

    with open(a, "w") as f:
        f.write("one")
    measured["create"] = seen.wait_for(lambda e: e.path == a and e.created and e.is_file)

    began = time.monotonic()
    with open(a, "a") as f:
        f.write("two")
    seen.wait_for(lambda e: e.path == a and e.modified and e.event_id > 0
                  and e.event_id > _first_id(seen, a))
    measured["change"] = time.monotonic() - began

    began = time.monotonic()
    os.rename(a, b)
    seen.wait_for(lambda e: e.path == b and e.renamed)
    measured["rename"] = time.monotonic() - began

    began = time.monotonic()
    os.remove(b)
    seen.wait_for(lambda e: e.path == b and e.removed)
    measured["delete"] = time.monotonic() - began

    print("watch latency (s):", {k: round(v, 3) for k, v in measured.items()})
    assert all(v < WAIT_SEC for v in measured.values())
    assert watcher.last_event_id == max(e.event_id for e in seen.events)


def _first_id(seen, path):
    return min(e.event_id for e in seen.events if e.path == path)


@mac_only
def test_roots_are_canonical(tmp_path):
    # /var is a link to /private/var: the watcher's roots and paths are real
    watcher = fs_watch.Watcher([str(tmp_path) + "/"], lambda _e: None)
    assert watcher.roots == [os.path.realpath(str(tmp_path))]


@mac_only
def test_replay_from_last_event_id(root):
    first = Seen()
    watcher = fs_watch.Watcher([root], first, latency=0.05)
    watcher.start()
    marker = os.path.join(root, "marker")
    open(marker, "w").close()
    first.wait_for(lambda e: e.path == marker)
    watcher.stop()
    since = watcher.last_event_id
    assert since > 0

    away = os.path.join(root, "while-away.txt")
    with open(away, "w") as f:
        f.write("changed while no watcher ran")

    again = Seen()
    replay = fs_watch.Watcher([root], again, since=since, latency=0.05)
    replay.start()
    try:
        again.wait_for(lambda e: e.path == away and e.created)
        again.wait_for(lambda e: e.history_done)
        assert all(e.event_id > since for e in again.events if not e.history_done)
    finally:
        replay.stop()


@mac_only
def test_restart_with_new_roots(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    seen = Seen()
    watcher = fs_watch.Watcher([str(one)], seen, latency=0.05)
    watcher.start()
    try:
        watcher.restart([str(one), str(two)])
        target = os.path.join(fs_watch.canonical(str(two)), "new.txt")
        open(target, "w").close()
        seen.wait_for(lambda e: e.path == target)
    finally:
        watcher.stop()


def _fake_batch(watcher, events):
    """Deliver events through the C callback path, as CoreServices would."""
    count = len(events)
    names = (ctypes.c_char_p * count)(*[e.path.encode() for e in events])
    flags = (ctypes.c_uint32 * count)(*[e.flags for e in events])
    ids = (ctypes.c_uint64 * count)(*[e.event_id for e in events])
    watcher._callback(None, None, count, ctypes.cast(names, ctypes.c_void_p), flags, ids)


def test_dropped_events_ask_for_a_scan_of_that_subtree(tmp_path):
    root = fs_watch.canonical(str(tmp_path))
    (tmp_path / "docs" / "deep").mkdir(parents=True)
    (tmp_path / "docs" / "deep" / "x.md").write_text("x")
    asked = []

    def consumer(events):
        for where, under in fs_watch.scan_requests(events, [root]):
            asked.append((where, under, scan(where, {}, under=under).added))

    watcher = fs_watch.Watcher([root], consumer)
    _fake_batch(watcher, [Event(root + "/docs", fs_watch.MUST_SCAN_SUBDIRS, 41),
                          Event(root + "/docs/deep", fs_watch.KERNEL_DROPPED, 42),
                          Event(root + "/other.txt", fs_watch.ITEM_CREATED | fs_watch.ITEM_IS_FILE, 43)])
    assert asked == [(root, "docs", ["docs/deep/x.md"])]
    assert watcher.last_event_id == 43


def test_a_failing_consumer_never_raises_and_is_not_marked_handled(tmp_path):
    def broken(_events):
        raise RuntimeError("boom")

    watcher = fs_watch.Watcher([str(tmp_path)], broken, since=7)
    _fake_batch(watcher, [Event(str(tmp_path) + "/a", fs_watch.ITEM_CREATED, 50)])
    assert watcher.last_event_id == 7


def test_root_changed_asks_for_the_whole_root(tmp_path):
    root = fs_watch.canonical(str(tmp_path))
    event = Event(root, fs_watch.ROOT_CHANGED, 9)
    assert event.root_changed and not event.must_scan
    assert fs_watch.scan_requests([event], [root]) == [(root, "")]


# ── the backup scan ──────────────────────────────────────────────────────

def _known(root):
    return {rel: stat_of(os.path.join(root, rel)) for rel in _files(root)}


def _files(root):
    out = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(dirpath, d))]
        for n in names:
            p = os.path.join(dirpath, n)
            if not os.path.islink(p):
                out.append(os.path.relpath(p, root).replace(os.sep, "/"))
    return out


def test_scan_finds_what_changed_while_no_watcher_ran(tmp_path):
    root = str(tmp_path)
    (tmp_path / "keep.txt").write_text("same")
    (tmp_path / "grow.txt").write_text("a")
    (tmp_path / "touch.txt").write_text("t")
    (tmp_path / "swap.txt").write_text("s")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "gone.txt").write_text("g")
    known = _known(root)

    (tmp_path / "grow.txt").write_text("abc")                            # size
    st = os.stat(tmp_path / "touch.txt")
    os.utime(tmp_path / "touch.txt", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))   # mtime
    (tmp_path / "swap.new").write_text("s")                              # inode
    os.utime(tmp_path / "swap.new", ns=(os.stat(tmp_path / "swap.txt").st_atime_ns,
                                        known["swap.txt"].mtime_ns))
    os.replace(tmp_path / "swap.new", tmp_path / "swap.txt")
    (tmp_path / "sub" / "gone.txt").unlink()
    (tmp_path / "sub" / "new.txt").write_text("n")

    result = scan(root, known)
    assert result.added == ["sub/new.txt"]
    assert result.changed == ["grow.txt", "swap.txt", "touch.txt"]
    assert result.removed == ["sub/gone.txt"]
    assert result.complete and result.seconds >= 0


def test_scan_under_limits_to_a_subtree(tmp_path):
    root = str(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "1").write_text("1")
    (tmp_path / "b" / "2").write_text("2")
    known = {"a/old": Stat(1, 1, 1), "b/old": Stat(1, 1, 1)}
    result = scan(root, known, under="a")
    assert result.added == ["a/1"]
    assert result.removed == ["a/old"]


def test_scan_never_follows_a_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    root = tmp_path / "root"
    root.mkdir()
    (root / "real.txt").write_text("r")
    os.symlink(outside, root / "linkdir")
    os.symlink(outside / "secret.txt", root / "linkfile")
    result = scan(str(root), {})
    assert result.added == ["real.txt"]
    assert result.skipped == 2
    assert stat_of(root / "linkfile") is None


def test_scan_skips_git_and_what_the_caller_skips(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")
    (tmp_path / ".env").write_text("KEY=1")
    (tmp_path / "ok.md").write_text("ok")
    result = scan(str(tmp_path), {"gone.md": Stat(1, 1, 1)},
                  skip=lambda rel, _d: rel == ".env")
    assert result.added == ["ok.md"]
    assert result.removed == ["gone.md"]
    assert result.skipped == 2


def test_an_unreadable_folder_reports_nothing_removed_under_it(tmp_path):
    (tmp_path / "locked").mkdir()
    (tmp_path / "locked" / "f").write_text("f")
    known = _known(str(tmp_path))
    os.chmod(tmp_path / "locked", 0)
    try:
        if os.access(tmp_path / "locked", os.R_OK):
            pytest.skip("running with permission to read anything")
        assert scan(str(tmp_path), known).removed == []
    finally:
        os.chmod(tmp_path / "locked", 0o755)


def test_a_spent_budget_reports_nothing_removed(tmp_path):
    (tmp_path / "x").write_text("x")
    result = scan(str(tmp_path), {"gone": Stat(1, 1, 1)}, budget_sec=-1)
    assert not result.complete and result.removed == []


# ── git-ignored paths ────────────────────────────────────────────────────

def test_git_ignored_with_a_faked_runner(tmp_path, monkeypatch):
    calls = []

    def fake(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=b"build/\0notes.log\0", stderr=b"")

    monkeypatch.setattr(fs_watch, "RUNNER", fake)
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.o").write_text("o")
    (tmp_path / "notes.log").write_text("l")
    (tmp_path / "src.py").write_text("s")
    skip = fs_watch.git_ignored(str(tmp_path))
    assert calls == [["-C", fs_watch.canonical(str(tmp_path)), "ls-files", "--others", "--ignored",
                      "--exclude-standard", "--directory", "-z"]]
    assert skip("build", True) and skip("build/out.o", False) and skip("notes.log", False)
    assert skip(".git", True) and not skip("src.py", False)
    assert scan(str(tmp_path), {}, skip=skip).added == ["src.py"]


def test_outside_a_work_tree_nothing_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(fs_watch, "RUNNER", lambda args: subprocess.CompletedProcess(
        args, 128, stdout=b"", stderr=b"fatal: not a git repository"))
    skip = fs_watch.git_ignored(str(tmp_path))
    assert not skip("anything", False) and skip(".git", True)
    monkeypatch.setattr(fs_watch, "RUNNER", lambda args: None)      # no git here
    assert not fs_watch.git_ignored(str(tmp_path))("anything", False)


# ── when a backup scan is due ────────────────────────────────────────────

def test_scan_clock_start_wake_interval():
    clock = ScanClock()
    wall, mono = 1_000_000.0, 500.0
    assert clock.due(wall, mono) == "start"
    assert clock.due(wall + 10, mono + 10) is None
    # asleep for an hour: the wall clock moves on, the monotonic clock does not
    assert clock.due(wall + 3610, mono + 20) == "wake"
    assert clock.due(wall + 3620, mono + 30) is None
    later = fs_watch.BACKUP_EVERY_SEC
    assert clock.due(wall + 3620 + later, mono + 30 + later) == "interval"
    assert clock.due(wall + 3630 + later, mono + 40 + later) is None


def test_off_macos_watching_is_not_available(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert fs_watch.available() is False
