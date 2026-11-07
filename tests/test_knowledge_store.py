"""The knowledge engine's storage core (build plan step 2): the log, the
segments, the manifest, recovery, snapshots, compaction and forget.

Every test runs in this process on a temporary folder. A crash is a copy of
the folder taken while the store is still open: the bytes the operating
system holds at that moment, which is what a killed process leaves."""
from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

import pytest

from jarvis.knowledge import manifest as mf
from jarvis.knowledge import segment as sg
from jarvis.knowledge import store as st
from jarvis.knowledge import wal as wl
from jarvis.knowledge.store import DOCS, EDGES_OUT, Store, StoreReadOnly
from jarvis.knowledge.wal import Op


def put(key: str, value: str | bytes, family: int = DOCS) -> Op:
    return Op(family, key.encode(), value if isinstance(value, bytes) else value.encode())


def delete(key: str, family: int = DOCS) -> Op:
    return Op(family, key.encode(), None)


def val(store: Store, key: str, at=None, family: int = DOCS):
    v = store.value(family, key.encode(), at)
    return None if v is None else v.decode(errors="replace")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "k")
    yield s
    s.close()


def crash_copy(store: Store, to: Path) -> Path:
    """The folder as a killed process leaves it."""
    shutil.copytree(store.directory, to)
    return to


def reopen(path: Path) -> Store:
    return Store(path)


# --- basics ----------------------------------------------------------------

def test_commit_get_scan_and_versions(store):
    a = store.commit([put("a", "1"), put("b", "1")])
    b = store.commit([put("a", "2"), delete("b")])
    assert val(store, "a") == "2"
    assert val(store, "a", at=a) == "1"
    assert val(store, "b") is None and val(store, "b", at=a) == "1"
    assert [v.key for v in store.scan(DOCS)] == [b"a"]
    assert [v.key for v in store.scan(DOCS, at=a)] == [b"a", b"b"]
    assert b == a + 1


def test_families_are_separate(store):
    store.commit([put("x", "doc"), put("x", "edge", EDGES_OUT)])
    assert val(store, "x") == "doc" and val(store, "x", family=EDGES_OUT) == "edge"


def test_reads_are_the_same_after_flush_and_reopen(tmp_path):
    s = Store(tmp_path / "k")
    first = s.commit([put(f"k{i:04d}", f"v{i}") for i in range(500)])
    s.commit([put("k0007", "changed"), delete("k0008")])
    before = [(v.key, v.value) for v in s.scan(DOCS)]
    s.flush()
    assert [(v.key, v.value) for v in s.scan(DOCS)] == before
    assert val(s, "k0007", at=first) == "v7"
    s.close()
    s = reopen(tmp_path / "k")
    assert [(v.key, v.value) for v in s.scan(DOCS)] == before
    assert val(s, "k0007", at=first) == "v7"
    assert [v.key for v in s.prefix(DOCS, b"k001")] == [f"k{i:04d}".encode() for i in range(10, 20)]
    s.close()


def test_every_acknowledged_lsn_survives_a_crash(store, tmp_path):
    lsns = [store.commit([put(f"k{i}", f"v{i}")]) for i in range(20)]
    copy = reopen(crash_copy(store, tmp_path / "crash"))
    try:
        assert copy.lsn == lsns[-1]
        assert all(val(copy, f"k{i}") == f"v{i}" for i in range(20))
        assert copy.commit([put("after", "x")]) == lsns[-1] + 1
    finally:
        copy.close()


def test_a_flush_deletes_log_files_below_its_checkpoint(store):
    store.commit([put("a", "1")])
    store.flush()
    store.commit([put("b", "1")])
    files = wl.wal_files(store.wal_dir)
    assert len(files) == 1 and int(files[0].stem) > store.manifest.checkpoint_lsn - 1


# --- recovery ----------------------------------------------------------------

def _append(path: Path, *records: bytes) -> None:
    with open(path, "ab") as fh:
        for r in records:
            fh.write(r)


def test_an_uncommitted_transaction_never_appears(store, tmp_path):
    store.commit([put("a", "1")])
    copy = crash_copy(store, tmp_path / "crash")
    log = wl.wal_files(copy / "wal")[-1]
    _append(log, wl.encode(99, wl.TXN_BEGIN, b""),
            wl.encode(99, wl.PUT, wl.op_payload(put("ghost", "boo"))))
    s = reopen(copy)
    try:
        assert val(s, "ghost") is None and val(s, "a") == "1"
    finally:
        s.close()


def _txn_bounds(log: Path) -> list[tuple[int, int, int]]:
    """(lsn, start, end) of each transaction in a log file."""
    out, start = [], None
    data = memoryview(log.read_bytes())
    pos = 0
    while pos < len(data):
        rec = wl._record_at(data, pos)
        if rec.rtype == wl.TXN_BEGIN:
            start = rec.offset
        if rec.rtype == wl.TXN_COMMIT:
            out.append((rec.lsn, start, rec.end))
        pos = rec.end
    return out


def test_the_log_cut_at_every_byte_of_its_last_record_recovers(store, tmp_path):
    store.commit([put("a", "first")])
    store.commit([put("b", "second" * 3)])
    base = crash_copy(store, tmp_path / "base")
    log = wl.wal_files(base / "wal")[-1]
    (_, _, _), (lsn2, start, end) = _txn_bounds(log)
    for cut in range(start, end + 1):
        trial = tmp_path / f"cut-{cut}"
        shutil.copytree(base, trial)
        os.truncate(wl.wal_files(trial / "wal")[-1], cut)
        s = reopen(trial)
        try:
            assert s.writable, cut
            assert val(s, "a") == "first"
            assert val(s, "b") == ("second" * 3 if cut == end else None), cut
            nxt = s.commit([put("c", "x")])
            assert val(s, "c") == "x"
        finally:
            s.close()
        shutil.rmtree(trial)


def test_a_bad_record_past_the_durable_mark_is_cut_as_tail_even_with_good_records_after(store, tmp_path):
    store.commit([put("a", "1")])
    store.commit([put("b", "2")])
    store.commit([put("c", "3")])
    copy = crash_copy(store, tmp_path / "crash")
    log = wl.wal_files(copy / "wal")[-1]
    (_, _, _), (_, start2, end2), (_, _, end3) = _txn_bounds(log)
    data = bytearray(log.read_bytes())
    data[start2 + wl.HEADER.size + 2] ^= 0xFF        # inside txn 2's begin record header area
    data[end2 - 3] ^= 0xFF                            # and its commit
    log.write_bytes(bytes(data))
    s = reopen(copy)
    try:
        assert s.writable and s.state == "ok"
        assert val(s, "a") == "1" and val(s, "b") is None and val(s, "c") is None
        assert log.stat().st_size < end3
    finally:
        s.close()


def test_a_bad_record_at_or_below_the_durable_mark_is_corruption(store, tmp_path):
    store.commit([put("a", "1")])
    store.commit([put("b", "2")])
    with store.wal.exclusive():
        store.wal.barrier()
    store.commit([put("c", "3")])
    copy = crash_copy(store, tmp_path / "crash")
    log = wl.wal_files(copy / "wal")[-1]
    (_, start1, end1), _, _ = _txn_bounds(log)
    data = bytearray(log.read_bytes())
    data[end1 - 2] ^= 0xFF
    log.write_bytes(bytes(data))
    s = reopen(copy)
    try:
        assert s.state == "degraded" and not s.writable
        with pytest.raises(StoreReadOnly):
            s.commit([put("d", "4")])
    finally:
        s.close()


def test_a_clean_close_leaves_a_durable_mark(tmp_path):
    s = Store(tmp_path / "k")
    last = s.commit([put("a", "1")])
    s.close()
    found = wl.scan(tmp_path / "k" / "wal")
    assert found.durable_lsn == last


def test_a_corrupt_manifest_falls_back_to_the_previous(tmp_path):
    s = Store(tmp_path / "k")
    s.commit([put("a", "1")])
    s.flush()
    s.commit([put("b", "2")])
    s.flush()
    number = s.manifest.number
    s.close()
    path = mf.manifest_path(tmp_path / "k", number)
    path.write_bytes(path.read_bytes().replace(b'"checkpoint_lsn"', b'"checkpoint_lsx"'))
    s = reopen(tmp_path / "k")
    try:
        assert s.manifest.number == number - 1
        assert any("checksum" in p for p in s.problems)
        assert val(s, "a") == "1"
    finally:
        s.close()


def test_a_newer_format_is_left_untouched(tmp_path):
    d = tmp_path / "k"
    d.mkdir()
    (d / "FORMAT").write_text("jkn 99\nabc\n")
    with pytest.raises(mf.FormatTooNew):
        Store(d)
    assert (d / "FORMAT").read_text() == "jkn 99\nabc\n"
    assert not (d / "CURRENT").exists()


def test_engine_id_is_kept_and_new_per_directory(tmp_path):
    a = Store(tmp_path / "a")
    ida = a.engine_id
    a.close()
    b = Store(tmp_path / "b")
    assert b.engine_id != ida
    b.close()
    assert Store(tmp_path / "a").engine_id == ida


def test_a_segment_that_fails_its_hash_reads_from_a_snapshot(tmp_path):
    s = Store(tmp_path / "k")
    s.commit([put("a", "1")])
    s.snapshot()
    s.commit([put("b", "2")])
    s.flush()
    newest = s.manifest.segments[-1]["file"]
    s.close()
    seg = tmp_path / "k" / "seg" / newest
    data = bytearray(seg.read_bytes())
    data[20] ^= 0xFF
    seg.write_bytes(bytes(data))
    s = reopen(tmp_path / "k")
    try:
        assert s.state == "degraded" and not s.writable
        assert val(s, "a") == "1"
    finally:
        s.close()


# --- segments ----------------------------------------------------------------

def test_segment_round_trip_bloom_and_block_crc(tmp_path):
    versions = [sg.Version(f"key{i:05d}".encode(), 10, (b"x" * (i % 300)) if i % 7 else None)
                for i in range(3000)]
    path = tmp_path / "s.docs"
    entry = sg.write_segment(path, DOCS, 1, versions)
    assert entry["count"] == 3000 and entry["sha256"] == sg.file_sha256(path)
    seg = sg.Segment(path, sg.BlockPool(), expect_sha256=entry["sha256"])
    assert len(seg.first_keys) > 1
    assert seg.versions(b"key00007") == [versions[7]]
    assert seg.versions(b"key00014")[0].value is None
    assert seg.versions(b"nope") == []
    assert [v.key for v in seg.scan(b"key00100", b"key00105")] == [f"key{i:05d}".encode() for i in range(100, 105)]
    misses = sum(seg.might_have(f"absent{i}".encode()) for i in range(2000))
    assert misses < 60   # about 1 percent at 10 bits per key
    seg.close()
    data = bytearray(path.read_bytes())
    data[sg.HEAD.size + sg.BLOCK_HEAD.size + 5] ^= 0xFF
    path.write_bytes(bytes(data))
    bad = sg.Segment(path, sg.BlockPool())
    with pytest.raises(sg.SegmentCorrupt):
        bad.block(0)
    bad.close()
    with pytest.raises(sg.SegmentCorrupt):
        sg.Segment(path, sg.BlockPool(), expect_sha256=entry["sha256"])


def test_block_pool_keeps_the_hot_set_through_a_long_scan():
    pool = sg.BlockPool(budget_bytes=4000)
    pool.put(("f", 0), [], 500)
    assert pool.get(("f", 0)) is not None       # read again: protected
    for i in range(1, 100):
        pool.put(("f", i), [], 500)              # a long scan through probation
    assert pool.get(("f", 0)) is not None
    assert pool.size <= 4000


# --- snapshots, compaction --------------------------------------------------

def test_snapshot_links_segments_and_keeps_three(store):
    for i in range(5):
        store.commit([put(f"k{i}", "v")])
        snap = store.snapshot()
        assert (snap / "MANIFEST").exists()
    names = [p.name for p in store.snapshots()]
    assert len(names) == 3
    seg = next(p for p in store.snapshots()[-1].iterdir() if p.name != "MANIFEST")
    assert seg.stat().st_nlink >= 2


def test_compaction_keeps_what_a_pinned_read_needs_and_drops_the_rest(store):
    first = store.commit([put("a", "1"), put("gone", "x")])
    store.flush()
    store.commit([put("a", "2")])
    store.flush()
    with store.reading(first) as pinned:
        store.commit([put("a", "3"), delete("gone")])
        store.flush()
        gens = store.manifest.generations()
        store.compact(gens)
        assert val(store, "a", at=pinned) == "1"
    assert val(store, "a") == "3" and val(store, "gone") is None
    assert len(store.manifest.generations()) == 1
    # Unpinned now: a second compaction keeps only the newest of each key
    # and drops the tombstone, since the oldest generation is in it.
    store.compact(store.manifest.generations())
    seg_versions = [v for _, seg in store.segments[DOCS] for v in seg.scan()]
    assert [(v.key, v.value) for v in seg_versions] == [(b"a", b"3")]


def test_size_tiered_pick(store):
    for i in range(st.TIER_FANOUT):
        store.commit([put(f"k{i}", "v")])
        store.flush()
    gens = store.pick_tier()
    assert gens == store.manifest.generations()[:st.TIER_FANOUT]
    store.compact()
    assert len(store.manifest.generations()) == 1
    assert all(val(store, f"k{i}") == "v" for i in range(st.TIER_FANOUT))


def test_compacted_files_are_deleted(store):
    for i in range(2):
        store.commit([put(f"k{i}", "v")])
        store.flush()
    old = {e["file"] for e in store.manifest.segments}
    store.compact(store.manifest.generations())
    left = {p.name for p in store.seg_dir.iterdir()}
    assert not (old & left)


# --- forget -------------------------------------------------------------------

def _all_bytes(directory: Path) -> bytes:
    out = bytearray()
    for root, _, files in os.walk(directory):
        for name in files:
            out += Path(root, name).read_bytes()
    return bytes(out)


def test_forget_leaves_no_copy_anywhere(store):
    secret = b"SECRET:" + os.urandom(48)
    store.commit([put("keep", "public"), put("doc:private", secret),
                  put("doc:private", b"edge" + secret, EDGES_OUT)])
    store.flush()
    store.snapshot()
    store.commit([put("doc:private", secret + b"v2")])     # in the memtable and the log
    store.commit([put("other", "x")])
    assert secret in _all_bytes(store.directory)
    report = store.forget({DOCS: {b"doc:private"}, EDGES_OUT: {b"doc:private"}})
    assert report["snapshots_removed"]
    assert secret not in _all_bytes(store.directory)
    assert val(store, "doc:private") is None and val(store, "keep") == "public"
    assert val(store, "other") == "x"
    store.close()
    again = Store(store.directory)
    try:
        assert again.value(DOCS, b"doc:private") is None
        assert again.value(DOCS, b"keep") == b"public"
    finally:
        again.close()


def test_forget_overrides_a_pin(store):
    first = store.commit([put("p", "secret-old")])
    store.flush()
    with store.reading(first):
        store.commit([put("p", "secret-new")])
        store.forget({DOCS: {b"p"}})
        assert store.versions(DOCS, b"p") == []


# --- the compaction child, and the pace (real processes) -------------------

@pytest.mark.knowledge_engine
def test_compaction_in_a_child_process(tmp_path):
    s = Store(tmp_path / "k", compact_in_child=True)
    try:
        for i in range(4):
            s.commit([put(f"k{i}", f"v{i}")])
            s.flush()
        s.compact()
        assert len(s.manifest.generations()) == 1
        assert [val(s, f"k{i}") for i in range(4)] == [f"v{i}" for i in range(4)]
    finally:
        s.close()


@pytest.mark.knowledge_engine
def test_a_stopped_compaction_child_holds_up_no_read(tmp_path):
    import threading
    import time
    s = Store(tmp_path / "k", compact_in_child=True)
    try:
        for i in range(4):
            s.commit([put(f"k{i}", "v" * 1000)])
            s.flush()
        s.set_paused(True)
        done = threading.Event()
        threading.Thread(target=lambda: (s.compact(), done.set()), daemon=True).start()
        end = time.monotonic() + 10
        while s.child is None and time.monotonic() < end:
            time.sleep(0.01)
        t0 = time.monotonic()
        assert val(s, "k2") == "v" * 1000
        s.commit([put("during", "yes")])
        assert time.monotonic() - t0 < 2
        assert not done.is_set()
        s.set_paused(False)
        assert done.wait(30)
        assert val(s, "during") == "yes"
    finally:
        s.close()
