# Windows support: reconciled execution plan

Updated 31 October 2026 against `61c9761f47eed67c1754144e1ba4bcea5abbec49`. Workflow: **Windows plan reconciliation** (`wf-054417dbc31f`). Three read-only agent audits covered foundations, providers, and desktop/voice/release. The integration review checked their source evidence and removed overlapping assignments.

**Remaining implementation budget: 46–85 active agent-hours.** With three implementation workers and an integration owner, the modeled critical path is **25–47 active elapsed hours**, approximately **4–6 eight-hour working days** after host access and sign-ins are ready. These are source-based planning allowances, not measured agent throughput or a delivery commitment. Native probe failures trigger a package-specific reforecast.

This work updated the plan and estimate. It did not implement Windows support, install dependencies, run native Windows tests, change production, or qualify audio hardware. Current Windows host availability was not established by this audit.

## What changed since the earlier plan

The 28 October plan used main `73190ca` and team-client branch `98124d5`. The audited checkout now contains team pairing, messaging, file transfer, presence, host resource reporting, persistent local folders, and brain/page tools for those capabilities.

| Earlier material | Current evidence | Consequence |
|---|---|---|
| Team-client row: “main has no team client.” | `jarvis/team_client.py` and `team_api.py` are in this checkout. | Reuse pairing and connection management. Implement and qualify the Windows `SecretStore` backend. |
| Messaging row: “Messaging and presence stay deferred.” | `messaging_runtime.py`, `messaging_client.py`, `messaging_files.py`, `team_api.py`, and `host_resources.py` exist. | Include the existing messaging, attachment and network features in Windows acceptance. Do not build another team server. |
| Generic filesystem review | `folder_readers.py:38–39` now evaluates POSIX-only constants at import, reached through `server.py:120` → `folder_service.py:40`. | A new startup prerequisite and a scoped-reader implementation, not just path formatting. |
| Desktop and voice adaptation | The audited desktop and voice files have no changes from `73190ca`. | Retain their Windows work; current Mac behavior is not native Windows evidence. |
| Mac-only CI | CI now shares setup and builds the frontend once, but still uses `macos-latest`. | Extend the existing workflow; retain its reduced duplicate-run structure. |
| 33–66 aggregate agent-hours; 18–32 elapsed hours | New local-folder and attachment security/desktop work, explicit process identity and prompt targeting, and wider release coverage. | Replace the prior total with 46–85 aggregate hours and 25–47 modeled elapsed hours for the scope below. |

The estimate increase is 13–19 aggregate hours at the range endpoints. It reflects changed scope and clearer accounting, not measured slowdown. Existing team and UI implementation removes the need to recreate those features; no numerical saving is claimed without execution evidence.

## Target and scope

Proposed initial release target: native Windows 11 x64, retaining macOS Intel and Apple Silicon behavior. Start native qualification with Python 3.12 and Node 22, matching current CI. `pyproject.toml` advertises Python >=3.11: test that minimum or explicitly change the supported-version policy before release. Node is a frontend build dependency; provider CLIs have their own requirements.

Included: current Python/React product, Claude and Codex brains and supported session operations, local folder catalog and scoped reads, team pairing, messages and files, network CPU/memory readings, current Kroko/sherpa-onnx input and Fish output, desktop tools, installation and CI. Preserve current provider capability differences rather than inventing Codex prompt-answer parity.

Separately scoped: Windows ARM64, Windows 10, WSL agent hosting, remote desktop qualification, .NET/Avalonia hosting, signed installer/auto-update service, new project/folder UI designs, new speech engines, and a new explicit echo-cancellation engine. Existing folders use local SQLite; the proposed project relationship model is not part of this port. Server infrastructure and MongoDB are not desktop prerequisites.

The separate shared AEC handoff remains relevant. Windows voice acceptance is included; implementing a new cross-platform echo engine is not. If existing processing fails required overlap or repeated-human-word tests, the corresponding acceptance gate stays open until shared work resolves it. A core preview is not a full-parity release.

## Evidence and implementation tasks

### Installed paths and private storage — foundation worker

**Installed paths: 1–2 hours.** `data_paths.py:26–27` still selects `~/Library/Application Support/JARVIS` outside a checkout. Implement Windows installed roots while preserving checkout isolation and explicit data overrides. Separate config overrides from data overrides. Test outside the source tree, paths with spaces/Unicode, persistence and first/second startup.

**Shared private-file operations: 3–5 hours.** `data_paths.ensure_tool_token` uses `O_NOFOLLOW`, `getuid`, and `fchmod`; `access_keys._write` uses `O_NOFOLLOW`; generated MCP and Codex configs rely on `chmod(0600)`. Define one platform boundary for private creation, reopen, replacement and directory access, then reuse it at all callers. A Windows permission failure must fail the affected operation. Test second-user access, reparse/junction substitution, concurrent create, writable parents and atomic replacement. Preserve same-user child access. Prove the native operations before committing to a binding/dependency.

### Existing folders and attachment storage — foundation worker

**Scoped folder readers: 3–6 hours.** `folder_readers.py` uses `dir_fd`, `scandir(fd)` and `pread`, as well as import-time POSIX constants. Port the underlying operations without replacing race-resistant containment with resolve-then-read. Retain policies, bounded reads, pagination and cursor behavior. Qualify drive/UNC paths, case aliases, Unicode, reserved names, reconnects, symlink/junction replacement, and filesystem identity. Existing `folder_store.py`, `folder_policy.py`, `folder_service.py` and frontend are reused.

**Attachment storage: 1.5–3 hours.** `messaging_files.private_dir`, `_open_new`, `_hash_file`, and `_read_copy` use Unix ownership/opening rules. Apply shared private storage to immutable snapshots and verified downloads. Preserve byte hashes, cancellation cleanup, retries and revision-bound manifests. `download_path()` currently uses the manifest filename directly; introduce a Windows-safe local filename mapping for device names, trailing dots and alternate-stream syntax without changing the approved display name or wire manifest. Desktop marking and reveal belong to the desktop package below.

### Team credentials and resources — foundation worker

**Team secrets: 1.5–3 hours.** `SecretStore` already defines get/set/delete; `TeamConnection` defaults to `KeychainStore`, which calls Apple's `security`. Add platform selection and a Windows backend. Reuse pairing, saved-session reuse, revocation and outage logic. Verify standard-user storage, server/account isolation, reuse after restart and delete/logout.

**Host metrics: 1–2 hours.** `host_resources.default_reader()` implements Mac and Linux and returns no Windows reader. Add a Windows `Reader` and validate CPU deltas and memory against observed host values. Preserve unknown, warming-up, stale and unsupported semantics. Do not substitute process CPU or load average for whole-host CPU.

Foundation subtotal: **11–21 hours**. Storage primitives precede scoped readers and attachment adaptation. Paths, credential backend and host sampler can be prepared independently.

### Provider and session work — provider worker

| Package | Hours | Work and completion evidence |
|---|---:|---|
| Non-destructive process identity | 1–2 | Replace `session_watch.pid_alive`'s `os.kill(pid, 0)` boundary. Preserve live disposable children during repeated discovery; handle invalid/reused PIDs, exit and access-denied/unknown state. |
| Native provider probes | 2–3 | Record actual installed CLI versions, Claude endpoint/auth shape, Codex schema/queue availability, same-project targeting and prompt-target feasibility. Observe receipt in the intended transcript. |
| Shared launch and bounded pipes | 2–4 | Replace repeated `shlex.split`/plain split assumptions with executable/argv contracts in Claude, Codex, run executor, auth and send paths. Test actual supported wrappers, spaces, Unicode and metacharacters. Make `AppServer._next` time out while reading, not only before blocking `readline`. Qualify async pipes in normal and reload startup. |
| Owned lifecycle and restart | 2–4 | Current kill/close paths address direct children and restart uses `os.execv`. Implement/prove race-free owned-tree cleanup and restart handoff. Test grandchildren, nested ownership, cancellation during spawn, hung pipes, retained origin/port/environment and no duplicate instances. |
| Native discovery and delivery | 3–6 | Replace `ps`/`lsof` and POSIX executable-name assumptions in `codex_sessions`. Qualify file URI/path identity, `state_5.sqlite` shape and actual queue semantics. Claude currently requires `Path(socket_path).exists()` and `AF_UNIX`. Use the observed native endpoint, not an assumed named-pipe design. Refuse ambiguous session association. |
| Login, usage and transcript qualification | 1–2 | Preserve provider-owned authentication and isolated homes. Claude usage currently reads Apple Keychain. Qualify native credentials without independently refreshing provider tokens. Preserve unknown usage/expiry. Exercise drive, case, UNC and resumed transcript fixtures. |
| Exact-target prompt implementation | 2–4 | Only after the native feasibility probe identifies a reliable route: implement supported-terminal prompt answers with target/lifetime checks. Never substitute foreground keystrokes silently. Retain current provider capability limits. |
| **Provider subtotal** | **13–25** | Includes process identity and prompt implementation once. |

Reuse `SessionProvider`, current normalized brain protocols, turn state, output draining, run queue/cancellation state and transcript tracking. `session_steer.py:78–94` already documents why successful transport writes do not prove target acceptance. Codex's newest-update association heuristic needs ambiguity tests, not an assumption of correctness on Windows.

The blocking-read timeout issue in `codex_provider.AppServer._next` is platform-independent. It belongs to the shared launch/pipe package rather than a separate unbudgeted rewrite.

### Desktop work — desktop/client worker

| Package | Hours | Work and completion evidence |
|---|---:|---|
| Terminal/browser/editor launch | 1–2 | Replace AppleScript and `open` paths in `actions.py`; retain structured arguments, cwd, validation and explicit unsupported results. |
| Capture, scaling, windows and URL inspection | 2–4 | `screen.py` invokes `screencapture`, `sips` and AppleScript. Add native capture and enumeration; test mixed DPI, negative coordinates, multiple monitors, blank/protected/inaccessible capture. Actual URL inspection or an explicit unavailable capability; a title alone is not URL evidence. |
| Notifications | 1–2 | `notifier.available()` currently permits Darwin/osascript only. Implement and test standard-user installation identity and delivery, including OS-disabled notifications. |
| Received-file marking and reveal | 1–2 | `messaging_files.py:173–189` calls `open -R` and `/usr/bin/xattr`. Use a tested Windows downloaded-file marking mechanism and Explorer reveal without executing the file. Marking failure must not become success. |
| Capability/preflight integration | 1 | Reuse shared UI/error contracts and diagnostics. Expose available/unsupported states and retain Mac behavior. |
| **Desktop subtotal** | **6–11** | Prompt targeting belongs to the provider package, private snapshot operations to foundations. |

Existing attachment tests inject reveal/quarantine callbacks; live-transfer fixtures use no-op callbacks. Those successes do not qualify Windows marking or Explorer behavior.

### Existing voice path — desktop/client worker

**3–5 hours.** Qualify the pinned `sherpa-onnx==2.0.0` runtime and Kroko model hash/load on native Windows. Preserve existing streaming PCM, bounded queues and Fish output. Test first-word retention, backlog under representative CPU load, overload recovery, mute/reconnect, browser microphone permission, autoplay and output routing. Collect actual browser capture settings in diagnostics. Test human speech during playback, repeated words and stop requests with real devices. Coordinate shared AEC work once; do not build another recognizer or TTS system for this port.

`voiceInput.ts` requests browser echo cancellation; `speech.py` still has recent-word rejection. Browser constraints and fake-mic tests do not prove acoustic cancellation. The older AEC document's human-day implementation allowances are not silently added here. If explicit AEC is necessary, first perform its separately scoped **4–8 active agent-hour feasibility spike**, then estimate from the result.

### CI, installed release and integration

**Release implementation: 5–9 hours.** Allocate 2–3 hours to native Windows CI/fixture portability, 2–4 hours to installed-wheel and model-provisioning tests, and 1–2 hours to installation/upgrade documentation. Current CI uses `macos-latest`; wheel smoke hardcodes `/tmp/wheel-check/bin`. `scripts/setup_speech_input.py` is outside packaged `jarvis` resources, so provide a supported installed-package model setup route. Test an ordinary user without developer PATH, first/second startup, packaged assets, normal/reload startup and chosen loopback/TLS origin. Record actual Mac architecture results; one moving runner label is insufficient.

**Baseline/setup: 2–4 hours.** Record base commit, host and versions, isolate fixtures and agree actual platform interfaces before dependent implementation. This includes preparing the worktree and focused Mac baseline; host delivery and consent waits are outside the allowance.

**Integration/review: 6–10 hours.** One owner controls shared entrypoint/configuration changes, dependency contracts, merge order, cross-platform review and the acceptance report. This is coordination and review of merged work, not a second charge for package implementation tests. Allocate 3–5 of these hours to final integrated review; the remainder overlaps worker execution.

## Parallel execution and critical path

Start one integration owner and three bounded implementation lanes. Native probes are included within their package hours, not an extra fourth workstream.

| Lane | Work | Active hours | Dependency |
|---|---|---:|---|
| Foundation worker | Paths, private storage, folders, attachments, team secrets, host sampler | 11–21 | Private storage contract before reader/snapshot integration |
| Provider worker | Process identity, probes, launch/pipes, lifecycle, discovery/auth, supported prompts | 13–25 | Contract handoff; native endpoint evidence before adapter commitments |
| Desktop/client worker | Desktop and existing voice path | 9–16 | Can probe independently; full-app tests after safe startup |
| Integration owner / release handoff | Setup, shared review and release implementation | 13–23 | CI preparation can overlap; installed acceptance uses merged bytes |

For a conservative elapsed model: **2–4 setup + 2–4 dependent handoff delay + max(11–21, 13–25, 9–16) worker lane + 5–9 release acceptance + 3–5 final review = 25–47 active elapsed hours**. Handoff delay is a scheduling allowance within worker dependencies, not additional aggregate effort. Release CI preparation can overlap; this model reserves its full package after integration instead of assuming perfect overlap. Final review is part of the 6–10 integration budget, not added twice to aggregate hours.

With eight productive hours per working day, 25–47 hours is roughly 4–6 working days after rounding to whole days. Host delivery, user consent, physical-device scheduling, runner queues and failed native feasibility experiments are not assigned invented durations. The estimate is not obtained by dividing total effort by three.

### First 4–6 active elapsed hours: resolve the uncertain routes

- Record native Python, provider CLI, terminal and browser versions. Probe isolated code; do not launch the unchanged watcher against real sessions.
- Demonstrate secure create/reopen/replace and scoped reads under reparse/junction changes.
- Open two disposable same-project Claude sessions and two Codex sessions. Target each and observe its transcript. Test resumed and ambiguous cases.
- Prove an exact-target prompt route for the supported terminal, or report the unsupported capability immediately.
- Load the pinned recognizer and provision its model from an installed package; test fixed PCM inference before real audio acceptance.
- Test native capture/URL inspection, notification registration and received-file marking/reveal.
- Return observed failures, completed adapters, remaining edits and a new package-level estimate. A failed route is a named decision, not a blanket buffer on the whole project.

## Minimise CI/CD runs without deferring verification

Use local focused tests and one integration branch/worktree owner. Do not open a PR for every worker or push every intermediate commit. Preserve the existing PR/main trigger policy and cancellation of superseded branch runs. Prepare native runner jobs early, run one deliberate early Windows probe batch when needed, then push coherent integrated checkpoints to one PR. Reuse one frontend build per job and artifact where compatible; matrix platform tests remain separate evidence.

The plan adds no deployment of the team server for desktop-only changes. Use existing test doubles and self-messaging fixtures; use staging once for integrated pairing/message/file acceptance after local gates pass. Re-run affected tests after fixes rather than all platform suites for every documentation edit. The final release still requires tests of the exact merged bytes.

## Acceptance gates and human steps

- Safe import and first/second startup; unrelated sessions survive discovery; no secret file is readable by another ordinary user; junction and replacement attacks are refused.
- Both brains complete authenticated turns and tools, cancellation cleans owned trees, restart preserves the selected origin and reconnects once.
- Claude/Codex native discovery and message receipt identify the intended session even with duplicate project folders and reused PIDs. Unsupported capabilities stay explicit.
- Existing folder registration, discovery, bounded reads, reconnect and persistence work on native filesystem paths.
- Team sessions persist securely, logout/revocation works, and self-messages plus a two-device message/file transfer preserve manifests and receipts. Download marking and reveal must pass independently of byte transfer.
- Network CPU and memory reflect observed host data; unavailable readings stay unknown.
- Native capture, supported browser/terminal/editor actions, notifications and advertised prompt answers pass their tests.
- Real voice trials pass the agreed Windows operating envelope. Shared AEC failures remain release blockers for the corresponding claim.
- Installed artifacts work outside the source checkout, with supported model provisioning and documented upgrade/uninstall. Mac Intel and Apple Silicon regression evidence accompanies Windows results.

Human involvement is limited to a native Windows host/account, ordinary provider/team sign-in or MFA when needed, physical microphone/speaker trials and decisions on any new interface/dependency or unsupported capability that the probes reveal. Credentials stay in provider/OS stores; no secrets in repository files, logs or fixtures. Do not promise that a new consent requirement can be bypassed.

Rollback uses the previous release artifact and preserved configuration only after checking schema/data compatibility. A binary downgrade is not assumed to undo a migration. No production release is authorized by this research workflow.

## Diagnostics handoff

Read this reconciled plan and its estimate page. Recheck HEAD and capture any changes after `61c9761` before implementation. Create an isolated implementation worktree. Use the existing `SessionProvider`, `SecretStore`, `Reader`, folder catalog and messaging contracts. Present concrete definitions for new platform interfaces/dependencies under repository rules, then execute approved work without repeated approval for its internal steps.

Assign the four ownership lanes above. Fix import/process/private-storage prerequisites before full-app native startup. Run the first native probe gate early and reforecast from observed results. Keep shared UI, protocol, storage schema and voice engines. Return implementation PRs, source-linked remaining gaps, native/Mac acceptance evidence, versions, install/model instructions and rollback conditions. Do not claim native support from mocked tests.

## Materials reviewed

- Original Windows plan and estimate at `.agents/in-progress/windows-feasibility.html` and `windows-estimate-breakdown.html`; these paths now direct to the reconciled documents.
- `.agents/in-progress/readback-echo-cancellation-diagnostics-handoff.md`: shared acoustic acceptance and conditional AEC work.
- `.agents/in-progress/dotnet-browser-proposal.html`: separate future host architecture, not a dependency.
- `.agents/in-progress/network-view-diagnostics-handoff.md`: network/presence intent, checked against current implementation.
- `docs/planning/local-folders/implementation.md`: folder plan, checked against current catalog/readers.
- Current source and test files named above, `pyproject.toml`, `.github/workflows/ci.yml`, `.github/actions/setup/action.yml`, and commit changes from `73190ca`.

These documents live under `docs/planning/windows/`, a Git-visible path, so a future commit can carry them into other worktrees. They are not committed by this workflow. The old ignored entry points remain convenient links; the AEC handoff is still Git-ignored and must be copied into an implementation worktree if needed.
