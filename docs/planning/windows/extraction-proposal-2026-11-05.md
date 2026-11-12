# Proposed: narrow Windows extraction for received and local zips (for Matt's yes/no)

Not implemented (held for Matt). Opening any package or bundle zip on Windows fails today at
`bundle_archive.extract` (`bundle_archive.py:388`, `os.O_NOFOLLOW` does not exist),
measured in the loopback smoke: `POST /api/packages/local` returned 500 for both Tim's
repacked handoff and the `mic-check-page` example, after draft_files' held walk had
read the zip correctly. Nothing was left in `packages/` by either failure.

Standard library and ctypes only, built on the approved A and B primitives. No new
public interface, dependency, setting, wire or schema change. macOS and Linux keep
today's code path unchanged.

## Scope: two call sites, Windows branch only

| Call | Today | Windows proposal |
|---|---|---|
| `work_packages.unpack_held` (`:421-437`) | the zip copied to `packages/.partial-<uuid>.zip` with `os.open(O_EXCL, 0600)`, which on Windows inherits the folder's ACL, then reopened by path for `unpack` | created exclusively, owner-only, through the pinned `packages/` folder handle; the same descriptor is handed to `unpack` as a file object, so the zip checked is the bytes written, with no reopen by name |
| `bundle_archive.extract` (`:358-410`) | staging `mkdir(0o700)`, `os.open(O_EXCL\|O_NOFOLLOW)` per file, `os.rename` of the staging folder | `_win_extract`: the same walk over the same `Listing`, with every folder and file created relative to held handles (below) |

`work_packages.save_result` (`dir_fd` writes from a page package) stays closed and is not part of this.

## API (internal; the public `extract` and `unpack_held` keep their signatures)

```python
# jarvis/_win_handles.py
def create_dir(parent: DirHandle, name: str, security_descriptor: int) -> DirHandle
    # NtCreateFile FILE_CREATE | FILE_DIRECTORY_FILE | FILE_OPEN_REPARSE_POINT, relative
    # to `parent`; FileExistsError if the name exists in any form (long, 8.3, case).
def create_file(parent: DirHandle, name: str, security_descriptor: int) -> int
    # NtCreateFile FILE_CREATE | FILE_NON_DIRECTORY_FILE, share 0, relative to `parent`;
    # returns the handle. Never FILE_OPEN_IF / FILE_OVERWRITE*: no existing file is reused.
def rename_dir_into(handle: int, parent: DirHandle, name: str) -> None
    # FileRenameInformation with ReplaceIfExists = FALSE, RootDirectory = parent.

# jarvis/private_files.py
def staged_dir(parent: Path) -> ContextManager[tuple[DirHandle, DirHandle]]
    # (held parent, new owner-only staging folder in it), removed on exit unless the
    # caller renamed it into place. The parent is pinned as for create_new/replace
    # (_pin_folder: no delete sharing, Q2 folder rules, no reparse point).

# jarvis/bundle_archive.py
def _win_extract(archive, listing, destination, *, folder, budgets) -> Path
```

## Rules

1. **Budgets unchanged.** The entry count and per-entry checks stay in `list_members`;
   every byte still goes through `_read_into` and `_Spent`, which count decompressed bytes,
   the total, the ratio past `ratio_floor`, depth and `max_seconds`. Only the `write`
   callable changes (WriteFile on the created handle).
2. **Exclusive creation, no overwrite.** Every folder and file is created with `FILE_CREATE`
   relative to the folder handle above it. A name that already exists in any form fails,
   whether as its long name, its 8.3 name or another case, and fails the whole extraction.
   The staging folder is new and empty and only this call writes into it. The final rename
   uses `ReplaceIfExists = FALSE`: an existing destination is `FileExistsError`, as today.
3. **Aliases refused.**
   - **At listing time**, so a bundle's per-entry listing shows the entry as blocked: any
     path component that Windows would not take as itself is refused as `bad-name`. That
     is `_win_handles.name_refused`: `:` (streams), `<>"|?*`, a trailing dot or space,
     and DOS device stems with any extension, superscript digits included.
   - **Also at listing time:** a component shaped like an 8.3 short name with a `~digit`
     tail (`^[^.~]{1,6}~[0-9]{1,6}(\.[^.]{0,3})?$`, any case), so `CREDEN~1.JSO` cannot
     stand in for `credentials.json`.
   - **The existing case and NFC collision check** (`casefold` of the NFC form) stays as it
     is.
   - **After each create**, the new handle's own name (normalised final path) must equal
     the requested component exactly. Otherwise the extraction is refused.
4. **Links refused.** Link, device and encrypted entries are already refused by
   `_kind_refused`. Every create and open passes `FILE_OPEN_REPARSE_POINT`, and a parent
   is only ever a handle this call created. Nothing is opened by a path below `packages/`.
5. **Owner-only.** The staging folder is created with the A descriptor
   (`O:<user>D:P(A;OICI;FA;;;<user>)(A;OICI;FA;;;SY)`), so the package folder renamed into
   place is owner-only. Files carry the same descriptor explicitly.
   - `keep_modes` has no meaning on Windows and is ignored. No execute bit exists to keep or
     drop.
6. **Handles before the rename.** Every file and subfolder handle is closed before the
   staging folder is renamed. NTFS refuses to rename a folder while anything below it is
   open; this was measured, WinError 5. Only the staging folder's own handle, with DELETE
   access, and the pinned parent stay open.
7. **Failure.** Any error removes what was created, by handle and in reverse order of
   creation (`FileDispositionInformation`), never with a path-based `rmtree`. The parent's
   pin means the staging folder cannot have been moved meanwhile. The error comes back as
   today's `ArchiveError` or `PackageError`.

## Safety criteria

1. No byte is written outside the new staging folder, and no existing file is opened for
   writing.
2. No entry name reaches a file under any name other than exactly itself.
3. The budgets refuse the same archives they refuse on macOS. The same fixtures give the
   same `ArchiveError` reasons, apart from the new Windows-only `bad-name` ones.
4. The unpacked package and the partial zip are owner-only from creation.
5. On failure, nothing is left in `packages/`. On success, exactly one new folder appears.
6. macOS and Linux behaviour is unchanged.

## Native tests (Windows host)

- Each existing `bundle_archive` and `work_packages` fixture (traversal, absolute, drive,
  backslash, links, devices, encrypted, case and NFC collisions, folder/file conflict, zip
  bomb by ratio and by total, too deep, too many entries) gives the same result as on
  macOS.
- New Windows-only fixtures:
  - `CON.txt`, `a.`, `b `, `x:y`, `CREDEN~1.JSO` next to `credentials.json`, and `Ä` vs
    `Ä`;
  - an entry whose path exceeds 260 characters, which extracts with
    `LongPathsEnabled=0`.
- **Swap tests:**
  - a junction planted at the destination name before the rename gives `FileExistsError`,
    and the junction's target is untouched;
  - with a test hook, the staging folder renamed while a file is being written is refused
    by NTFS, and the extraction fails closed.
- **ACL:** `Get-Acl` on the unpacked folder and a file shows criterion 1 of A.
- **End to end:** the smoke reopens Tim's repacked handoff and `mic-check-page`. It checks
  that `package.json` is recognised, that nothing runs, and that
  `GET /api/packages/<id>/files/index.html` is byte-identical to the source. Each zip is
  then opened twice, and the second time gives the same package (`fresh=false`).

## Questions

- **E1:** Should files unpacked from a received zip get Windows' Mark-of-the-Web
  (`Zone.Identifier`)? macOS deliberately clears quarantine for packages
  (`work_packages.unquarantine`). On Windows that call is a macOS `xattr` step, which fails
  and is logged.
  - Recommended: add no mark, matching the macOS outcome.
  - Separately, make `unquarantine` a no-op on Windows, so it isn't logged as a failure.
- **E2:** Is it right to refuse the 8.3-shaped names (rule 3) for bundles that are only
  inspected? It is an over-refusal for a real file named, say, `FOO~1.TXT`. Recommended:
  yes, since the check is the only reliable defence against aliases.
