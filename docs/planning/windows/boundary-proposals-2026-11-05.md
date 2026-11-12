# Proposed boundaries for native Windows startup (for Matt's yes/no)

Both are standard library + ctypes only, no new dependency, no UI/wire/storage-schema change.
On POSIX each one is the current code moved verbatim behind the same call, so the existing
tests must pass unchanged. Nothing here is implemented; until approved, `folder_readers`
stays in the explicit closed "unsupported on Windows" state (commit 6404dc4), and the
full-app start stays on hold. Drafted by an Opus helper from the code at 7233729 + 6404dc4.

## A. Private files: `jarvis/private_files.py`

### Who needs it (grep of `O_NOFOLLOW|fchmod|getuid|0o600|0o700|pread`)

| Caller | When it runs | Tier |
|---|---|---|
| `data_paths.ensure_tool_token` (:579-635) | every start, every tool call (`web_auth:523`, `server:4077`), `update.py` | **startup** (second start fails today) |
| `server._write_mcp_config` (:1252-1265, `chmod(0o600)` after `write_text`) | every brain spawn; holds connection tokens | **startup** (no crash, but `chmod` gives no protection on Windows) |
| `codex_provider.write_config` (:208-213) | Codex brain only | startup when Codex is the brain |
| `messaging_files.private_dir` / `_open_new` (:138-153), `attachment_locations._real_folder` (:521), `_verified_copy` (:539-541) | once signed in to a team server (cleanup loop, snapshots, downloads) | second tier: signed-in startup raises `AttributeError: getuid` |
| `access_keys._write` (:95-103) | `python -m jarvis.access_keys create/revoke` | later |
| `bundle_cache` (:63-65, :88, :103), `bundle_inspection._private_copy` (:118), `bundle_archive` (:373-388) | opening received files | later |

Proposal covers only rows 1-3 now (row 4 uses the same calls, its port is a later step).

### API

```python
class NotPrivate(OSError):
    """The path exists but is a link/reparse point, not a regular file or
    directory, or not owned by this user. Never deleted or repaired by us."""

def ensure_dir(path: Path) -> Path
    # path and missing parents, owner-only; NotPrivate if path is a link or someone else's.
def create_new(path: Path, data: bytes) -> None
    # exclusive create, owner-only from the first instant; FileExistsError if present.
def adopt(path: Path, max_bytes: int = 4096) -> tuple[bytes, Callable[[bytes], None]]
    # open an existing file through ONE handle without following a link; check it is a
    # regular file this user owns; make it owner-only again (the fchmod step); return its
    # bytes and a writer that truncates+writes through the same handle (tool token's
    # "empty file is ours to fill").
def replace(path: Path, data: bytes) -> None
    # owner-only temp beside it, flushed, renamed over `path`; never a partial file.
```

`ensure_tool_token` becomes `create_new`, else `adopt`; `_write_mcp_config` and
`codex_provider.write_config` become `replace`.

POSIX: exactly today's idioms (`O_CREAT|O_EXCL` 0600, `O_NOFOLLOW` + `fstat` + `st_uid ==
getuid()` + `fchmod`, `mkdir(0o700)`+`chmod`, temp + `os.replace`).

Windows:
- **Who "this user" is.** The process token's user SID (`GetTokenInformation(TokenUser)`).
  SDDL at creation `O:<user SID>D:P(A;;FA;;;<user SID>)(A;;FA;;;SY)`, applied through
  `CreateFileW(..., lpSecurityAttributes)` with `CREATE_NEW`. Owner is set explicitly
  because an elevated token otherwise makes `BUILTIN\Administrators` the owner. For
  directories, `CreateDirectoryW` with the same descriptor plus `(OI)(CI)` inheritance.
- **Opening without following.** `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS`
  (dirs). A handle with `FILE_ATTRIBUTE_REPARSE_POINT` (junction, symlink, or any other tag)
  is `NotPrivate`. So is anything that `GetFileType` does not report as `FILE_TYPE_DISK`.
- **The owner check.** `GetSecurityInfo(handle, OWNER|DACL)`, on the same handle as the read.
- **adopt.** The handle is opened with `GENERIC_READ|GENERIC_WRITE|READ_CONTROL|WRITE_DAC`.
  The protected DACL is re-applied through `SetSecurityInfo` on that handle (the `fchmod`
  analogue). The probe's failure was a handle without `WRITE_DAC`; with it this is
  expected to work, which is unverified.
- **replace.**
  - Uses `SetFileInformationByHandle(FileRenameInfoEx, REPLACE_IF_EXISTS|POSIX_SEMANTICS)`
    on the temp's handle. It falls back to `MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)`
    where POSIX semantics are refused (FAT and some network shares).
  - A destination that another process holds open without `FILE_SHARE_DELETE`
    (the probe's WinError 5) is retried a bounded number of times, about 10 × 50 ms.
    After that it raises `PermissionError` and leaves the old file intact.
- **Not done:** no encryption, no Credential Manager (that is SecretStore, a separate
  decision), no protection from an elevated administrator or SYSTEM (the macOS
  root-equivalent), no deletion of a foreign file, no change to non-secret files (DB,
  usage.json, server-address.json keep inherited ACLs).

### Safety criteria (each checkable)
1. A file made by `create_new`/`replace` has, from its first instant, owner = user SID, a protected DACL with exactly two ACEs (user FA, SYSTEM FA), and no inherited ACE. No moment exists where it has the parent's ACL.
2. A junction, symlink or any reparse point at the path, or on a component walked, is `NotPrivate`. Its target is not read, written, truncated or re-ACL'd.
3. A file owned by another SID is `NotPrivate`. It is not repaired and not deleted.
4. `adopt` reads, checks and repairs through one handle. There is no second path lookup.
5. `replace` never leaves a partial or a looser-ACL file at `path`. On failure the old content stays.
6. Secrets never reach logs. Error messages name the path, never the contents.
7. POSIX behaviour and tests are unchanged.

### Native test criteria (Windows host)
- **Create and DACL:** `create_new`, then `GetSecurityInfo` (and `icacls`) shows criterion 1. Run once elevated and once from an ordinary user; the ordinary-user run needs a non-admin account Matt creates.
- **Second start:** `ensure_tool_token` twice in fresh processes returns the same token. This is today's regression.
- **Adopting a legacy file:** a file created with an inherited ACL (today's first-start token) is adopted and ends with criterion 1. Its owner is the user, or `Administrators` when created elevated: **open question Q1** below.
- **Links:** a junction and a file symlink planted at the token path (symlinks need elevation or developer mode) give `NotPrivate`, and the target's bytes and ACL are unchanged.
- **Foreign owner:** a file `SetSecurityInfo`'d to owner `S-1-5-32-545` (Users) is refused.
- **Open destination:** another process holds the destination open without share-delete, then `replace` runs. It retries, then raises, and the old file is intact. With share-delete the replace succeeds.
- **Second user:** user B cannot open user A's token, `mcp.json` or brain-home files (needs the second account; manual, marked test).
- **Same user:** a same-user child process can read the token, which is how the MCP child gets it.

### Open questions
- **Q1:** A legacy file owned by `BUILTIN\Administrators` (written by an elevated run): adopt it and set owner = user when the token has Administrators enabled, or refuse it? Recommended: adopt only while elevated, and log it.
- **Q2:** Should startup also check the data root's DACL? A checkout under `C:\` (not the profile) inherits `Authenticated Users: Modify`, so another user could swap non-secret files. Recommended: warn in preflight, don't tighten.

## B. Contained reader backend for `folder_readers` on Windows

### Callers to keep working
- `folder_service` uses `kind_of`, `list_dir`, `read_text` and `read_transcript`.
- `draft_files.Source.open` uses `open_file` and reads with `os.pread`.
- `work_packages.page_file` uses `opened_file` and reads with `os.pread`.
- `work_packages.save_result` uses `opened_dir` plus `dir_fd` writes. It stays closed on Windows; it is a later decision.

The public functions and dataclasses are unchanged. The budgets come from `folder_contract`
(page 100, scan 10,000, 2 s, 200 lines / 64 KiB). Only the internals get a Windows branch.

### Internal API (`jarvis/_win_handles.py`, Windows only, imported lazily)

```python
@dataclass(frozen=True)
class FileIdentity:
    volume_serial: int
    file_id: bytes                      # FILE_ID_INFO, 128-bit (ReFS-safe)

class DirHandle:                         # context manager, closes the HANDLE
    identity: FileIdentity

def open_root(canonical: Path) -> DirHandle
    # CreateFileW(r"\\?\" + canonical, FILE_LIST_DIRECTORY|FILE_READ_ATTRIBUTES|SYNCHRONIZE,
    #   share R|W|D, OPEN_EXISTING, BACKUP_SEMANTICS|OPEN_REPARSE_POINT); reparse -> forbidden.
def open_child_dir(parent: DirHandle, name: str) -> DirHandle
def open_child_file(parent: DirHandle, name: str) -> tuple[int, os.stat_result]
    # both: NtCreateFile with OBJECT_ATTRIBUTES.RootDirectory = parent (the openat analogue),
    # FILE_OPEN_REPARSE_POINT, FILE_DIRECTORY_FILE / FILE_NON_DIRECTORY_FILE, FILE_OPEN only.
    # file -> msvcrt.open_osfhandle(handle, O_RDONLY|O_BINARY) so callers keep an int fd.
def entries(parent: DirHandle) -> Iterator[RawEntry]
    # GetFileInformationByHandleEx(FileIdExtdDirectoryRestartInfo / FileIdExtdDirectoryInfo),
    # 64 KiB buffer: name, attributes, size, last write time, file id. No path lookups.
def pread(fd: int, n: int, offset: int) -> bytes
    # ReadFile(get_osfhandle(fd), OVERLAPPED{offset}); folder_readers.pread = os.pread on POSIX.
```

**Rules:**
- **Names.** A component is refused (`forbidden`) when it:
  - contains `\`, `/`, `:` (blocks alternate data streams), or NUL;
  - is `.` or `..`;
  - ends in `.` or a space;
  - has a stem that is a DOS device name (CON, PRN, AUX, NUL, COM0-9, LPT0-9, with the superscript variants).

  Such entries are still listed, as kind `other`, and are never opened.
- **Exact name.** After each open, the child's long name, read through `entries` or
  `FileNameInfo` on the new handle, must equal the requested component exactly. This
  refuses 8.3 short names (`CREDEN~1.JSO`) and case variants (`.SSH`), so
  `folder_policy`'s sensitive-name rules cannot be bypassed by an alias.
- **Kinds.**
  - Any `FILE_ATTRIBUTE_REPARSE_POINT` is `link`: never followed, never opened.
  - A directory is `dir`.
  - A file is `file` only if `GetFileType` reports a disk file and it has no reparse
    attribute.
- **Paths.** Root opens use `\\?\`. Every deeper open is handle-relative, so no
  MAX_PATH limit applies inside a folder.
- **Root identity.** `open_root` compares `FileIdentity` with the identity
  `folder_locations` stored as evidence; a mismatch is `missing`, so the availability
  check reconnects it.
- **Errors.** Sharing violations and access denied map to `access_denied`; not found
  maps to `missing`; a reparse point maps to `forbidden`.

### Safety criteria
1. No read reaches a file outside the canonical root through a junction, symlink, mount point or other reparse point, whether it is present beforehand or swapped in mid-walk.
2. Every component is opened relative to the handle before it. There is no path-string reopen after a check.
3. One file has one accepted name. Short names, case variants and stream names are refused.
4. The page, scan, time, line and byte budgets are the same as on POSIX, and no file is read whole.
5. Hard links are not detectable here, the same as on POSIX: parity, not a new guarantee.
6. Until this is approved and passes its tests, every reader entry point raises the closed unsupported state, and nothing falls back to path-based `open()`.

### Native test criteria
- **Junction swap:** a walk is paused (test hook) between components, the next component is renamed away, and a junction to a directory outside is put in its place. The read raises `forbidden` or `missing` and never returns outside bytes. Repeated for a file symlink (elevated).
- **Present links:** a junction and a symlink already inside the root are listed as `link`, and `read_text` on them is `forbidden`.
- **Aliases:** a file whose policy-hidden name is `.ssh`; asking for `.SSH` or its 8.3 name (when 8.3 creation is on for the volume, checked with `fsutil 8dot3name query`) is refused.
- **Name hazards:** `CON`, `aux.txt`, `a.` and `x:stream` are refused to open and listed as `other` where they exist.
- **Long paths:** a file whose full path is over 260 characters under a root is readable with `LongPathsEnabled=0`.
- **Budgets:** a directory of 12,000 entries gives `partial=True` at 10,000. A 100 MB file's window reads at most 64 KiB at an offset.
- **Locked file:** a file held open without share-read gives `access_denied`.
- **Callers:** `draft_files.Source` and `work_packages.page_file` read through `pread` and agree byte for byte with the source.
- **Users:** both test runs above are repeated as an ordinary user, since elevation masks `access_denied`.

### Open questions
- **Q3:** Use `NtCreateFile` (ntdll, documented in winternl; the true openat) for relative opens? The alternative is `OpenFileById` with the id from the parent's listing. Recommended: `NtCreateFile`.
- **Q4:** OneDrive and cloud placeholders are reparse points, so they would show as `link` and be unreadable. Accept this for now?
- **Q5:** Does `os.fstat` on an `open_osfhandle` fd of a directory work? It is not needed if directories stay `DirHandle`, which is proposed.
