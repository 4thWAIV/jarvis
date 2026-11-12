"""folder_readers' Windows walk, natively: junctions and symlinks present
or swapped in mid-walk, 8.3 and case aliases, names Windows reads as
something else, long paths, budgets, locked files, the registered root's
identity, hard links, and the held walk draft sources open by. macOS keeps
the POSIX tests in test_folders.py, unchanged."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis import folder_readers
from jarvis.folder_contract import FolderError

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' own walk")


def _long(path: Path) -> str:
    return "\\\\?\\" + os.path.abspath(path)


def _identity(path: Path) -> str:
    info = os.stat(path)
    return f"{info.st_dev}:{info.st_ino}"


def _code(call) -> str:
    with pytest.raises(FolderError) as refused:
        call()
    return refused.value.code


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "inside.txt").write_bytes(b"inside\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "inside.txt").write_bytes(b"OUTSIDE SECRET\n")
    return root, outside


def _window_text(window) -> str:
    return "\n".join(window.lines)


# ── links present and swapped in ─────────────────────────────────────────

def test_a_junction_inside_is_listed_as_a_link_and_never_followed(tree):
    import _winapi
    root, outside = tree
    _winapi.CreateJunction(str(outside), str(root / "jn"))
    kinds = {e.name: e.kind for e in folder_readers.list_dir(root, Path(),
                                                             lambda r, d: True).entries}
    assert kinds["jn"] == "link"
    assert folder_readers.kind_of(root, Path("jn")) == "link"
    assert _code(lambda: folder_readers.read_text(root, Path("jn/inside.txt"))) == "forbidden"
    assert _code(lambda: folder_readers.list_dir(root, Path("jn"), lambda r, d: True)) \
        == "forbidden"


def test_a_file_symlink_inside_is_a_link_and_never_read(tree):
    root, outside = tree
    try:
        os.symlink(outside / "inside.txt", root / "ln.txt")
    except OSError:
        pytest.skip("symbolic links need elevation or developer mode")
    assert folder_readers.kind_of(root, Path("ln.txt")) == "link"
    assert _code(lambda: folder_readers.read_text(root, Path("ln.txt"))) == "forbidden"


def test_a_junction_swapped_in_mid_walk_never_yields_outside_bytes(tree, monkeypatch):
    """Paused between components: the next folder is renamed away and a
    junction to the outside put in its place before it is opened."""
    import _winapi
    from jarvis import _win_handles
    root, outside = tree
    real = _win_handles.open_child_dir
    swapped = []

    def swap_then_open(parent, name, exact=True):
        if name == "sub" and not swapped:
            os.rename(root / "sub", root / "sub-away")
            _winapi.CreateJunction(str(outside), str(root / "sub"))
            swapped.append(name)
        return real(parent, name, exact)

    monkeypatch.setattr(_win_handles, "open_child_dir", swap_then_open)
    assert _code(lambda: folder_readers.read_text(root, Path("sub/inside.txt"))) in (
        "forbidden", "missing")
    assert swapped == ["sub"]


def test_a_root_that_is_a_junction_is_refused(tree, tmp_path):
    import _winapi
    _root, outside = tree
    _winapi.CreateJunction(str(outside), str(tmp_path / "jroot"))
    assert _code(lambda: folder_readers.list_dir(tmp_path / "jroot", Path(),
                                                 lambda r, d: True)) == "forbidden"


# ── one file, one accepted name ──────────────────────────────────────────

def test_case_variants_and_short_names_are_not_taken(tmp_path):
    root = tmp_path / "root"
    (root / ".ssh").mkdir(parents=True)
    (root / ".ssh" / "id_rsa").write_bytes(b"key\n")
    (root / "credentials.json").write_bytes(b"{}\n")
    assert folder_readers.kind_of(root, Path(".ssh")) == "dir"
    assert _code(lambda: folder_readers.kind_of(root, Path(".SSH"))) == "missing"
    assert _code(lambda: folder_readers.read_text(root, Path(".SSH/id_rsa"))) == "missing"
    assert _code(lambda: folder_readers.read_text(root, Path("CREDENTIALS.JSON"))) == "missing"
    listing = subprocess.run(["cmd", "/c", "dir", "/x", str(root)], capture_output=True,
                             text=True).stdout
    short = [line.split()[-2] for line in listing.splitlines()
             if line.rstrip().endswith("credentials.json") and len(line.split()) >= 5]
    if not short or "~" not in short[0]:
        pytest.skip("8.3 names are not made on this volume")
    assert _code(lambda: folder_readers.read_text(root, Path(short[0]))) == "missing"
    assert _window_text(folder_readers.read_text(root, Path("credentials.json"))) == "{}"


def test_names_windows_reads_as_something_else_are_listed_as_other_and_never_opened(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for name in ("aux.txt", "a.", "trail "):
        with open(_long(root) + "\\" + name, "wb") as f:     # abspath would strip "a."
            f.write(b"hazard\n")
    (root / "plain.txt").write_bytes(b"plain\n")
    with open(root / "plain.txt:stream", "wb") as f:
        f.write(b"hidden stream\n")
    kinds = {e.name: e.kind for e in folder_readers.list_dir(root, Path(),
                                                             lambda r, d: True).entries}
    assert kinds == {"aux.txt": "other", "a.": "other", "trail ": "other", "plain.txt": "file"}
    for name in ("aux.txt", "a.", "trail ", "plain.txt:stream", "CON", "nul"):
        assert _code(lambda: folder_readers.read_text(root, Path(name))) == "forbidden", name
    assert folder_readers.kind_of(root, Path("aux.txt")) == "other"
    assert _code(lambda: folder_readers.kind_of(root, Path("lpt1"))) == "missing"
    assert _window_text(folder_readers.read_text(root, Path("plain.txt"))) == "plain"


# ── long paths, budgets, locks ───────────────────────────────────────────

def test_a_path_past_max_path_inside_a_folder_is_read(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    rel = Path(*["d" * 60 + str(i) for i in range(5)]) / "deep.txt"
    assert len(str(root / rel)) > 260
    os.makedirs(_long(root / rel.parent))
    with open(_long(root / rel), "wb") as f:
        f.write(b"far down\n")
    assert _window_text(folder_readers.read_text(root, rel)) == "far down"
    assert [e.name for e in folder_readers.list_dir(root, rel.parent,
                                                    lambda r, d: True).entries] == ["deep.txt"]


def test_a_large_folder_is_looked_at_only_up_to_the_scan_budget(tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(12_000):
        open(root / f"f{i:05}", "wb").close()
    listing = folder_readers.list_dir(root, Path(), lambda r, d: True)
    assert listing.partial and listing.scanned == 10_000
    assert len(listing.entries) == 100


def test_a_large_file_is_read_one_bounded_window_at_an_offset(tmp_path, monkeypatch):
    from jarvis.folder_contract import TEXT_MAX_BYTES
    root = tmp_path / "root"
    root.mkdir()
    line = b"0123456789" * 9 + b"\n"
    with open(root / "big.log", "wb") as f:
        for _ in range(100):
            f.write(line * 10_000)                  # 100 MB in all
    asked = []
    real = folder_readers.pread
    monkeypatch.setattr(folder_readers, "pread",
                        lambda fd, n, at: asked.append(n) or real(fd, n, at))
    window = folder_readers.read_text(root, Path("big.log"), offset=50_000_000)
    assert max(asked) <= TEXT_MAX_BYTES and window.lines
    assert window.offset == 50_000_000 and window.next_offset > window.offset


def test_a_file_held_without_read_sharing_is_access_denied(tmp_path):
    import _winapi
    root = tmp_path / "root"
    root.mkdir()
    (root / "locked.txt").write_bytes(b"locked\n")
    handle = _winapi.CreateFile(str(root / "locked.txt"), _winapi.GENERIC_READ, 0, 0,
                                _winapi.OPEN_EXISTING, 0, 0)
    try:
        assert _code(lambda: folder_readers.read_text(root, Path("locked.txt"))) \
            == "access_denied"
    finally:
        _winapi.CloseHandle(handle)


# ── the registered root, and hard links ──────────────────────────────────

def test_the_held_root_must_be_the_directory_registered(tree, tmp_path):
    """Another ordinary directory put at the registered path, holding a file
    of the same name, is refused: the reader never reads it as the folder."""
    root, _outside = tree
    recorded = _identity(root)
    assert _window_text(folder_readers.read_text(root, Path("sub/inside.txt"),
                                                 identity=recorded)) == "inside"
    os.rename(root, tmp_path / "registered-moved")
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "inside.txt").write_bytes(b"IMPOSTOR\n")
    for read in (lambda: folder_readers.read_text(root, Path("sub/inside.txt"),
                                                  identity=recorded),
                 lambda: folder_readers.list_dir(root, Path(), lambda r, d: True,
                                                 identity=recorded),
                 lambda: folder_readers.kind_of(root, Path("sub"), identity=recorded),
                 lambda: folder_readers.open_file(root, Path("sub/inside.txt"),
                                                  identity=recorded)):
        assert _code(read) == "identity_mismatch"


def test_a_hard_link_to_a_file_outside_or_filtered_is_not_read(tree):
    root, outside = tree
    os.link(outside / "inside.txt", root / "looks-fine.txt")
    (root / ".env").write_bytes(b"SECRET=1\n")
    os.link(root / ".env", root / "notes.txt")
    for name in ("looks-fine.txt", "notes.txt"):
        assert _code(lambda: folder_readers.read_text(root, Path(name))) == "forbidden"
        assert _code(lambda: folder_readers.open_file(root, Path(name))) == "forbidden"


# ── draft sources: the original path held through the read ──────────────

def test_a_named_file_is_walked_from_its_drive_and_held(tree):
    root, _outside = tree
    named = root / "SUB" / "Inside.TXT"
    fd, info, held = folder_readers.open_named_file(named)
    try:
        assert folder_readers.pread(fd, 100, 0) == b"inside\n"
        assert held == Path(os.path.realpath(root / "sub" / "inside.txt"))
        assert held.name == "inside.txt" and held.parent.name == "sub"
    finally:
        os.close(fd)


def test_a_junction_on_the_named_path_is_refused_not_resolved(tree, tmp_path):
    import _winapi
    _root, outside = tree
    _winapi.CreateJunction(str(outside), str(tmp_path / "via"))
    assert _code(lambda: folder_readers.open_named_file(tmp_path / "via" / "inside.txt")) \
        == "forbidden"


def test_draft_sources_read_through_the_held_walk(tree, tmp_path, monkeypatch):
    import _winapi
    from jarvis import draft_files
    root, outside = tree
    monkeypatch.setattr(draft_files, "_allowed", lambda path, doing: path)
    source = draft_files.Source.open(str(root / "sub" / "inside.txt"))
    try:
        assert folder_readers.pread(source._fd, 100, 0) == b"inside\n"
        assert source.path == Path(os.path.realpath(root / "sub" / "inside.txt"))
    finally:
        source.close()
    _winapi.CreateJunction(str(outside), str(tmp_path / "via"))
    with pytest.raises(draft_files.NotAttached, match="link"):
        draft_files.Source.open(str(tmp_path / "via" / "inside.txt"))


# ── review follow-ups ────────────────────────────────────────────────────

def test_a_name_outside_the_bmp_is_read_as_itself_and_never_as_a_sibling(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "x\U0001F600.txt").write_bytes(b"emoji\n")
    (root / "x\U0001F600.tx").write_bytes(b"SIBLING\n")
    assert _window_text(folder_readers.read_text(root, Path("x\U0001F600.txt"))) == "emoji"
    fd, _info, held = folder_readers.open_named_file(root / "x\U0001F600.txt")
    try:
        assert folder_readers.pread(fd, 100, 0) == b"emoji\n" and held.name == "x\U0001F600.txt"
    finally:
        os.close(fd)


def test_a_named_file_on_a_share_is_refused(tree):
    root, _outside = tree
    unc = Path("\\\\localhost\\" + str(root).replace(":", "$", 1)) / "sub" / "inside.txt"
    assert unc.anchor.startswith("\\\\localhost\\") and len(unc.anchor) > 12, unc.anchor
    assert _code(lambda: folder_readers.open_named_file(unc)) == "forbidden"


def test_an_offline_file_is_listed_as_other_and_not_read(tmp_path):
    import ctypes
    root = tmp_path / "root"
    root.mkdir()
    (root / "cloud.txt").write_bytes(b"not here\n")
    assert ctypes.windll.kernel32.SetFileAttributesW(str(root / "cloud.txt"), 0x1000)
    try:
        kinds = {e.name: e.kind for e in folder_readers.list_dir(root, Path(),
                                                                 lambda r, d: True).entries}
        assert kinds == {"cloud.txt": "other"}
        assert _code(lambda: folder_readers.read_text(root, Path("cloud.txt"))) == "unsupported"
    finally:
        ctypes.windll.kernel32.SetFileAttributesW(str(root / "cloud.txt"), 0x80)


def test_an_alias_of_a_hidden_file_answers_as_a_missing_one_does(tmp_path):
    """Review finding 6, with the real policy. A case variant is refused as
    written (the policy folds case). An 8.3 alias is not: CREDEN~1.JSO opens
    credentials.json, which the policy hides, and the answer must be the one
    a short name with no file behind it gets."""
    from jarvis import draft_files
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_bytes(b"SECRET=1\n")
    (project / "credentials.json").write_bytes(b"{}\n")
    with pytest.raises(draft_files.NotAttached, match="does not"):
        draft_files.Source.open(str(project / ".ENV"))
    with pytest.raises(draft_files.NotAttached, match="does not"):
        draft_files.Source.open(str(project / "credentials.json"))
    listing = subprocess.run(["cmd", "/c", "dir", "/x", "/a", str(project)],
                             capture_output=True, text=True).stdout
    short = [line.split()[-2] for line in listing.splitlines()
             if line.rstrip().endswith("credentials.json") and len(line.split()) >= 5]
    if not short or "~" not in short[0]:
        pytest.skip("8.3 names are not made on this volume")
    with pytest.raises(draft_files.NotAttached) as alias:
        draft_files.Source.open(str(project / short[0]))
    absent = short[0].replace("~1", "~9")
    with pytest.raises(draft_files.NotAttached) as missing:
        draft_files.Source.open(str(project / absent))
    assert str(alias.value) == str(missing.value).replace(absent, short[0])
    assert "There is no file" in str(alias.value)
    os.link(project / ".env", project / "notes.txt")
    with pytest.raises(draft_files.NotAttached, match="link"):
        draft_files.Source.open(str(project / "notes.txt"))
