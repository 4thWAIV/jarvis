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
