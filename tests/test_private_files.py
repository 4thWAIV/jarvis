"""private_files on Windows: the tool token, mcp.json and config.toml made
owner-only by their own security descriptor, held folders, and every refusal
(links, placeholders, foreign owners Administrators included, hard links,
folders others can change). Native: these call the real Win32 and NT APIs
on a temporary folder. macOS keeps its own tests (test_internal_tool,
test_private_data, test_codex_provider), unchanged."""
import os
import subprocess
import sys
import textwrap

import pytest

from jarvis import private_files as pf

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows' own security")


def _sddl(path) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-Acl -LiteralPath '{path}').Sddl"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _owner_only(sddl: str) -> bool:
    """Owner this user (LA when it is the built-in administrator), a
    protected DACL of exactly the user and SYSTEM, nothing inherited."""
    sid = pf._user_sid()
    me = {sid, "LA"} if sid.endswith("-500") else {sid}
    owner = sddl[2:sddl.index("G:")] if "G:" in sddl else sddl[2:sddl.index("D:")]
    dacl = sddl[sddl.index("D:"):]
    return (owner in me and dacl.startswith("D:P")
            and sorted(dacl[dacl.index("("):].strip("()").split(")(")) == sorted(
                [f"A;;FA;;;{s}" for s in me & {owner}] + ["A;;FA;;;SY"]))


def _icacls(path, *args):
    subprocess.run(["icacls", str(path), *args], capture_output=True, check=True)


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "private"
    d.mkdir()
    return d


# ── criterion 1: owner-only from the first instant ───────────────────────

def test_a_new_file_is_owned_by_this_user_with_only_the_user_and_system(folder):
    pf.create_new(folder / "tool-token", b"secret")
    assert _owner_only(_sddl(folder / "tool-token")), _sddl(folder / "tool-token")
    assert (folder / "tool-token").read_bytes() == b"secret"


def test_a_replaced_file_is_owner_only_and_whole(folder):
    path = folder / "mcp.json"
    path.write_text("old")                           # inherited ACL, as the old code left it
    pf.replace(path, b'{"new": 1}')
    assert path.read_bytes() == b'{"new": 1}'
    assert _owner_only(_sddl(path)), _sddl(path)
    assert sorted(p.name for p in folder.iterdir()) == ["mcp.json"]


def test_create_new_never_touches_what_is_there(folder):
    (folder / "tool-token").write_bytes(b"theirs")
    with pytest.raises(FileExistsError):
        pf.create_new(folder / "tool-token", b"ours")
    assert (folder / "tool-token").read_bytes() == b"theirs"


def test_an_inherited_acl_file_of_ours_is_adopted_and_made_owner_only(folder):
    path = folder / "tool-token"
    path.write_bytes(b"legacy-token")
    if pf._user_sid() not in _sddl(path) and "O:LA" not in _sddl(path):
        pytest.skip("this account's plain files are not owned by its own SID")
    assert not _owner_only(_sddl(path))
    data, write = pf.adopt(path)
    assert data == b"legacy-token"
    write.close()
    assert _owner_only(_sddl(path)), _sddl(path)


def test_an_empty_adopted_file_is_filled_through_the_same_handle(folder):
    path = folder / "tool-token"
    pf.create_new(path, b"")
    data, write = pf.adopt(path)
    assert data == b""
    write(b"fresh")
    assert path.read_bytes() == b"fresh"
    with pytest.raises(ValueError):
        write(b"again")


# ── criteria 2 and 3: links, placeholders, foreign owners, hard links ────

def test_a_junction_at_the_path_is_refused_and_its_target_untouched(folder, tmp_path):
    import _winapi
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x").write_bytes(b"outside")
    before = _sddl(outside)
    _winapi.CreateJunction(str(outside), str(folder / "tool-token"))
    for call in (lambda: pf.adopt(folder / "tool-token"),
                 lambda: pf.replace(folder / "tool-token", b"ours")):
        with pytest.raises(OSError):
            call()
    assert (outside / "x").read_bytes() == b"outside" and _sddl(outside) == before
    assert sorted(p.name for p in folder.iterdir()) == ["tool-token"]


def test_a_symlink_at_the_path_is_refused_and_its_target_untouched(folder, tmp_path):
    planted = tmp_path / "planted"
    planted.write_bytes(b"attacker-chosen-token")
    before = _sddl(planted)
    try:
        os.symlink(planted, folder / "tool-token")
    except OSError:
        pytest.skip("symbolic links need elevation or developer mode")
    with pytest.raises(pf.NotPrivate):
        pf.adopt(folder / "tool-token")
    with pytest.raises(pf.NotPrivate):
        pf.replace(folder / "tool-token", b"ours")
    with pytest.raises(FileExistsError):
        pf.create_new(folder / "tool-token", b"ours")
    assert planted.read_bytes() == b"attacker-chosen-token" and _sddl(planted) == before
    assert os.path.islink(folder / "tool-token")


def test_a_file_owned_by_administrators_is_refused_not_repaired(folder):
    """Matt's answer to Q1: a file an elevated run left owned by
    Administrators is someone else's, even to an administrator."""
    path = folder / "tool-token"
    path.write_bytes(b"admin-token")
    done = subprocess.run(["powershell", "-NoProfile", "-Command",
                           f"$a = Get-Acl -LiteralPath '{path}'; "
                           "$a.SetOwner([Security.Principal.SecurityIdentifier]'S-1-5-32-544'); "
                           f"Set-Acl -LiteralPath '{path}' $a"], capture_output=True)
    if done.returncode or "O:BA" not in _sddl(path):
        pytest.skip("cannot make a file owned by Administrators without elevation")
    before = _sddl(path)
    with pytest.raises(pf.NotPrivate, match="S-1-5-32-544"):
        pf.adopt(path)
    with pytest.raises(pf.NotPrivate):
        pf.replace(path, b"ours")
    assert path.read_bytes() == b"admin-token" and _sddl(path) == before


def test_an_adopted_file_with_another_hard_link_is_refused(folder, tmp_path):
    path = folder / "tool-token"
    pf.create_new(path, b"token")
    os.link(path, tmp_path / "second-name")
    with pytest.raises(pf.NotPrivate, match="2 hard links"):
        pf.adopt(path)


def test_an_offline_placeholder_is_refused(folder):
    """A OneDrive placeholder is a reparse point (refused above); a file
    marked offline, as cloud files are, is refused too (Q4)."""
    import ctypes
    path = folder / "tool-token"
    pf.create_new(path, b"token")
    assert ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x1000)
    try:
        with pytest.raises(pf.NotPrivate, match="placeholder"):
            pf.adopt(path)
    finally:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)


def test_a_refusal_names_the_path_and_never_the_contents(folder, tmp_path):
    path = folder / "tool-token"
    pf.create_new(path, b"do-not-print-me")
    os.link(path, tmp_path / "other")
    with pytest.raises(pf.NotPrivate) as caught:
        pf.adopt(path)
    assert "do-not-print-me" not in str(caught.value) and "tool-token" in str(caught.value)


# ── Q2: folders others can change ────────────────────────────────────────

def test_a_folder_others_can_modify_is_refused_for_every_call(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "mcp.json").write_bytes(b"old")
    _icacls(shared, "/grant", "*S-1-5-11:(OI)(CI)M")           # Authenticated Users
    for call in (lambda: pf.create_new(shared / "tool-token", b"x"),
                 lambda: pf.adopt(shared / "mcp.json"),
                 lambda: pf.replace(shared / "mcp.json", b"new")):
        with pytest.raises(pf.NotPrivate, match="S-1-5-11"):
            call()
    assert sorted(p.name for p in shared.iterdir()) == ["mcp.json"]
    assert (shared / "mcp.json").read_bytes() == b"old"


def test_a_folder_under_one_others_can_empty_is_refused(tmp_path):
    above = tmp_path / "above"
    (above / "private").mkdir(parents=True)
    _icacls(above, "/grant", "*S-1-1-0:(DC)")                  # Everyone: delete child
    with pytest.raises(pf.NotPrivate, match="S-1-1-0"):
        pf.create_new(above / "private" / "tool-token", b"x")
    assert not (above / "private" / "tool-token").exists()


def test_a_junction_above_the_folder_is_refused_not_followed(tmp_path):
    """Review finding 5: the folder is reached from the drive's root by
    handle, so a junction on any folder above it is refused, and the real
    folder behind it is never written to."""
    import _winapi
    real = tmp_path / "real"
    (real / "jarvis").mkdir(parents=True)
    _winapi.CreateJunction(str(real), str(tmp_path / "data"))
    for call in (lambda: pf.create_new(tmp_path / "data" / "jarvis" / "tool-token", b"x"),
                 lambda: pf.replace(tmp_path / "data" / "jarvis" / "mcp.json", b"x"),
                 lambda: pf.ensure_dir(tmp_path / "data" / "jarvis" / "deeper")):
        with pytest.raises(pf.NotPrivate, match="link"):
            call()
    assert list((real / "jarvis").iterdir()) == []


def test_ensure_dir_in_a_folder_others_can_modify_is_refused(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    _icacls(shared, "/grant", "*S-1-5-11:(OI)(CI)M")
    with pytest.raises(pf.NotPrivate, match="S-1-5-11"):
        pf.ensure_dir(shared / "new")
    assert not (shared / "new").exists()


def test_a_folder_not_on_a_local_drive_is_refused(tmp_path):
    unc = "\\\\localhost\\" + str(tmp_path).replace(":", "$", 1)
    with pytest.raises(pf.NotPrivate, match="local drive"):
        pf.create_new(os.path.join(unc, "tool-token"), b"x")


def test_short_and_non_bmp_names_are_created_and_replaced_exactly(folder):
    pf.replace(folder / "m", b"one")                 # the rename buffer's own minimum
    pf.replace(folder / "m", b"two")
    pf.create_new(folder / "t\U0001F600k", b"emoji")
    assert (folder / "m").read_bytes() == b"two"
    assert (folder / "t\U0001F600k").read_bytes() == b"emoji"
    assert sorted(p.name for p in folder.iterdir()) == ["m", "t\U0001F600k"]


def test_a_junction_for_the_folder_is_refused(tmp_path):
    import _winapi
    outside = tmp_path / "outside"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(tmp_path / "brain"))
    with pytest.raises(pf.NotPrivate, match="link"):
        pf.replace(tmp_path / "brain" / "mcp.json", b"x")
    with pytest.raises(pf.NotPrivate):
        pf.ensure_dir(tmp_path / "brain")
    assert list(outside.iterdir()) == []


# ── the folder is held: renames and swaps while handles are open ─────────

def test_the_folder_cannot_be_renamed_away_while_a_replace_holds_it(folder, monkeypatch):
    real = pf._write_handle
    seen = []

    def swap_then_write(handle, data, path):
        with pytest.raises(PermissionError):
            os.rename(folder, folder.with_name("moved"))
        seen.append("refused")
        real(handle, data, path)

    monkeypatch.setattr(pf, "_write_handle", swap_then_write)
    pf.replace(folder / "mcp.json", b"new")
    assert seen == ["refused"]
    assert (folder / "mcp.json").read_bytes() == b"new"


def test_a_junction_swapped_in_above_the_held_folder_redirects_nothing(tmp_path, monkeypatch):
    """The grandparent is renamed and a junction to an attacker's tree with
    the same names put in its place, while the folder is held. Measured on
    Windows 11 (NTFS): the rename is refused (WinError 5) while anything
    below is open, so the swap cannot happen. Were it allowed, the file
    would still be written, renamed and cleaned up inside the real folder,
    since every step after the first open is relative to its handle."""
    import _winapi
    base = tmp_path / "base"
    folder = base / "data" / "jarvis"
    folder.mkdir(parents=True)
    (folder / "mcp.json").write_bytes(b"old")
    attacker = tmp_path / "attacker"
    (attacker / "jarvis").mkdir(parents=True)
    real = pf._write_handle
    outcome = []

    def swap_then_write(handle, data, path):
        try:
            os.rename(base / "data", base / "data-moved")
        except PermissionError:
            outcome.append("rename refused")
        else:
            _winapi.CreateJunction(str(attacker), str(base / "data"))
            outcome.append("swapped")
        real(handle, data, path)

    monkeypatch.setattr(pf, "_write_handle", swap_then_write)
    pf.replace(folder / "mcp.json", b"new")
    assert list((attacker / "jarvis").iterdir()) == []
    if outcome == ["swapped"]:
        assert (base / "data-moved" / "jarvis" / "mcp.json").read_bytes() == b"new"
        assert sorted(p.name for p in (base / "data-moved" / "jarvis").iterdir()) == ["mcp.json"]
    else:
        assert outcome == ["rename refused"]
        assert (folder / "mcp.json").read_bytes() == b"new"


def test_a_held_destination_is_retried_then_left_as_it_was(folder):
    path = folder / "mcp.json"
    pf.replace(path, b"old")
    with open(path, "rb"):                           # Python shares read and write, not delete
        with pytest.raises(PermissionError):
            pf.replace(path, b"new")
    assert path.read_bytes() == b"old"
    assert sorted(p.name for p in folder.iterdir()) == ["mcp.json"]


def test_a_destination_open_with_delete_sharing_is_replaced(folder):
    import _winapi
    path = folder / "mcp.json"
    pf.replace(path, b"old")
    handle = _winapi.CreateFile(str(path), _winapi.GENERIC_READ, 7, 0,
                                _winapi.OPEN_EXISTING, 0, 0)
    try:
        pf.replace(path, b"new")
    finally:
        _winapi.CloseHandle(handle)
    assert path.read_bytes() == b"new"


# ── directories ──────────────────────────────────────────────────────────

def test_ensure_dir_makes_owner_only_folders(tmp_path):
    made = pf.ensure_dir(tmp_path / "a" / "b")
    sddl = _sddl(made)
    assert sddl.startswith(("O:" + pf._user_sid(), "O:LA")) and "D:P" in sddl
    assert sddl.count("(A;") == 2 and "(A;OICI;FA;;;SY)" in sddl, sddl
    assert pf.ensure_dir(made) == made


# ── the tool token across starts and processes ───────────────────────────

def _token_in_a_fresh_process(data, env_extra=None):
    env = {**os.environ, "JARVIS_DATA_DIR": str(data)}
    return subprocess.run(
        [sys.executable, "-c", "from jarvis import data_paths; "
                               "print(data_paths.ensure_tool_token())"],
        capture_output=True, text=True, env=env, check=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(pf.__file__)))).stdout.strip()


def test_a_second_start_adopts_the_same_token(tmp_path):
    """The regression: the first start made the token and every later one
    failed on O_NOFOLLOW."""
    first = _token_in_a_fresh_process(tmp_path / "data")
    second = _token_in_a_fresh_process(tmp_path / "data")
    assert first and first == second
    assert _owner_only(_sddl(tmp_path / "data" / "jarvis" / "tool-token"))


def test_a_same_user_child_can_read_the_token(tmp_path):
    """How the brain's MCP child gets it."""
    path = tmp_path / "tool-token"
    pf.create_new(path, b"child-readable")
    read = subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
        print(open(r'{path}', 'rb').read().decode())""")],
                          capture_output=True, text=True, check=True)
    assert read.stdout.strip() == "child-readable"
