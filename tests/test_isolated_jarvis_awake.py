"""The package check keeps the display awake as well as the Mac: while the
display sleeps under a locked screen the browser's audio clock stops, so a
check's story never gets past its first chunk (2026-11-14)."""
import subprocess

from tests import isolated_jarvis


class _Held:
    def __init__(self, args, **_):
        self.args = args
        self.ended = False

    def poll(self):
        return None if not self.ended else 0

    def terminate(self):
        self.ended = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.ended = True


def test_the_check_holds_the_display_awake_and_wakes_it(monkeypatch):
    started = []
    monkeypatch.setattr(isolated_jarvis.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(isolated_jarvis.subprocess, "Popen",
                        lambda args, **kw: started.append(_Held(args, **kw)) or started[-1])
    with isolated_jarvis._awake():
        pass
    args = started[0].args
    assert args[0] == "/usr/bin/caffeinate"
    for flag in ("-i", "-s", "-d", "-u"):
        assert flag in args
    assert args[args.index("-w") + 1].isdigit()
    assert started[0].ended          # let go once the check ends


def test_no_caffeinate_means_nothing_is_held(monkeypatch):
    monkeypatch.setattr(isolated_jarvis.shutil, "which", lambda name: None)
    called = []
    monkeypatch.setattr(isolated_jarvis.subprocess, "Popen", lambda *a, **k: called.append(a))
    with isolated_jarvis._awake():
        pass
    assert not called
