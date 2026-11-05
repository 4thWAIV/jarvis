# Windows native probe gate: first results (5 November 2026)

Evidence from the first native probes on the Windows host, against
`feature/windows-port` at `7233729` (main `b6714fa`). Tim's plan is
`reconciliation.md` beside this file; this records what was observed, what
it changes, and which decisions are Matt's. Nothing here is a support claim.

## Host and toolchain (observed)

| Item | Observed |
|---|---|
| OS | Windows 11 Pro 10.0.26200, x64, Hyper-V virtual machine, 4 logical CPUs, 16 GiB |
| Session | RDP (`RDP-Tcp#0`), user `wontseemecomin`, **elevated (High integrity, Administrators)**. Ordinary-user operation is not yet qualified. |
| Python | 3.12.10 (python.org, `py -3.12`) |
| Node / npm | v22.23.4 / 10.9.9 |
| Git | 2.56.0.windows.4, `core.autocrlf=true`, `core.symlinks=false`, no `.gitattributes` (working tree is CRLF) |
| Claude Code | 2.1.302, winget native install (`%LOCALAPPDATA%\Microsoft\WinGet\Links\claude.exe`, a symlink); on the user PATH but absent from PATH inherited by already-open shells |
| Codex | **not installed** |
| Browser | Edge 156.0.4314.58; **Chrome not installed** (the voice page is only tested in Chrome) |
| OpenSSL | 3.5.9, from Git for Windows (`C:\Program Files\Git\usr\bin\openssl.exe`) |
| Long paths | `LongPathsEnabled = 0` |
| Dependencies | `pip install -r requirements.txt` succeeds natively, including `sherpa-onnx==2.0.0` |

Bundle `jarvis-7233729.bundle` verified: SHA-256 `3108203e…cf7a614` as briefed,
complete history, heads `b6714fa` (main) and `7233729` (feature/windows-port).

## Safe import

Importing every `jarvis` module in its own interpreter with isolated data: 95
import, 21 fail. Nothing is written to the data directory by importing.

1. 19 fail through `folder_readers.py:38` (`os.O_DIRECTORY`, also `O_NOFOLLOW`,
   `O_NONBLOCK` at `:39`), including `jarvis.server`.
2. `usage_scan.py:161`: `datetime(2, 1, 1).timestamp()` raises
   `OSError [Errno 22]`. Windows' C runtime refuses naive local conversions
   before 1970 (and `fromtimestamp` refuses negative stamps and years past
   3000), so `_DAY_MIN`/`_DAY_MAX` and `day_key` need Windows-safe bounds.
3. `migrations/001_dispatches_to_runs.py` is not importable by name by design.

With only (1) and (2) shimmed in a probe process, `jarvis.server` imports
(141 routes). These two are the whole import gate.

Second start: `data_paths.ensure_tool_token` (`:617`, `:622`, `:624`) uses
`O_NOFOLLOW`, `getuid` and `fchmod`, so the first start creates the token
(with an inherited ACL; `0o600` is ignored) and **every later start fails**.

## Process inspection (commit 7233729), native

`process_inspect.inspect_process` on the real kernel32: self, parent, an own
child (running, then exited), a child exiting with 259 (`exited_with_still_active_code`),
pid 0 and 2**32 (`invalid_pid`), an unused pid (`no_such_process`), identity
match (`alive`) and mismatch (`identity_mismatch`) all behave as designed.
System (pid 4) and lsass read `alive` only because the shell is elevated; an
ordinary user should get `access_denied` -> `unknown`, still to be checked.

**The roster's `procStart` is the native creation FILETIME.** For the live
session, `procStart` = `134383483135624848` = the token `GetProcessTimes`
gives (`win-filetime:134383483135624848`). Each session's published inbox key
also carries it (`procStartFt`). Production PID-reuse detection on Windows is
therefore available inside the approved interface: pass
`Identity(pid, "win-filetime:" + procStart)`. Not on macOS, where
`process_inspect` reports no identity and an expected one would make every
session `unknown`.

Focused tests natively (`test_process_inspect.py`, `test_session_watch.py`):
134 passed, 2 skipped, 5 failed:

- two tests fake `os.kill` (the POSIX branch), so on Windows the real kernel
  answers: a test defect in 7233729;
- `test_config_roots_includes_both_defaults_and_the_env_extras` joins with
  `:` while the code correctly splits on `os.pathsep`: a test defect;
- two fail with `WinError 206` (MAX_PATH): pytest's `%TEMP%` path plus a long
  encoded project folder exceeds 260 characters with long paths disabled. A
  real product risk for long project paths, not only a fixture problem.

Known defect confirmed: `session_watch.py:2043` announces `finished` on
`WORKING -> UNKNOWN`, and since 7233729 `UNKNOWN` also means "could not be
inspected", so a failed inspection announces a false completion.
`claude_activity` (`:1481-1483`) also inspects each PID twice per poll.

## Claude Code session delivery, native

Read from Claude Code 2.1.302's own bundle, then proved:

- `messagingSocketPath` is a named pipe, `\\.\pipe\LOCAL\cc-msg-<32 hex>`.
  `session_steer.post_to_session` (`AF_UNIX`, `Path.exists`) cannot reach it.
- On a pipe the auth line is **required**. Each session publishes its peer key
  at `~/.claude/sessions/<pid>.<sha256(canonical pipe path)>.key`, JSON
  `{peerToken, procStartFt, pidDomain}`, where the canonical path is
  `\\.\pipe\` plus the name with ASCII A-Z lowercased (verified against the
  live key file name). Wire: one connection, `{"type":"auth","token":peerToken}`
  then the `{"type":"user",...}` line, written promptly.
- Two disposable same-project sessions (plan mode, Haiku, `CLAUDE*` variables
  scrubbed) were each sent a nonce through their own pipe with their own key.
  Each transcript (`~/.claude/projects/C--Users-…-jarvis-windows/<id>.jsonl`)
  recorded only its own nonce, wrapped as "Another Claude session sent a
  message:", and each replied with exactly that nonce. A message with no auth
  line and one with the other session's token were **silently dropped**
  although the pipe write succeeded, confirming that `sent` is not receipt.
- `encode_cwd` matches the native transcript folder name.
- Both idle sessions reported `status: waiting`, `waitingFor: "dialog open"`
  for their whole life while accepting and answering peer messages. Under
  `HUMAN_HAND_REASONS` (`session_watch.py:834`) a prompted session in that
  state is `NEEDS_YOU` and not between turns, so JARVIS would queue its second
  message indefinitely. Which dialog this is needs a visible re-probe.

## Filesystem, private storage and host (native, elevated)

- `os.open(..., 0o600)` ignores the mode; the file inherits the parent ACL
  (`%LOCALAPPDATA%`: SYSTEM, Administrators, the user). A protected DACL
  (user + SYSTEM) set at creation through `CreateFileW` with a security
  descriptor (ctypes, standard library) gives no window; fixing the ACL after
  `os.open` fails (handle lacks WRITE_DAC). Same-user children can read it.
  No second ordinary user exists, so "another user cannot read" is inferred
  from ACLs, not tested.
- Missing in Python 3.12 on Windows: `pread`, `pwrite`, `O_DIRECTORY`,
  `O_NOFOLLOW`, `O_NONBLOCK`, `getuid`, `fchmod`, `dir_fd` support,
  `scandir(fd)`; `st_uid` is 0.
- `os.open` follows junctions and symlinks. A rename-and-junction swap made a
  path-based open read a file outside the root; opening each component with
  `FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS` exposes the
  reparse attribute and a different file index, so a component-by-component
  handle walk can refuse it. `fstat().st_ino` equals the file index.
- `os.replace` fails (WinError 5) while the destination is open, even with
  FILE_SHARE_DELETE.
- Reserved names on this build: `CON`, `aux.txt`, `COM1.log` are created as
  ordinary files, `NUL` is swallowed, trailing dots and spaces are stripped
  (collisions), `:` and `<>|?*` are refused.
- Installed (non-checkout) root resolves to `~\Library\Application Support\JARVIS`.
- Memory from `GlobalMemoryStatusEx` agrees with CIM. CPU from
  `GetSystemTimes` is not yet validated against an independent reading.

## Second session (same day, after a VM restart): what was fixed and measured

Commits `6404dc4`..`0db0e18`, each with its native evidence in its message.
Tests run with `--basetemp` under `%TEMP%\jb` unless noted (see MAX_PATH below),
and without UTF-8 mode unless noted.

| Area | Result |
|---|---|
| Import gate | All 115 importable modules import; `jarvis.server` has 141 routes (144 once `jarvis/web` is built); nothing written to data. Folder reads refuse as `unsupported` on Windows (16 reader tests skip there, a new test checks the refusal). |
| Process identity | Roster `procStart` re-confirmed equal to the native token after the restart. Each entry inspected once a poll; a reused pid (real child, wrong token) is `exited`; uninspectable `unknown` no longer announced as finished. Roster read as UTF-8 (cp1252 misread shown natively). |
| Installed root | `%LOCALAPPDATA%\JARVIS` from the known-folder API (equals `LOCALAPPDATA`); checkout and `JARVIS_DATA_DIR`/`JARVIS_ENV_FILE` overrides unchanged; spaces and accents fine; two starts persist. |
| Host metrics | CPU 3.3 vs 3.4 % idle and 53.9 vs 53.6 % with two of four CPUs busy against `\Processor(_Total)\% Processor Time`; memory total equals CIM, used within 25 MB. |
| Team secrets | Credential Manager store exercised with generated values under a unique namespace: per-server credential, persist local machine, isolation, read from a second process, replace, delete; nothing left. |
| Templates | Persona and connections templates LF via `.gitattributes` and synced as bytes: fresh Windows clone, its own frontend build, wheel and sdist, clean install, isolated two-start sync `seeded` then `current`. A persona written by the old code is kept unchanged. |
| Claude launch | `shlex.split` stripped the real path's backslashes; now Windows rules. Through `ClaudeSession`: 23 arguments byte-exact from a spaced, accented launcher; the real `claude.exe` from a spaced hard link answered a nonce on `claude-opus-5-5` and exited on close. |
| Frontend | `npm ci` + `npm run build` pass (tsc clean, 187 modules; the existing >500 kB chunk warning). |
| Wheel | Built from an export outside the checkout with the frontend; installed in a clean venv outside any checkout: `jarvis --help` exits 0, CI's import assertions hold, `CHECKOUT` false. |

Still failing natively, none caused by these commits (failing sets diffed against
the prior HEAD):

- **MAX_PATH:** four tests fail at pytest's default `%TEMP%\pytest-of-<user>\pytest-N`
  base (two encoded project folders exceed 260 characters) and pass under a short
  base. Product risk for long project paths remains while `LongPathsEnabled=0`.
- **Process termination:** 13 brain/run-executor tests (killpg, SIGTERM, children
  left running, `WinError 87`); waits on the termination interface.
- **Private files:** `ensure_tool_token` (`O_NOFOLLOW`) fails 61 tests that need the
  tool token (brain/page parity and others); waits on the private-file boundary
  proposed in `boundary-proposals-2026-11-05.md`.
- **Test defects:** text-mode writes giving CRLF (`test_usage_scan`), locale-encoded
  reads at collection (`test_messaging_client.py:485`, `test_messaging_api.py`) and
  of the page catalog (`test_team_client`), all passing or collecting in UTF-8 mode.

Held for owner review: reading Claude Code's published peer key for the named-pipe
inbox. Claude Code 2.1.302's own sender finds a target's key file by the hash of its
canonical pipe path and checks the owner is alive with a matching `procStart`, so it
is Claude Code's same-user peer channel; no code reads it yet.

## Not yet probed

Codex discovery and delivery (Codex not installed); an exact-target prompt
answer route in Windows Terminal; recognizer model provisioning and fixed-PCM
inference; screen capture, URL inspection, notifications, received-file
marking and Explorer reveal; ordinary-user and second-user behaviour; a
full-app start (deliberately not attempted).
