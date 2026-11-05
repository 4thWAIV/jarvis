"""Handle-relative file access on Windows, through ntdll and kernel32 with
ctypes (standard library only). Windows only; imported lazily.

Windows' Python has no `dir_fd`, no O_NOFOLLOW and no openat, and a path
handed to CreateFileW follows every junction and symlink on the way. The
native equivalent of openat is NtCreateFile with OBJECT_ATTRIBUTES'
RootDirectory set to the handle of the folder before: the name is looked up
in that folder only, so nothing that happens to the path above it (a folder
renamed, a junction put in its place) changes what is opened. With
FILE_OPEN_REPARSE_POINT the reparse point itself is opened, never what it
points at, so a link can be seen and refused.
"""
from __future__ import annotations

import functools
from typing import Optional

# NtCreateFile's dispositions and options.
FILE_OPEN = 1
FILE_CREATE = 2
FILE_DIRECTORY_FILE = 0x1
FILE_SYNCHRONOUS_IO_NONALERT = 0x20
FILE_NON_DIRECTORY_FILE = 0x40
FILE_OPEN_FOR_BACKUP_INTENT = 0x4000
FILE_OPEN_REPARSE_POINT = 0x200000
SYNCHRONIZE = 0x00100000

_FILE_RENAME_INFORMATION = 10
_FILE_DISPOSITION_INFORMATION = 13
_FILE_RENAME_INFORMATION_EX = 65
_RENAME_REPLACE_IF_EXISTS = 0x1
_RENAME_POSIX_SEMANTICS = 0x2
# What a filesystem says when it has no POSIX-semantics rename.
_NO_POSIX_RENAME = {0xC000000D, 0xC00000BB, 0xC0000003}   # INVALID_PARAMETER, NOT_SUPPORTED,
#                                                             INVALID_INFO_CLASS


@functools.lru_cache(maxsize=1)
def _nt():
    import ctypes
    from ctypes import wintypes

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort),
                    ("Buffer", ctypes.c_wchar_p)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [("Length", ctypes.c_ulong), ("RootDirectory", wintypes.HANDLE),
                    ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", ctypes.c_ulong),
                    ("SecurityDescriptor", ctypes.c_void_p),
                    ("SecurityQualityOfService", ctypes.c_void_p)]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_void_p)]

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.c_ulong,
                                   ctypes.POINTER(ObjectAttributes),
                                   ctypes.POINTER(IoStatusBlock), ctypes.c_void_p,
                                   ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                   ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong]
    ntdll.NtCreateFile.restype = ctypes.c_ulong
    ntdll.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(IoStatusBlock),
                                           ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
    ntdll.NtSetInformationFile.restype = ctypes.c_ulong
    ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_ulong]
    ntdll.RtlNtStatusToDosError.restype = ctypes.c_ulong
    return ctypes, wintypes, ntdll, UnicodeString, ObjectAttributes, IoStatusBlock


def error(status: int, name) -> OSError:
    """An NTSTATUS as the OSError subclass Python gives its Windows error,
    naming `name` and nothing else."""
    ctypes, _, ntdll, *_ = _nt()
    code = ntdll.RtlNtStatusToDosError(status)
    return OSError(0, ctypes.FormatError(code).strip(), str(name), code)


def open_relative(parent: int, name: str, access: int, share: int, disposition: int,
                  options: int, security_descriptor: Optional[int] = None) -> int:
    """A handle on `name` inside the folder open as `parent`, looked up in
    that folder alone, with the reparse point itself opened rather than
    followed. `name` is one component; the caller has checked it."""
    ctypes, wintypes, ntdll, UnicodeString, ObjectAttributes, IoStatusBlock = _nt()
    # Lengths in bytes of UTF-16: a character outside the BMP is two units,
    # so len(name) would cut the name short (measured with an emoji name).
    size = len(name.encode("utf-16-le", "surrogatepass"))
    text = ctypes.create_unicode_buffer(name)
    unicode = UnicodeString(size, size, ctypes.cast(text, ctypes.c_wchar_p))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent,
                                  ctypes.pointer(unicode), 0, security_descriptor, None)
    handle, io = wintypes.HANDLE(), IoStatusBlock()
    status = ntdll.NtCreateFile(ctypes.byref(handle), access | SYNCHRONIZE,
                                ctypes.byref(attributes), ctypes.byref(io), None, 0x80,
                                share, disposition,
                                options | FILE_OPEN_REPARSE_POINT
                                | FILE_SYNCHRONOUS_IO_NONALERT, None, 0)
    if status:
        raise error(status, name)
    return handle.value


def rename_into(handle: int, parent: int, name: str, attempts: int, wait: float,
                busy: tuple[int, ...]) -> None:
    """Rename the file open as `handle` to `name` in the folder open as
    `parent`, replacing what is there: POSIX style where the filesystem has
    it (the old name is unlinked even while open with delete sharing),
    otherwise the plain replacing rename. A Windows error in `busy` (a
    destination someone holds open) is tried again `attempts` times."""
    import time
    ctypes, _, ntdll, _, _, IoStatusBlock = _nt()
    encoded = name.encode("utf-16-le", "surrogatepass")
    size = max(24, 20 + len(encoded))           # the struct itself is 24 bytes on x64
    buffer = ctypes.create_string_buffer(size)
    ctypes.c_void_p.from_buffer(buffer, 8).value = parent
    ctypes.c_uint32.from_buffer(buffer, 16).value = len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + 20, encoded, len(encoded))
    classes = [(_FILE_RENAME_INFORMATION_EX, _RENAME_REPLACE_IF_EXISTS | _RENAME_POSIX_SEMANTICS),
               (_FILE_RENAME_INFORMATION, 1)]
    for info_class, flags in classes:
        ctypes.c_uint32.from_buffer(buffer, 0).value = flags
        for attempt in range(attempts):
            status = ntdll.NtSetInformationFile(handle, ctypes.byref(IoStatusBlock()), buffer,
                                                size, info_class)
            if not status:
                return
            if info_class == _FILE_RENAME_INFORMATION_EX and status in _NO_POSIX_RENAME:
                break
            failure = error(status, name)
            if failure.winerror not in busy or attempt == attempts - 1:
                raise failure
            time.sleep(wait)


def delete_on_close(handle: int) -> None:
    """Mark the file open as `handle` (with DELETE access) to be removed
    when it closes: by the handle, never by its name."""
    ctypes, _, ntdll, _, _, IoStatusBlock = _nt()
    flag = ctypes.c_ubyte(1)
    ntdll.NtSetInformationFile(handle, ctypes.byref(IoStatusBlock()), ctypes.byref(flag), 1,
                               _FILE_DISPOSITION_INFORMATION)


# ── the folder reader's walk (folder_readers on Windows) ─────────────────

_LIST = 0x0001                 # FILE_LIST_DIRECTORY; FILE_READ_DATA on a file
_TRAVERSE = 0x0020
_READ_ATTRIBUTES = 0x0080
_SHARE_ALL = 0x1 | 0x2 | 0x4
_ATTR_DIRECTORY = 0x10
_ATTR_DEVICE = 0x40
_ATTR_REPARSE_POINT = 0x400
_ATTR_OFFLINE = 0x1000
_ATTR_RECALL_ON_OPEN = 0x40000
_ATTR_RECALL_ON_DATA_ACCESS = 0x400000
_CLOUD = _ATTR_OFFLINE | _ATTR_RECALL_ON_OPEN | _ATTR_RECALL_ON_DATA_ACCESS
_FILE_TYPE_DISK = 1
_FULL_DIRECTORY_INFO = 14
_FULL_DIRECTORY_RESTART_INFO = 15
_ERROR_NO_MORE_FILES = 18
_ERROR_HANDLE_EOF = 38
_FILETIME_UNIX_EPOCH = 116444736000000000
_DEVICE_STEMS = ({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                 | {f"{d}{n}" for d in ("COM", "LPT") for n in "0123456789¹²³"})


class Refused(OSError):
    """Not opened: `why` is "link" (a reparse point, never followed), "name"
    (a name Windows would read as something else), "hard link" (a file with
    another name, which may be outside the folder or one its policy hides)
    or "kind" (not a regular disk file where one was asked for)."""

    def __init__(self, why: str, name: str):
        super().__init__(f"{name!r} is refused ({why})")
        self.why = why


def name_refused(name: str) -> bool:
    """A component Windows would not take as itself: a separator, a stream
    (':'), a wildcard, a control character, '.' or '..', a trailing dot or
    space (Win32 strips them), or a DOS device name with any extension."""
    if name in ("", ".", "..") or name.endswith((".", " ")):
        return True
    if any(c in '\\/:*?"<>|' or ord(c) < 32 for c in name):
        return True
    return name.split(".", 1)[0].rstrip(" ").upper() in _DEVICE_STEMS


@functools.lru_cache(maxsize=1)
def _k32():
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

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", wintypes.HANDLE)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    H, D, B, P = wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, ctypes.c_void_p
    for fn, args, res in (
            (k32.CreateFileW, [wintypes.LPCWSTR, D, D, P, D, D, H], H),
            (k32.CloseHandle, [H], B),
            (k32.GetFileType, [H], D),
            (k32.GetFileInformationByHandle, [H, ctypes.POINTER(FileInfo)], B),
            (k32.GetFileInformationByHandleEx, [H, ctypes.c_int, P, D], B),
            (k32.GetFinalPathNameByHandleW, [H, wintypes.LPWSTR, D, D], D),
            (k32.ReadFile, [H, P, D, ctypes.POINTER(D), ctypes.POINTER(Overlapped)], B)):
        fn.argtypes, fn.restype = args, res
    return ctypes, wintypes, k32, FileInfo, Overlapped


def _last_error(name) -> OSError:
    ctypes = _k32()[0]
    code = ctypes.get_last_error()
    return OSError(0, ctypes.FormatError(code).strip(), str(name), code)


def _info(handle: int, name):
    ctypes, _, k32, FileInfo, _ = _k32()
    info = FileInfo()
    if not k32.GetFileInformationByHandle(handle, ctypes.byref(info)):
        raise _last_error(name)
    return info


def _attributes(handle: int, name) -> int:
    return _info(handle, name).dwFileAttributes


def identity(folder: "DirHandle") -> str:
    """The open folder's filesystem identity as os.stat gives it,
    "st_dev:st_ino" (volume serial, 128-bit file id from FileIdInfo;
    measured equal to os.stat's), which is how a registered folder's
    identity was recorded."""
    ctypes, _, k32, _, _ = _k32()
    buffer = ctypes.create_string_buffer(24)
    if not k32.GetFileInformationByHandleEx(folder.handle, 18, buffer, 24):   # FileIdInfo
        raise _last_error(folder.name)
    return (f"{int.from_bytes(buffer.raw[:8], 'little')}:"
            f"{int.from_bytes(buffer.raw[8:24], 'little')}")


def _final_path(handle: int, name) -> str:
    """The handle's normalized final path without its volume: each name as
    the file has it in its folder, in its own case, never an 8.3 alias
    (measured: a handle opened as CREDEN~1.JSO or .SSH reports
    credentials.json and .ssh)."""
    ctypes, _, k32, _, _ = _k32()
    size = 512
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        needed = k32.GetFinalPathNameByHandleW(handle, buffer, size, 0x4)   # VOLUME_NAME_NONE
        if not needed:
            raise _last_error(name)
        if needed < size:
            return buffer.value
        size = needed + 1


def _close(handle: int) -> None:
    _k32()[2].CloseHandle(handle)


class DirHandle:
    """An open folder, for opening what it holds relative to it."""

    def __init__(self, handle: int, name: str):
        self.handle, self.name = handle, name    # name: its own, as its folder has it

    def close(self) -> None:
        if self.handle is not None:
            _close(self.handle)
            self.handle = None

    def __enter__(self) -> "DirHandle":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _kind(attributes: int) -> str:
    if attributes & _ATTR_REPARSE_POINT:
        return "link"
    if attributes & _ATTR_DIRECTORY:
        return "dir"
    if attributes & (_CLOUD | _ATTR_DEVICE):
        return "other"
    return "file"


def open_root(path) -> DirHandle:
    """The folder at the absolute path `path`, opened as itself (a reparse
    point there is Refused, never followed) through a \\\\?\\ path, so no
    MAX_PATH limit applies."""
    import os
    ctypes, _, k32, _, _ = _k32()
    path = os.path.abspath(str(path))
    full = "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    handle = k32.CreateFileW(full, _LIST | _TRAVERSE | _READ_ATTRIBUTES, _SHARE_ALL, None, 3,
                             0x02000000 | 0x00200000, None)   # BACKUP_SEMANTICS, OPEN_REPARSE_POINT
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise _last_error(path)
    try:
        kind = _kind(_attributes(handle, path))
        if kind == "link":
            raise Refused("link", path)
        if kind != "dir":
            raise NotADirectoryError(0, "not a folder", path, 267)
        return DirHandle(handle, _final_path(handle, path).rsplit("\\", 1)[-1])
    except BaseException:
        _close(handle)
        raise


def _open_child(parent: DirHandle, name: str, access: int,
                exact: bool) -> tuple[int, str, str]:
    """(handle, kind, its own name) for `name` in `parent`: refused by name
    before any open, the reparse point itself opened and Refused as a link.
    With `exact`, FileNotFoundError unless its own name is exactly `name`:
    an 8.3 alias or another case opens the same file and is not taken."""
    if name_refused(name):
        raise Refused("name", name)
    handle = open_relative(parent.handle, name, access | _READ_ATTRIBUTES, _SHARE_ALL,
                           FILE_OPEN, 0)
    try:
        kind = _kind(_attributes(handle, name))
        if kind == "link":
            raise Refused("link", name)
        own = _final_path(handle, name).rsplit("\\", 1)[-1]
        if exact and own != name:
            raise FileNotFoundError(0, "no entry has exactly this name", name, 2)
    except BaseException:
        _close(handle)
        raise
    return handle, kind, own


def open_child_dir(parent: DirHandle, name: str, exact: bool = True) -> DirHandle:
    handle, kind, own = _open_child(parent, name, _LIST | _TRAVERSE, exact)
    if kind != "dir":
        _close(handle)
        raise NotADirectoryError(0, "not a folder", name, 267)
    return DirHandle(handle, own)


def open_child_file(parent: DirHandle, name: str, exact: bool = True):
    """(descriptor, its stat, its own name) for the regular disk file
    `name` in `parent` that has no other name; Refused("kind") for anything
    else, Refused("hard link") for a file with more than one link. The
    descriptor is the caller's to close."""
    import msvcrt
    import os
    handle, kind, own = _open_child(parent, name, _LIST, exact)
    if kind != "file" or _k32()[2].GetFileType(handle) != _FILE_TYPE_DISK:
        _close(handle)
        raise Refused("kind", name)
    try:
        links = _info(handle, name).nNumberOfLinks
    except BaseException:
        _close(handle)
        raise
    if links != 1:
        _close(handle)
        raise Refused("hard link", name)
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        _close(handle)
        raise
    try:
        return fd, os.fstat(fd), own
    except BaseException:
        os.close(fd)
        raise


def child_kind(parent: DirHandle, name: str) -> str:
    """"dir", "file", "link" or "other" for `name` in `parent`, opened with
    attribute access alone; FileNotFoundError if no entry has exactly that
    name. A name Windows would read as something else is never opened: it
    is "other" when the folder lists it."""
    if name_refused(name):
        if any(entry.name == name for entry in entries(parent)):
            return "other"
        raise FileNotFoundError(0, "no entry has exactly this name", name, 2)
    try:
        handle, kind, _ = _open_child(parent, name, 0, exact=True)
    except Refused:
        return "link"
    _close(handle)
    return kind


class RawEntry:
    __slots__ = ("name", "kind", "size", "modified")

    def __init__(self, name: str, kind: str, size: int, modified: float):
        self.name, self.kind, self.size, self.modified = name, kind, size, modified


def entries(parent: DirHandle):
    """What `parent` holds, read through its handle with no path lookups:
    name, kind, size and last write time each. A name Windows would read as
    something else is "other"."""
    ctypes, _, k32, _, _ = _k32()
    buffer = ctypes.create_string_buffer(64 * 1024)
    info_class = _FULL_DIRECTORY_RESTART_INFO
    while True:
        if not k32.GetFileInformationByHandleEx(parent.handle, info_class, buffer,
                                                len(buffer)):
            if ctypes.get_last_error() == _ERROR_NO_MORE_FILES:
                return
            raise _last_error("folder listing")
        info_class = _FULL_DIRECTORY_INFO
        offset = 0
        while True:                                # FILE_FULL_DIR_INFO records
            at = ctypes.addressof(buffer) + offset
            following = ctypes.c_uint32.from_address(at).value
            written = ctypes.c_int64.from_address(at + 24).value
            size = ctypes.c_int64.from_address(at + 40).value
            attributes = ctypes.c_uint32.from_address(at + 56).value
            length = ctypes.c_uint32.from_address(at + 60).value
            name = ctypes.wstring_at(at + 68, length // 2)
            if name not in (".", ".."):
                kind = "other" if name_refused(name) else _kind(attributes)
                yield RawEntry(name, kind, size,
                               (written - _FILETIME_UNIX_EPOCH) / 10_000_000)
            if not following:
                break
            offset += following


def held_path(fd: int) -> str:
    """The path Windows gives the file open as `fd` now, with its drive, in
    its own names: what a policy judges, never reopened."""
    import msvcrt
    ctypes, _, k32, _, _ = _k32()
    handle = msvcrt.get_osfhandle(fd)
    size = 512
    while True:
        buffer = ctypes.create_unicode_buffer(size)
        needed = k32.GetFinalPathNameByHandleW(handle, buffer, size, 0)   # VOLUME_NAME_DOS
        if not needed:
            raise _last_error("open file")
        if needed < size:
            path = buffer.value
            if path.startswith("\\\\?\\UNC\\"):
                return "\\\\" + path[8:]
            return path[4:] if path.startswith("\\\\?\\") else path
        size = needed + 1


def pread(fd: int, n: int, offset: int) -> bytes:
    """At most `n` bytes of the file open as `fd` from `offset`, by ReadFile
    with the offset given: the file's position is never relied on."""
    import msvcrt
    ctypes, wintypes, k32, _, Overlapped = _k32()
    buffer = ctypes.create_string_buffer(max(n, 1))
    at = Overlapped(None, None, offset & 0xFFFFFFFF, offset >> 32, None)
    done = wintypes.DWORD()
    if not k32.ReadFile(msvcrt.get_osfhandle(fd), buffer, n, ctypes.byref(done),
                        ctypes.byref(at)):
        if ctypes.get_last_error() == _ERROR_HANDLE_EOF:
            return b""
        raise _last_error("read")
    return buffer.raw[:done.value]
