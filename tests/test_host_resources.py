"""host_resources: whole-machine CPU and memory for the node update."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis import host_resources
from jarvis.host_resources import HostResources, LinuxReader, Reading


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class Scripted:
    """A reader that hands out the readings (or raises the errors) given."""

    def __init__(self, *steps):
        self.steps = list(steps)

    def read(self):
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _sampler(*steps, clock=None):
    reader = Scripted(*steps)
    return HostResources(reader=lambda: reader, clock=clock or Clock())


def test_the_first_sample_has_memory_but_no_cpu_yet():
    s = _sampler(Reading("host", 100, 1000, 4_000, 16_000))
    assert s.current()["state"] == "warming_up"
    s.sample()
    now = s.current()
    assert now == {"state": "warming_up", "scope": "host", "cpu_percent": None,
                   "memory_used_bytes": 4_000, "memory_total_bytes": 16_000, "age_ms": 0}


def test_cpu_is_busy_time_over_all_time_between_two_samples():
    clock = Clock()
    s = _sampler(Reading("host", 100, 1000, 4_000, 16_000),
                 Reading("host", 160, 1200, 5_000, 16_000), clock=clock)
    s.sample()
    clock.now += 5
    s.sample()
    clock.now += 2
    assert s.current() == {"state": "measured", "scope": "host", "cpu_percent": 30.0,
                           "memory_used_bytes": 5_000, "memory_total_bytes": 16_000,
                           "age_ms": 2000}


def test_a_failed_reading_is_kept_as_an_error_not_the_old_figures():
    s = _sampler(Reading("host", 100, 1000, 4_000, 16_000), PermissionError("no"),
                 Reading("host", 200, 1400, 4_500, 16_000))
    s.sample()
    s.sample()
    assert s.current() == {"state": "error", "scope": "host", "cpu_percent": None,
                           "memory_used_bytes": None, "memory_total_bytes": None,
                           "age_ms": 0}
    assert s.last_error == "PermissionError" and s.errors_in_a_row == 1
    s.sample()
    assert s.current()["state"] == "measured" and s.current()["cpu_percent"] == 25.0
    assert s.errors_in_a_row == 0


def test_a_sampler_that_has_stopped_is_not_reported_as_current():
    clock = Clock()
    s = _sampler(Reading("host", 1, 10, 1, 2), Reading("host", 2, 20, 1, 2), clock=clock)
    s.sample(); s.sample()
    clock.now += 61
    assert s.current()["state"] == "error"
    assert s.current()["cpu_percent"] is None and s.current()["age_ms"] == 60_000


def test_a_platform_without_a_reader_says_unsupported():
    s = HostResources(reader=lambda: None)
    s.sample()
    assert s.current()["state"] == "unsupported" and s.current()["cpu_percent"] is None


@pytest.mark.skipif(sys.platform != "darwin", reason="reads this Mac")
def test_this_mac_reports_its_own_cpu_and_memory():
    s = HostResources()
    s.sample()
    first = s.current()
    assert first["state"] == "warming_up" and first["scope"] == "host"
    memsize = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                 text=True, check=True).stdout)
    assert first["memory_total_bytes"] == memsize
    assert 0 < first["memory_used_bytes"] <= memsize
    sum(range(2_000_000))                      # let a few ticks go by
    s.sample()
    now = s.current()
    assert now["state"] in ("measured", "warming_up")   # no tick yet is still honest
    if now["state"] == "measured":
        assert 0.0 <= now["cpu_percent"] <= 100.0


# -- Linux, from files laid out as /proc and /sys/fs/cgroup ------------------

MEMINFO = "MemTotal:       16000000 kB\nMemFree:  1000 kB\nMemAvailable:    6000000 kB\n"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _proc(root: Path, user: int, idle: int) -> None:
    # user nice system idle iowait irq softirq steal guest guest_nice
    _write(root, "proc/stat", f"cpu  {user} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 2 3 4\n")
    _write(root, "proc/meminfo", MEMINFO)


def test_a_linux_host_reads_proc(tmp_path):
    _proc(tmp_path, user=100, idle=900)
    clock = Clock()
    s = HostResources(reader=lambda: LinuxReader(tmp_path), clock=clock)
    s.sample()
    _proc(tmp_path, user=150, idle=1050)
    s.sample()
    now = s.current()
    assert now["scope"] == "host" and now["state"] == "measured"
    assert now["cpu_percent"] == 25.0
    assert now["memory_total_bytes"] == 16_000_000 * 1024
    assert now["memory_used_bytes"] == (16_000_000 - 6_000_000) * 1024


def _container(root: Path, usage_usec: int) -> None:
    _proc(root, user=1, idle=1)
    _write(root, "sys/fs/cgroup/cgroup.controllers", "cpu memory\n")
    _write(root, "sys/fs/cgroup/cpu.max", "200000 100000\n")          # two cores
    _write(root, "sys/fs/cgroup/cpu.stat", f"usage_usec {usage_usec}\nuser_usec 1\n")
    _write(root, "sys/fs/cgroup/memory.max", "2147483648\n")
    _write(root, "sys/fs/cgroup/memory.current", "1073741824\n")
    _write(root, "sys/fs/cgroup/memory.stat", "anon 1\ninactive_file 268435456\n")


def test_a_linux_container_with_limits_reports_its_own_share(tmp_path):
    clock = Clock(500.0)
    _container(tmp_path, usage_usec=1_000_000)
    s = HostResources(reader=lambda: LinuxReader(tmp_path, clock=clock), clock=clock)
    s.sample()
    clock.now += 5                                   # 10 core-seconds of capacity
    _container(tmp_path, usage_usec=3_500_000)       # 2.5 s of it busy
    s.sample()
    now = s.current()
    assert now["scope"] == "container" and now["state"] == "measured"
    assert now["cpu_percent"] == 25.0
    assert now["memory_total_bytes"] == 2 * 1024 ** 3
    assert now["memory_used_bytes"] == 1024 ** 3 - 256 * 1024 ** 2


def test_a_container_without_a_memory_limit_uses_the_host_s_memory(tmp_path):
    _container(tmp_path, usage_usec=1)
    _write(tmp_path, "sys/fs/cgroup/memory.max", "max\n")
    reading = LinuxReader(tmp_path, clock=Clock()).read()
    assert reading.scope == "container"
    assert reading.memory_total == 16_000_000 * 1024


def test_a_cgroup_with_no_limits_is_the_host(tmp_path):
    _container(tmp_path, usage_usec=1)
    _write(tmp_path, "sys/fs/cgroup/memory.max", "max\n")
    _write(tmp_path, "sys/fs/cgroup/cpu.max", "max 100000\n")
    assert LinuxReader(tmp_path).read().scope == "host"


def test_missing_proc_files_are_a_sampler_error(tmp_path):
    s = HostResources(reader=lambda: LinuxReader(tmp_path))
    s.sample()
    assert s.current()["state"] == "error"
    assert s.last_error == "FileNotFoundError"


def test_one_sampler_per_backend():
    assert isinstance(host_resources.sampler, HostResources)
