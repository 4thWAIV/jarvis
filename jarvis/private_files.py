"""Files only this user may read: the loopback tool token, the brain's
mcp.json and the Codex brain's config.toml, which hold the token's path and
copies of the user's connection secrets.

Four calls, one rule each:

- `ensure_dir`: a directory and its missing parents, owner-only.
- `create_new`: a new file, owner-only from its first instant; never an
  existing one.
- `adopt`: an existing file read through ONE handle opened without following
  a link, checked to be a regular file this user owns, made owner-only again,
  and a writer through that same handle (the tool token's "an empty file is
  ours to fill").
- `replace`: the file's whole contents, owner-only afterwards.

And on Windows only, `PartialFile`: a new owner-only file written, read
back, given a named stream and renamed into place through its one handle
(a received download, messaging_files.py).

On macOS and Linux each call is exactly the code its caller had before:
O_EXCL at 0600; O_NOFOLLOW with fstat, the owner's uid and fchmod on the one
descriptor; mkdir 0700; and for `replace`, a write and then chmod 0600.

Windows ignores those modes (a file inherits its folder's ACL), so there
every file is created with its own security descriptor: owned by this
process's user SID, with a protected DACL of exactly that user and SYSTEM.
The owner is named explicitly because an elevated token would otherwise make
Administrators the owner. The folder holding the file is reached from its
drive's root one folder at a time by handle (a junction or link anywhere on
the way refused, each folder's ACL read from its own handle), held without
delete sharing, so it cannot be renamed or removed until the call is done,
and the file is created, opened, renamed and cleaned up
relative to that handle (`_win_handles`), never by a path that could have
changed. `replace` writes an owner-only temporary file beside the path and
renames it over, so the path never holds a partial or looser file.

Refused on Windows as NotPrivate, never followed, repaired or deleted:
anything with a reparse point (junction, symlink, a OneDrive placeholder) or
a cloud-recall attribute; a file owned by any other SID, Administrators
included; an adopted file with more than one hard link; and a folder that
anyone but this user, SYSTEM and Administrators may add to, delete from or
re-permission, or that sits under a folder that lets anyone else delete,
rename or re-permission what it holds. Administrators are trusted for
folders because they can override any ACL anyway; nothing here protects from
an elevated administrator or SYSTEM, as nothing on macOS protects from root.
"""
from __future__ import annotations

import functools
import logging
import os
import secrets
import stat
import sys
import weakref
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# A destination another process holds open without delete sharing refuses a
# rename for that long; past it the old file stays and the error is raised.
REPLACE_ATTEMPTS = 10
REPLACE_WAIT_SEC = 0.05


class NotPrivate(OSError):
    """The path exists but is a link or reparse point, not a regular file or
    directory, someone else's, or in a folder others can change. Never
    deleted or repaired here."""


def ensure_dir(path: Path) -> Path:
    """`path` and its missing parents, each made owner-only; NotPrivate if
    it is a link or someone else's."""
    path = Path(path)
    if sys.platform == "win32":
        return _win_ensure_dir(path)
    missing = [p for p in (path, *path.parents) if not p.exists()]
    for p in reversed(missing):
        p.mkdir(mode=0o700, exist_ok=True)
        os.chmod(p, 0o700)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise NotPrivate(f"{path} is not this user's directory")
    os.chmod(path, 0o700)
    return path


def create_new(path: Path, data: bytes) -> None:
    """Create `path` holding `data`, owner-only from the first instant.
    FileExistsError if anything is already there, a link included."""
    path = Path(path)
    if sys.platform == "win32":
        return _win_create_new(path, data)
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


class _Writer:
    """Truncates and writes the adopted file through the descriptor it was
    read with, then closes it. Dropped unused, it closes the descriptor."""

    def __init__(self, fd: int):
        self._fd = fd
        self._closer = weakref.finalize(self, os.close, fd)

    def __call__(self, data: bytes) -> None:
        if not self._closer.alive:
            raise ValueError("this writer was already used")
        try:
            os.ftruncate(self._fd, 0)
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, data)
        finally:
            self._closer()

    def close(self) -> None:
        self._closer()


def adopt(path: Path, max_bytes: int = 4096) -> tuple[bytes, Callable[[bytes], None]]:
    """(at most `max_bytes` of the existing file `path`, a writer that
    replaces its contents), both through one handle that was opened without
    following a link, checked to be a regular file this user owns, and made
    owner-only again. FileNotFoundError if there is none. The handle stays
    open until the writer is called or dropped."""
    path = Path(path)
    if sys.platform == "win32":
        fd = _win_adopt(path)
    else:
        fd = os.open(str(path), os.O_RDWR | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise NotPrivate(f"{path} is not a regular file")
            if info.st_uid != os.getuid():
                raise NotPrivate(f"{path} is owned by uid {info.st_uid}, not by us")
            os.fchmod(fd, 0o600)
        except BaseException:
            os.close(fd)
            raise
    writer = _Writer(fd)
    try:
        data = os.read(fd, max_bytes)
    except BaseException:
        writer.close()
        raise
    return data, writer


def replace(path: Path, data: bytes) -> None:
    """`data` as the whole of `path`, owner-only afterwards. On Windows it
    goes through an owner-only temporary file renamed over the path, and
    NotPrivate if what is there now is a link, not a regular file or someone
    else's; on any failure there the old file is left as it was."""
    path = Path(path)
    if sys.platform == "win32":
        return _win_replace(path, data)
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError as e:                             # pragma: no cover
        log.warning("could not tighten %s's permissions: %s", path.name, e)


# ── Windows ──────────────────────────────────────────────────────────────

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_GENERIC_ALL = 0x10000000
_DELETE = 0x00010000
_READ_CONTROL = 0x00020000
_WRITE_DAC = 0x00040000
_WRITE_OWNER = 0x00080000
_FILE_LIST_DIRECTORY = 0x0001
_FILE_ADD_FILE = 0x0002
_FILE_ADD_SUBDIRECTORY = 0x0004
_FILE_TRAVERSE = 0x0020
_FILE_DELETE_CHILD = 0x0040
_FILE_READ_ATTRIBUTES = 0x0080
_SHARE_READ_WRITE = 0x1 | 0x2
_SHARE_ALL = 0x1 | 0x2 | 0x4
_OPEN_EXISTING = 3
_FLAG_BACKUP_SEMANTICS = 0x02000000
_FLAG_OPEN_REPARSE_POINT = 0x00200000
_ATTR_DIRECTORY = 0x10
_ATTR_REPARSE_POINT = 0x400
_ATTR_OFFLINE = 0x1000
_ATTR_RECALL_ON_OPEN = 0x40000
_ATTR_RECALL_ON_DATA_ACCESS = 0x400000
_NOT_PLAIN = (_ATTR_REPARSE_POINT | _ATTR_OFFLINE | _ATTR_RECALL_ON_OPEN
              | _ATTR_RECALL_ON_DATA_ACCESS)
_FILE_TYPE_DISK = 1
_SE_FILE_OBJECT = 1
_OWNER_INFO = 0x1
_DACL_INFO = 0x4
_PROTECTED_DACL_INFO = 0x80000000
_INHERIT_ONLY_ACE = 0x08
_ALLOWED_ACE_TYPES = (0x0, 0x9)        # ACCESS_ALLOWED, ACCESS_ALLOWED_CALLBACK
_DENIED_ACE_TYPES = (0x1, 0xA)         # ACCESS_DENIED, ACCESS_DENIED_CALLBACK
_ERROR_ACCESS_DENIED = 5
_ERROR_SHARING_VIOLATION = 32

_SYSTEM = "S-1-5-18"
_ADMINISTRATORS = "S-1-5-32-544"
_TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
_OWNER_RIGHTS = "S-1-3-4"
# Rights in the folder holding a private file that would let someone plant,
# remove or re-permission an entry in it; and in any folder above, the ones
# that would let someone swap a folder on the way.
_PARENT_WRITE = (_FILE_ADD_FILE | _FILE_ADD_SUBDIRECTORY | _FILE_DELETE_CHILD | _DELETE
                 | _WRITE_DAC | _WRITE_OWNER | _GENERIC_ALL | _GENERIC_WRITE)
_ANCESTOR_SWAP = _FILE_DELETE_CHILD | _DELETE | _WRITE_DAC | _WRITE_OWNER | _GENERIC_ALL


@functools.lru_cache(maxsize=1)
def _api():
    import ctypes
    from ctypes import wintypes

    class FileInfo(ctypes.Structure):
        _fields_ = [("dwFileAttributes", wintypes.DWORD),
                    ("ftCreationTime", wintypes.DWORD * 2),
                    ("ftLastAccessTime", wintypes.DWORD * 2),
                    ("ftLastWriteTime", wintypes.DWORD * 2),
                    ("dwVolumeSerialNumber", wintypes.DWORD),
                    ("nFileSizeHigh", wintypes.DWORD), ("nFileSizeLow", wintypes.DWORD),
                    ("nNumberOfLinks", wintypes.DWORD),
                    ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    H, P, D, B = wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL
    PP = ctypes.POINTER(ctypes.c_void_p)
    for fn, args, res in (
            (k32.CreateFileW, [wintypes.LPCWSTR, D, D, P, D, D, H], H),
            (k32.CreateDirectoryW, [wintypes.LPCWSTR, P], B),
            (k32.CloseHandle, [H], B),
            (k32.WriteFile, [H, P, D, ctypes.POINTER(D), P], B),
            (k32.FlushFileBuffers, [H], B),
            (k32.GetFileType, [H], D),
            (k32.GetFileInformationByHandle, [H, ctypes.POINTER(FileInfo)], B),
            (k32.GetCurrentProcess, [], H),
            (k32.LocalFree, [P], P),
            (adv.OpenProcessToken, [H, D, ctypes.POINTER(H)], B),
            (adv.GetTokenInformation, [H, ctypes.c_int, P, D, ctypes.POINTER(D)], B),
            (adv.ConvertSidToStringSidW, [P, ctypes.POINTER(wintypes.LPWSTR)], B),
            (adv.ConvertStringSecurityDescriptorToSecurityDescriptorW,
             [wintypes.LPCWSTR, D, PP, ctypes.POINTER(wintypes.ULONG)], B),
            (adv.GetSecurityDescriptorDacl, [P, ctypes.POINTER(B), PP, ctypes.POINTER(B)], B),
            (adv.GetSecurityInfo, [H, ctypes.c_int, D, PP, PP, PP, PP, PP], D),
            (adv.SetSecurityInfo, [H, ctypes.c_int, D, P, P, P, P], D),
            (adv.GetAce, [P, D, PP], B)):
        fn.argtypes, fn.restype = args, res
    return ctypes, wintypes, k32, adv, FileInfo


def _error(code: int, path) -> OSError:
    """The Windows error `code` as the OSError subclass Python gives it,
    naming the path and nothing else."""
    ctypes = _api()[0]
    return OSError(0, ctypes.FormatError(code).strip(), str(path), code)


def _last_error(path) -> OSError:
    return _error(_api()[0].get_last_error(), path)


@functools.lru_cache(maxsize=1)
def _user_sid() -> str:
    """This process token's user SID, as a string."""
    ctypes, wintypes, k32, adv, _ = _api()
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x8, ctypes.byref(token)):
        raise _last_error("process token")
    try:
        needed = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        buf = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, buf, needed, ctypes.byref(needed)):
            raise _last_error("process token")
        return _sid_string(ctypes.c_void_p.from_buffer(buf).value)
    finally:
        k32.CloseHandle(token)


def _sid_string(sid: int) -> str:
    ctypes, wintypes, k32, adv, _ = _api()
    text = wintypes.LPWSTR()
    if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
        raise _last_error("SID")
    try:
        return text.value
    finally:
        k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))


class _Descriptor:
    """This user's owner-only security descriptor, freed on exit: owner the
    user SID, a protected DACL of the user and SYSTEM, inherited by what a
    folder holds when `directory`."""

    def __init__(self, directory: bool = False):
        ctypes, _, _, adv, _ = _api()
        sid = _user_sid()
        inherit = "OICI" if directory else ""
        self.address = ctypes.c_void_p()
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"O:{sid}D:P(A;{inherit};FA;;;{sid})(A;{inherit};FA;;;SY)", 1,
                ctypes.byref(self.address), None):
            raise _last_error("security descriptor")

    def attributes(self):
        """A SECURITY_ATTRIBUTES carrying it, for CreateDirectoryW."""
        ctypes, wintypes = _api()[:2]

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                        ("bInheritHandle", wintypes.BOOL)]
        return SecurityAttributes(ctypes.sizeof(SecurityAttributes), self.address, False)

    def dacl(self) -> int:
        ctypes, wintypes, _, adv, _ = _api()
        present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not adv.GetSecurityDescriptorDacl(self.address, ctypes.byref(present),
                                             ctypes.byref(acl), ctypes.byref(defaulted)):
            raise _last_error("security descriptor")
        return acl.value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        _api()[2].LocalFree(self.address)


def _close(handle: int) -> None:
    _api()[2].CloseHandle(handle)


def _file_info(handle: int, path):
    ctypes, _, k32, _, FileInfo = _api()
    info = FileInfo()
    if not k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise _last_error(path)
    return info


def _security(handle: int, path) -> tuple[str, list[tuple[int, int, int, str]]]:
    """(owner SID, DACL as (type, flags, mask, SID) per ACE) of an open
    handle; `path` only names it in an error. A null DACL is one ACE
    granting everyone everything."""
    ctypes, _, k32, adv, _ = _api()
    owner, dacl, sd = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    code = adv.GetSecurityInfo(handle, _SE_FILE_OBJECT, _OWNER_INFO | _DACL_INFO,
                               ctypes.byref(owner), None, ctypes.byref(dacl), None,
                               ctypes.byref(sd))
    if code:
        raise _error(code, path)
    try:
        if not dacl.value:
            return _sid_string(owner.value), [(0, 0, _GENERIC_ALL, "S-1-1-0")]
        aces = []
        count = ctypes.c_uint16.from_address(dacl.value + 4).value
        for i in range(count):
            ace = ctypes.c_void_p()
            if not adv.GetAce(dacl, i, ctypes.byref(ace)):
                raise _last_error(path)
            kind = ctypes.c_uint8.from_address(ace.value).value
            flags = ctypes.c_uint8.from_address(ace.value + 1).value
            if kind in _ALLOWED_ACE_TYPES + _DENIED_ACE_TYPES:
                mask = ctypes.c_uint32.from_address(ace.value + 4).value
                aces.append((kind, flags, mask, _sid_string(ace.value + 8)))
            else:
                aces.append((kind, flags, 0xFFFFFFFF, ""))
        return _sid_string(owner.value), aces
    finally:
        k32.LocalFree(sd)


def _others_with(aces, rights: int, trusted: set[str]) -> list[str]:
    """SIDs outside `trusted` that an ACE that applies here allows any of
    `rights`. An allow ACE this does not read counts against it."""
    found = []
    for kind, flags, mask, sid in aces:
        if flags & _INHERIT_ONLY_ACE or kind in _DENIED_ACE_TYPES:
            continue
        if kind not in _ALLOWED_ACE_TYPES:
            found.append("an unreadable entry")
        elif mask & rights and sid not in trusted:
            found.append(sid)
    return found


def _pin_folder(directory: Path) -> int:
    """A handle on `directory`, reached from its drive's root one folder at
    a time, each opened relative to the one before and as itself, so a
    junction or link anywhere on the way is refused rather than followed;
    the last opened without delete sharing, so it cannot be renamed or
    removed while held. NotPrivate unless it is a plain folder on a local
    drive that only this user, SYSTEM and Administrators can add to, delete
    from or re-permission, under folders that let no one else delete,
    rename or re-permission what they hold, each judged by its own handle."""
    from jarvis import _win_handles as wh
    ctypes, _, k32, _, _ = _api()
    directory = Path(os.path.abspath(directory))
    anchor = directory.anchor
    if len(anchor) != 3 or not anchor[0].isalpha() or anchor[1:] != ":\\":
        raise NotPrivate(f"{directory} is not on a local drive: not a safe place for a secret")
    trusted = {_user_sid(), _SYSTEM, _ADMINISTRATORS, _TRUSTED_INSTALLER, _OWNER_RIGHTS}
    access = _READ_CONTROL | _FILE_READ_ATTRIBUTES | _FILE_LIST_DIRECTORY | _FILE_TRAVERSE
    parts = directory.relative_to(anchor).parts
    handle = k32.CreateFileW("\\\\?\\" + anchor, access,
                             _SHARE_READ_WRITE if not parts else _SHARE_ALL, None,
                             _OPEN_EXISTING, _FLAG_BACKUP_SEMANTICS | _FLAG_OPEN_REPARSE_POINT,
                             None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise _last_error(anchor)
    here = Path(anchor)
    try:
        for i, part in enumerate(parts):
            owner, aces = _security(handle, here)
            others = _others_with(aces, _ANCESTOR_SWAP, trusted)
            if owner not in trusted or others:
                who = ", ".join(sorted(set(others))) or owner
                raise NotPrivate(f"{here} lets {who} move what it holds: "
                                 "not a safe place for a secret")
            here = here / part
            try:
                nxt = wh.open_relative(handle, part, access,
                                       _SHARE_READ_WRITE if i == len(parts) - 1 else _SHARE_ALL,
                                       wh.FILE_OPEN, wh.FILE_DIRECTORY_FILE)
            except PermissionError as e:
                raise NotPrivate(f"{here} cannot be opened to check it ({e.strerror}): "
                                 "not a safe place for a secret") from None
            _close(handle)
            handle = nxt
            if _file_info(handle, here).dwFileAttributes & _NOT_PLAIN:
                raise NotPrivate(f"{here} is a link or not a plain folder")
        owner, aces = _security(handle, directory)
        if owner not in trusted:
            raise NotPrivate(f"{directory} is owned by {owner}: "
                             "not a safe place for a secret")
        others = _others_with(aces, _PARENT_WRITE, trusted)
        if others:
            raise NotPrivate(f"{directory} can be changed by "
                             f"{', '.join(sorted(set(others)))}: not a safe place for a secret")
        return handle
    except BaseException:
        _close(handle)
        raise


def _child(folder: int, name: str, access: int, disposition: int, options: int = 0,
           security: "_Descriptor | None" = None, share: int = _SHARE_ALL) -> int:
    from jarvis import _win_handles as wh
    return wh.open_relative(folder, name, access, share, disposition, options,
                            security.address.value if security else None)


def _check_plain_file(handle: int, path: Path, one_link: bool) -> None:
    """NotPrivate unless the open handle is a regular disk file, with no
    reparse point or cloud recall, owned by this user's SID, and (when
    `one_link`) reachable by no other name."""
    info = _file_info(handle, path)
    if info.dwFileAttributes & _ATTR_DIRECTORY or info.dwFileAttributes & _NOT_PLAIN:
        raise NotPrivate(f"{path} is a link, placeholder or folder, not a regular file")
    if _api()[2].GetFileType(handle) != _FILE_TYPE_DISK:
        raise NotPrivate(f"{path} is not a regular file")
    if one_link and info.nNumberOfLinks != 1:
        raise NotPrivate(f"{path} has {info.nNumberOfLinks} hard links, not one")
    owner, _ = _security(handle, path)
    if owner != _user_sid():
        raise NotPrivate(f"{path} is owned by {owner}, not by us")


def _write_handle(handle: int, data: bytes, path, flush: bool = True) -> None:
    ctypes, wintypes, k32, _, _ = _api()
    buffer = ctypes.create_string_buffer(data, len(data))
    done, written = 0, wintypes.DWORD()
    while done < len(data):
        if not k32.WriteFile(handle, ctypes.byref(buffer, done), len(data) - done,
                             ctypes.byref(written), None):
            raise _last_error(path)
        done += written.value
    if flush and not k32.FlushFileBuffers(handle):
        raise _last_error(path)


def _protect(handle: int, security: _Descriptor, path) -> None:
    """The owner-only protected DACL put back on an open handle: fchmod."""
    code = _api()[3].SetSecurityInfo(handle, _SE_FILE_OBJECT,
                                      _DACL_INFO | _PROTECTED_DACL_INFO,
                                      None, None, security.dacl(), None)
    if code:
        raise _error(code, path)


def _win_ensure_dir(path: Path) -> Path:
    ctypes, _, k32, _, _ = _api()
    path = Path(os.path.abspath(path))
    missing = [p for p in (path, *path.parents) if not p.exists()]
    if missing:
        _close(_pin_folder(missing[-1].parent))
    with _Descriptor(directory=True) as security:
        attributes = security.attributes()
        for p in reversed(missing):
            if not k32.CreateDirectoryW(str(p), ctypes.byref(attributes)):
                failure = _last_error(p)
                if not isinstance(failure, FileExistsError):
                    raise failure
        folder = _pin_folder(path.parent)
        try:
            from jarvis import _win_handles as wh
            handle = _child(folder, path.name, _READ_CONTROL | _WRITE_DAC | _FILE_READ_ATTRIBUTES,
                            wh.FILE_OPEN, wh.FILE_DIRECTORY_FILE)
        finally:
            _close(folder)
        try:
            attributes = _file_info(handle, path).dwFileAttributes
            if not attributes & _ATTR_DIRECTORY or attributes & _NOT_PLAIN:
                raise NotPrivate(f"{path} is a link or not a plain folder")
            owner, _ = _security(handle, path)
            if owner != _user_sid():
                raise NotPrivate(f"{path} is owned by {owner}, not by us")
            _protect(handle, security, path)
        finally:
            _close(handle)
    return path


def _win_create_new(path: Path, data: bytes) -> None:
    from jarvis import _win_handles as wh
    folder = _pin_folder(path.parent)
    try:
        with _Descriptor() as security:
            handle = _child(folder, path.name, _GENERIC_WRITE, wh.FILE_CREATE,
                            wh.FILE_NON_DIRECTORY_FILE, security, share=0)
        try:
            _write_handle(handle, data, path)
        finally:
            _close(handle)
    finally:
        _close(folder)


def _win_adopt(path: Path) -> int:
    import msvcrt
    from jarvis import _win_handles as wh
    folder = _pin_folder(path.parent)
    try:
        handle = _child(folder, path.name,
                        _GENERIC_READ | _GENERIC_WRITE | _READ_CONTROL | _WRITE_DAC,
                        wh.FILE_OPEN)
    finally:
        _close(folder)
    try:
        _check_plain_file(handle, path, one_link=True)
        with _Descriptor() as security:
            _protect(handle, security, path)
        return msvcrt.open_osfhandle(handle, 0)
    except BaseException:
        _close(handle)
        raise


class PartialFile:
    """Windows only: a new owner-only file `name` in the private folder
    `directory`, held by one handle from creation until it is published or
    discarded. The folder is pinned as `_pin_folder` pins it (a junction or
    link on the way refused, held without delete sharing), the file is
    created relative to it with this user's descriptor and no sharing, and
    every later step goes through the file's own handle: writing, reading
    back, adding a named stream, renaming into place (`publish`) and
    deleting (`discard`), never a path that could have changed."""

    def __init__(self, directory: Path, name: str):
        import msvcrt
        from jarvis import _win_handles as wh
        self.path = Path(os.path.abspath(directory)) / name
        self._folder = _pin_folder(self.path.parent)
        try:
            with _Descriptor() as security:
                handle = _child(self._folder, name, _GENERIC_READ | _GENERIC_WRITE | _DELETE,
                                wh.FILE_CREATE, wh.FILE_NON_DIRECTORY_FILE, security, share=0)
            try:
                self._fd: "int | None" = msvcrt.open_osfhandle(handle, os.O_RDWR)
            except BaseException:
                _close(handle)
                raise
        except BaseException:
            _close(self._folder)
            raise
        self._handle = handle

    def write(self, data: bytes) -> None:
        _write_handle(self._handle, data, self.path, flush=False)

    def digest(self, chunk: int = 1 << 20) -> tuple[int, str]:
        """(size, sha256) of what is on disk now, read back by handle."""
        import hashlib
        from jarvis import _win_handles as wh
        if not _api()[2].FlushFileBuffers(self._handle):
            raise _last_error(self.path)
        hasher, size = hashlib.sha256(), 0
        while data := wh.pread(self._fd, chunk, size):
            hasher.update(data)
            size += len(data)
        return size, hasher.hexdigest()

    def add_stream(self, stream: str, data: bytes) -> None:
        """A new named stream `stream` of this file holding `data`, opened
        relative to the file's handle. It shares delete: the file's own
        handle holds DELETE, which NTFS checks against every stream."""
        from jarvis import _win_handles as wh
        handle = wh.open_relative(self._handle, ":" + stream, _GENERIC_WRITE, _SHARE_ALL,
                                  wh.FILE_CREATE, wh.FILE_NON_DIRECTORY_FILE)
        try:
            _write_handle(handle, data, f"{self.path}:{stream}")
        finally:
            _close(handle)

    def read_stream(self, stream: str, limit: int = 4096) -> bytes:
        """At most `limit` bytes of this file's named stream `stream`,
        opened relative to the file's handle (FileNotFoundError if none)."""
        import msvcrt
        from jarvis import _win_handles as wh
        handle = wh.open_relative(self._handle, ":" + stream, _GENERIC_READ, _SHARE_ALL,
                                  wh.FILE_OPEN, wh.FILE_NON_DIRECTORY_FILE)
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        except BaseException:
            _close(handle)
            raise
        try:
            return wh.pread(fd, limit, 0)
        finally:
            os.close(fd)

    def publish(self, name: str, replace: bool = False) -> None:
        """Rename the file to `name` in its folder and let it go. Without
        `replace`, FileExistsError if `name` is there, which is left as it
        is. With it, what is there must be a regular file of ours, as for
        `replace` (NotPrivate for a link, junction or folder, never
        replaced). On any failure the file is still held, to discard."""
        from jarvis import _win_handles as wh
        _check_plain_file(self._handle, self.path, one_link=True)
        if replace:
            existing = _child(self._folder, name, _READ_CONTROL | _FILE_READ_ATTRIBUTES,
                              wh.FILE_OPEN)
            try:
                _check_plain_file(existing, self.path.parent / name, one_link=False)
            finally:
                _close(existing)
            wh.rename_into(self._handle, self._folder, name, REPLACE_ATTEMPTS, REPLACE_WAIT_SEC,
                           (_ERROR_ACCESS_DENIED, _ERROR_SHARING_VIOLATION))
        else:
            wh.rename_into(self._handle, self._folder, name, 1, 0, (), replace=False)
        self.close()

    def discard(self) -> None:
        """Delete the file by its handle, its streams with it, and let it
        go. Nothing once it is published or let go."""
        from jarvis import _win_handles as wh
        if self._fd is not None:
            wh.delete_on_close(self._handle)
        self.close()

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
            _close(self._folder)


def _win_replace(path: Path, data: bytes) -> None:
    from jarvis import _win_handles as wh
    folder = _pin_folder(path.parent)
    try:
        try:
            existing = _child(folder, path.name, _READ_CONTROL | _FILE_READ_ATTRIBUTES,
                              wh.FILE_OPEN)
        except FileNotFoundError:
            pass
        else:
            try:
                _check_plain_file(existing, path, one_link=False)
            finally:
                _close(existing)
        temp = f".{path.name}.{secrets.token_hex(8)}.tmp"
        with _Descriptor() as security:
            handle = _child(folder, temp, _GENERIC_WRITE | _DELETE, wh.FILE_CREATE,
                            wh.FILE_NON_DIRECTORY_FILE, security, share=0)
        try:
            _write_handle(handle, data, path.parent / temp)
            wh.rename_into(handle, folder, path.name, REPLACE_ATTEMPTS, REPLACE_WAIT_SEC,
                           (_ERROR_ACCESS_DENIED, _ERROR_SHARING_VIOLATION))
        except BaseException:
            wh.delete_on_close(handle)
            raise
        finally:
            _close(handle)
    finally:
        _close(folder)
