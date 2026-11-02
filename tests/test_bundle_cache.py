"""The derived-output cache (jarvis/bundle_cache.py): keyed by owner,
attachment hash, member, reader version, kind and options; owner-only
files written whole; one owner's output never another's; trimmed least
recently used first."""
from __future__ import annotations

import os
import stat
import time

from jarvis import data_paths
from jarvis.bundle_cache import PARTIAL, CacheKey, DerivedCache

SHA = "ab" + "0" * 62


def key(**changes) -> CacheKey:
    base = dict(server_id="srv", owner_github_id=1, attachment_sha256=SHA, member_id="m1",
                reader="pdf", reader_version=1, kind="text", options="")
    return CacheKey(**{**base, **changes})


def test_a_value_comes_back_and_lives_owner_only_under_the_data_folder():
    cache = DerivedCache()
    cache.put(key(), b"page text")
    assert cache.get(key()) == b"page text"
    path = cache.path_for(key())
    assert path.is_relative_to(data_paths.bundle_cache_dir())
    assert path.parent.name == SHA[:2]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert not any(p.name.startswith(PARTIAL) for p in path.parent.iterdir())


def test_everything_that_decides_the_output_is_in_the_key(tmp_path):
    cache = DerivedCache(tmp_path)
    cache.put(key(), b"v1")
    for changed in (dict(owner_github_id=2), dict(server_id="other"),
                    dict(attachment_sha256="cd" + "0" * 62), dict(member_id="m2"),
                    dict(reader_version=2), dict(kind="render"), dict(options="p2"),
                    dict(reader="text")):
        assert cache.get(key(**changed)) is None, changed


def test_one_owners_output_is_kept_apart_and_forgotten_alone(tmp_path):
    cache = DerivedCache(tmp_path)
    cache.put(key(owner_github_id=1), b"tim's")
    cache.put(key(owner_github_id=2), b"matt's")
    assert not cache.path_for(key(owner_github_id=1)).is_relative_to(
        cache.path_for(key(owner_github_id=2)).parents[1])
    assert cache.forget_owner("srv", 1) == len(b"tim's")
    assert cache.get(key(owner_github_id=1)) is None
    assert cache.get(key(owner_github_id=2)) == b"matt's"


def test_a_server_id_never_makes_a_path(tmp_path):
    cache = DerivedCache(tmp_path)
    path = cache.path_for(key(server_id="../../etc"))
    assert path.is_relative_to(tmp_path) and ".." not in path.parts


def test_scratch_is_private_per_owner(tmp_path):
    cache = DerivedCache(tmp_path)
    one, two = cache.scratch("srv", 1), cache.scratch("srv", 2)
    assert one != two and stat.S_IMODE(one.stat().st_mode) == 0o700


def test_the_least_recently_used_go_first_past_the_limit(tmp_path):
    cache = DerivedCache(tmp_path, max_bytes=350)
    for n in range(3):
        cache.put(key(member_id=f"m{n}"), b"x" * 100)
        os.utime(cache.path_for(key(member_id=f"m{n}")), (1000 + n, 1000 + n))
    cache.get(key(member_id="m0"))           # used now: kept
    cache.put(key(member_id="m3"), b"x" * 100)
    kept = {n for n in range(4) if cache.get(key(member_id=f"m{n}")) is not None}
    assert kept == {0, 2, 3}


def test_trim_removes_what_a_crash_left_behind(tmp_path):
    cache = DerivedCache(tmp_path)
    scratch = cache.scratch("srv", 1)
    old, new = scratch / "member-old", scratch / "member-new"
    for path in (old, new):
        path.write_bytes(b"copy")
    os.utime(old, (time.time() - 7200, time.time() - 7200))
    cache.put(key(), b"kept")
    assert cache.trim() == 4
    assert not old.exists() and new.exists() and cache.get(key()) == b"kept"
