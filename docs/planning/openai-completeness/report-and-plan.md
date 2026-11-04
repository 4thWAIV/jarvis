# A complete JARVIS with OpenAI and Codex

Audit and repair plan, 1 November 2026. Source inspected at `2c10f72`, then `c55a4d6`; the intervening change repairs workflow reporter address discovery. This is a source-backed assessment and execution plan, not an implemented repair. No installed provider was removed or live brain restarted during this audit.

## Outcome and first delivery

JARVIS can converse through OpenAI and observe existing Codex terminal sessions. It cannot yet replace Claude as the product's execution engine. The first delivery must let a user create an ad hoc Codex agent in a chosen project or registered source folder, watch it work, send follow-ups, cancel it, and resume its recorded conversation. A process launched without roster integration, durable identity and follow-up control is not that delivery.

Recommended architecture: retain the shared JARVIS scheduler, SQLite records, workflow monitor, UI and tool authorization; give the execution engine Claude and Codex adapters. Use a JARVIS-owned Codex app-server connection for managed workers. Keep externally launched terminal sessions as a separate supported observation/queue mode. Do not confuse either with the restricted OpenAI voice brain.

## Findings by essential capability

| Capability | Current evidence and consequence | Repair priority |
|---|---|---|
| Create an ad hoc worker | `server.py:tool_spawn_run`, `/api/runs` and `RunExecutor` start Claude. No execution-provider argument. `_command` uses Claude's `-p`, stream JSON and session ID flags. Without Claude, the launch fails. | First delivery. |
| Execute a complete build | `tool_start_build` defaults to Sonnet and calls that same executor. The spec/plan/reporting machinery is reusable; launching the build owner is not provider-neutral. | First delivery plus workflow acceptance. |
| Select worker model/provider | `_normalise_model` recognizes Claude families/IDs. MCP descriptions and the persona teach Claude model names. A Codex model string is not a supported substitute for the missing runner. | First delivery. |
| Track newly created Codex agents | `CodexSessions` discovers terminal UI processes and explicitly excludes app-server/exec. `run_store` assumes a run ID is also a Claude session ID. A new managed Codex process would otherwise be invisible or misidentified. | First delivery. |
| Follow up, cancel, retry and resume | External Codex messaging uses `codex queue`; `can_interrupt=False`. Run cancellation and retry exist but are coupled to the Claude process and `--resume/--fork-session`. `_retry_run` uses the old JARVIS run ID as the provider session reference. | First delivery and recovery milestone. |
| Approvals and questions | External Codex approval waits are not discovered; `can_answer_dialogs=False`. The existing Codex brain refuses every server-initiated request with one generic decline response. That is not a worker approval/question implementation. | Required for unattended work that can encounter a human decision. |
| Durable workflow control | Workflow storage/reporting is reusable, but work starts through Claude. There is no Codex managed-worker parent/child lifecycle, restart recovery or run-to-thread mapping. | Required for a complete build. |
| Remove Claude / fresh setup | `brains_config.ALWAYS = "anthropic"`; `remove` raises “Claude is always there”. Missing config seeds Claude. Common preflight includes Claude cross-session settings; the setup skill assumes Claude login. OpenAI can be selected now, but Claude is not an optional product dependency. | Required for OpenAI-only onboarding. |
| Skills and memory | `sync_skills` writes the brain skills into `.claude/skills`. The shared persona ends its memory-index section with `@MEMORY.md`; the Codex adapter has no explicit index-loading equivalent. The separate external-session communication skill already supports Codex. | Required to preserve instructions and memory across turns/restarts. |
| Context protection | `BrainConfig.context_budget` defaults to 900,000 regardless of provider. Codex reports last-input token usage, but its model context-window field is not consumed. A user-approved rotation warning can arrive after a smaller model has compacted or hit its limit. | Required for long conversations/work. |
| Usage and capacity | OpenAI subscription readings exist. However `tool_usage_status` calls `usage_store.snapshot()` without a service, which defaults to Anthropic; the session usage scanner reads Claude project JSONL only. OpenAI session attribution and spoken capacity are incomplete. | Required for workload management; not a launch prerequisite. |
| Recovery and useful errors | The server has Claude-specific expired-login speech. The Codex brain opens a new thread on process creation rather than resuming the prior native thread. There is no durable managed-worker reconnect protocol. | Required for reliable operation. |
| Provider compatibility and containment | Feature disabling and a separate brain home exist. Untested Codex versions produce a warning, not the proposal's claimed strict gate. A worker needs controlled edit/command capabilities that the read-only brain deliberately lacks. | Required before shipping managed workers. |
| Product-wide evidence | Provider/session unit tests exist, but no acceptance run proves installation and a complete product journey with Claude absent. The earlier same-task Claude/OpenAI evaluation was not found. | Final release gate. |

These are functional gaps. Renaming comments, copying Claude branding, matching identical internal flags or optimizing startup milliseconds is not the first milestone.

## What already exists and should be retained

- Provider-neutral brain events and a working Codex app-server brain adapter, streaming replies, model/account discovery, ChatGPT sign-in and subscription limits.
- MCP configuration, image-result forwarding, web search, tool-failure reporting and existing trust gates. Qualify these end to end rather than assume they are absent.
- Codex terminal discovery, transcript reading, message queueing, delivery/reply tracking, unified agent naming and the corrected exclusion of subagent threads during terminal matching.
- Codex transcript history readers and folder discovery/read policies, including archived-session folders. These are not missing.
- External session `talk-to-jarvis` skill installation supports Codex. Needs-board and workflow reporting channels are not inherently Claude-only.
- Team server, people/messages/files, voice input/output and the React UI are shared application services. They need OpenAI-only acceptance, not rewrites.
- The work on UI capability parity and localization applies to both providers. Reuse its schemas, bindings and checks; do not bill or build that work twice.

## Distinctions the old plans missed

The old `openai-brain.html` primarily describes the conversational brain. `session-providers-design.md` describes observing and messaging already-running sessions. Neither supplies a provider-neutral worker launcher. `brain-provider-components.md` contains stale “not committed” and “not yet” notes; source and tests supersede those status notes.

An external Codex terminal does not become controllable merely because a new app-server can resume its history. Simultaneously resuming a live conversation risks two owners. Managed workers must use exact recorded identities and an owned connection; do not promise external-terminal approval control until a supported exact-target transport is proven.

Correction to the earlier status response: the source inspected here does not expose a `turn/interrupt` method on `CodexSession`. Its `kill` and `close` stop the process. The original proposal mentioned interruption, but that is not the same as an implemented graceful turn interruption.

## Supported implementation direction

The installed CLI reports `codex-cli 0.154.0`. A fresh schema export contains `ThreadStartParams`, `ThreadResumeParams`, `ThreadForkParams`, `TurnSteerParams`, `TurnInterruptParams`, command/file/permission approvals and user-input requests. This establishes available protocol shapes, not successful application integration.

Current official documentation describes persistent threads, turns, resume/fork and structured approval requests. Use that interface for managed workers, with a protocol probe against the selected installed version before implementation. Reuse Python transport primitives; no new SDK dependency is necessary just to spawn a worker. See [Codex App Server](https://learn.chatgpt.com/docs/app-server).

This is a local-product plan. Hosted/commercial authentication is a separate deployment boundary: the official page distinguishes local/open-source app-server authentication from commercial/hosted use. A hosted launch must use its supported authentication route; do not silently substitute API billing or transfer local subscription credentials to a server.

## Implementation sequence

### Establish ownership and protocol contract

1. Diagnostics creates an isolated worktree from current main and records the base. Coordinate ownership with the active navigation, localization and capability-parity workflow, especially `server.py`, `jarvis_mcp.py`, `brain.py`, settings and generated contracts. Do not edit live data or uninstall Claude to test absence.
2. Add this package to the existing workflow system with named tasks and source-linked evidence. Preserve local validation before merge; no production deployment as part of this handoff.
3. Export the installed Codex protocol to a temporary directory and exercise initialize, start, turn, follow-up, interrupt, resume, fork and all supported approval/question response shapes in a disposable project. Do not probe real user projects or send external messages.
4. Record supported capabilities/version and actual response fields. Unknown required behavior is a named blocker, not a switch to terminal keystrokes or guessed JSON.
5. Define one execution-provider contract with launch/attach, submit/steer, interrupt/cancel, resume/fork, close, event stream and capabilities. Keep the existing BrainProvider and observed SessionProvider roles distinct. Share RPC transport infrastructure without granting the voice brain worker-level permissions.

### Deliver the first usable Codex agent

1. Add explicit provider and provider-valid model selection to run requests, `spawn_run`, `start_build`, UI equivalents and discovery schemas. Use the configured worker provider; with only OpenAI configured, choose OpenAI. If the user names a provider, honor it. Never silently fall back to Claude or reinterpret a Codex model as Sonnet.
2. Preserve default behavior for existing Claude users, but make worker defaults independent of which voice brain is speaking. Validate models through the selected adapter, not the Claude-only regex.
3. Migrate the existing local SQLite database additively: retain the JARVIS run ID; persist provider, provider thread/session ID, active turn ID, account/home reference, parent run, resume/fork source, workflow/task ID and process ownership. Backfill legacy runs as Anthropic with their existing session IDs. Do not create another database or migrate provider transcript bytes.
4. Refactor RunExecutor so scheduling, concurrency, durable state, cancellation requests and publishing remain shared. Move Claude arguments, parsing and environment into its adapter. Add a Codex worker adapter using a dedicated app-server lifecycle and explicit project/folder cwd.
5. Keep the restricted brain profile separate from worker configuration. Workers need the authorized filesystem, command, git and MCP capabilities; use scoped permissions consistent with the requested work. Reuse login securely without copying credentials into worktrees or prompts, and serialize configuration writes so concurrent workers cannot overwrite one another's settings.
6. Register managed Codex sessions from the durable run registry; merge them with discovered sessions using provider plus native thread identity. Update run exclusions/deduplication that assume run ID equals session ID. Do not require a terminal window for roster visibility.
7. Route follow-ups to the managed connection using the exact thread/turn. Persist message lifecycle and replies. Send at most once per application request; after an ambiguous disconnect, reconcile before replaying.
8. Map turn start/progress/tool activity/completion/error to existing run/workflow events. Process alive does not mean task running; turn completed does not necessarily mean a multi-step build is complete. Keep successful artifacts and test evidence separate from transport success.
9. Demonstrate: say to start a Codex agent in a registered source folder, see it in Agents, have it edit and test a disposable file, read its reply, send a follow-up, cancel a running turn, and resume with the same context. This is the first acceptance checkpoint, before peripheral cleanup.

### Make builds, decisions and recovery complete

1. Make `start_build` launch the selected provider through the same runner. Preserve settled-spec persistence, review and phased progress. Supply provider-neutral build instructions and install/verify any required skills in Codex's actual search path; do not assume Claude plugins exist there.
2. Retain the workflow monitor as the task owner. Launch parallel work through the shared runner with per-task worktree/folder ownership, bounded concurrency and review dependencies. Native Codex child agents may be used only when their identity/events can be tracked; otherwise create independently managed workers through JARVIS.
3. Surface command/file/permission approvals and user questions as waiting states with request, item, thread and turn IDs. Reply using the correct request-specific schema. Connect existing human decision/readback flows; retain evidence of the authorized action. Do not auto-accept all requests or treat ordinary questions as permission decisions.
4. Handle decline, cancellation, stale answer, duplicate answer, disconnect and request-resolution notification. Recovered requests must still be current before answering. Unknown requests fail visibly instead of receiving an invalid generic decline shape.
5. Implement graceful interruption first; terminate only the owned process tree if the protocol cannot stop within the existing bounded cleanup policy. Mark queued cancellation without creating a process.
6. Retry uses the original provider/model and native history reference. Resume continues the intended thread; fork creates a new tracked child with lineage. A missing transcript produces an explicit restart-with-handover choice, not an accidental fresh run claimed as resumed.
7. On JARVIS restart, reconcile persisted queued/running/waiting work against exact owned threads/processes. Avoid PID reuse and duplicate execution. Do not mark abandoned processes successful. Store event cursors and reconcile native history where replay is supported.
8. Keep existing external terminal observation/queue behavior. If exact-target interrupt/approval handling is unavailable for an external session, expose that limitation and offer an explicit managed successor after the old session is quiescent. Never silently seize a live external thread.

### Remove the requirement for Claude

1. Replace the always-Anthropic configuration invariant with an explicit configured-provider set and valid default. Allow removing Claude after another ready provider is selected. Empty setup opens onboarding; corrupted config produces recovery guidance instead of quietly re-enabling Claude.
2. Scope provider checks, cross-session settings and skill installation to configured features. Claude absence must not create a misleading prerequisite or prevent OpenAI work. Preserve independent OS, speech and network checks.
3. Update setup instructions, UI actions and diagnostic remedies. OpenAI login failures must direct the user to OpenAI sign-in, not `claude` login. Test initial setup, logout/expiry, refresh and restart with only Codex installed.
4. Keep the already-working ChatGPT sign-in and account discovery. Do not introduce an API-key requirement or new auth system for this local repair.

### Preserve memory, context and capacity

1. Introduce one instruction assembly/sync path that loads the same persona and bounded memory index for each provider, with hashes and provider-specific file placement. Preserve edited user instructions. Do not rely on Claude's import syntax in Codex or accidentally include the repository developer persona before the brain persona.
2. Sync brain skills to each provider's actual supported search location. Separate those from worker skills and the external communication skill. Verify a test instruction and memory fact are received after restart, not merely that files exist.
3. Record the model's context limit from supported metadata/events, current input usage, compaction events and reserve needs. Use a per-model budget below the effective limit; validate configuration. If the limit cannot be obtained, label it unknown and use a documented conservative threshold rather than 900,000 for every model.
4. Preserve the existing consent semantics for intentional rotation. Handle provider auto-compaction explicitly and preserve the durable task/spec/checkpoint references. Test recovery after compaction rather than assuming identical native caches between Claude and Codex.
5. Allow active-brain replacement with a persisted handover and rollback on startup failure. This can follow agent launch; OpenAI-only startup does not depend on hot switching. Native history remains provider-owned; cross-provider transfer is an explicit summary, not a fake native resume.
6. Make usage reads explicitly provider/account-scoped. Add Codex token/cache usage to session attribution and managed-run events, deduplicate parent/child records, and preserve unknown values. Subscription allowance, model context occupancy and billable cost are distinct; do not report subscription usage as dollar cost.
7. Confirm network burn-down uses both accounts without counting one account once per worker. Reuse the existing provider/account network implementation; fix only missing attribution rather than rewriting network health.

### Enforce parity and qualify the complete product

1. Reuse the UI capability-parity package's contract/coverage gates for launch, model selection, decisions, controls and JSON results. Share schemas and authorization checks between UI and brain tools.
2. Add a provider contract suite for the common runner, using fake subprocess/RPC boundaries for scheduling, errors, lifecycle and restart tests. Run existing Claude tests unchanged where behavior is preserved.
3. Add a separate Codex-only test profile: isolated HOME/config/data, only Codex on the provider PATH, no Claude credentials/config, explicit OpenAI defaults. Instrument process launches to fail if any `claude` command is attempted. Do not alter Tim's installed tools or auth.
4. Exercise both a fresh install and an existing database migrated from Claude runs. Include legacy history reads and an unavailable-Claude retry that reports its provider rather than silently executing through Codex.
5. Run a real disposable Codex build through the application, not just a direct CLI probe: project/folder selection, launch, code change, tests, review, workflow updates, follow-up, decision, cancel, retry, native resume/fork and server restart. Confirm no duplicate side effects after disconnect.
6. Run a voice/typed-turn journey through the OpenAI brain: memory recall, folder browse, history search, image/screen tool, web/page read, create worker, inspect progress, handle its question, read result and summarize usage. Exercise untrusted-content blocking through the actual MCP result path.
7. Exercise team self-message with an attachment and approval, network self visibility, diagnostics and UI navigation in the isolated fixture setup. Keep ordinary voice output/input unchanged. Include Spanish if the localization package is present.
8. Record current latency and context/tool overhead with the selected model, without holding worker launch hostage to a benchmark competition. The old proposal's timing/window numbers are historical, not acceptance thresholds.
9. Independent review must trace one task from user intent to provider ID, process, artifacts, result and recovered history. Deliver a running local candidate, exact test commands/results and remaining limits before merge. No production release is authorized by this plan.

## Acceptance matrix

| Journey | Required result |
|---|---|
| Fresh OpenAI-only install | Onboarding, login, conversation and worker creation succeed with no Claude invocation. |
| Ad hoc coding task | Correct folder, provider/model, visible agent, useful edit/test result and follow-up. |
| Complete background build | Spec, phased tasks, parallel workers, review and progress all work using Codex. |
| Human decision | A waiting worker shows the exact question/action and continues only on the matching answer. |
| Stop/retry/resume/fork | Correct thread history and lineage; no duplicate run or work in the wrong folder. |
| Restart/disconnect/expired login | Explicit recoverable state, correct provider remedy, no silent lost or repeated work. |
| Long conversation | Memory and task checkpoint survive; context warning precedes the configured budget; compaction is visible. |
| Usage and network | Correct OpenAI account/session figures or explicit unknown; no phantom Claude dependency. |
| Shared product | Voice, files, team messages, UI tools and diagnostics operate through the OpenAI brain. |
| Existing Claude user | Existing provider behavior and stored runs continue to work. |

## Priority, effort and parallel work

Planning allowances, not measured execution times. No new model-training or speech-engine work is needed.

| Delivery | Active agent-assisted effort | Dependency |
|---|---:|---|
| First usable managed Codex agent: protocol, identity migration, runner, model selection, roster, follow-up/stop/resume slice | 12–20 hours | First. |
| Complete builds, decisions, retry/fork and restart recovery | 10–18 hours | Shared runner/identity contract. |
| OpenAI-only configuration, onboarding and correct remedies | 3–6 hours | Can run beside the runner after settings ownership is assigned. |
| Memory/skills/context and usage attribution | 6–12 hours | Can run in parallel in owned modules. |
| Integration, independent review and actual Codex-only journeys | 6–10 hours | All essential paths integrated. |
| Total | 37–66 active hours | Reforecast after the first usable agent. |

With an integrator and two disjoint implementation lanes, approximate active elapsed time is 22–38 hours, excluding human validation and actual external blockers. Do not add the whole UI-parity/localization budget again. The initial working agent should be shown as soon as its first checkpoint passes; do not wait for all cleanup to show that it can start work.

Greatest complexity: persistent identity and recovery across JARVIS runs, provider threads, turns, workflows and separately launched terminal sessions. Merely invoking a command is not the largest task. Approval/request lifetimes and preventing duplicate work after reconnect are next. Extracting a runner must retain the existing cancellation/concurrency guarantees rather than replacing them with a second scheduler.

Human involvement is limited to a required provider sign-in if no usable login exists, decisions on an actual provider/protocol blocker, and the local acceptance walkthrough. Do not ask repeatedly for saved credentials or ask the user to install Claude to finish the OpenAI path.

## Audit evidence and limits

Read source: `run_executor.py`, `run_store.py`, `server.py`, `jarvis_mcp.py`, `builds.py`, `brains_config.py`, `brain.py`, `brain_provider.py`, `codex_provider.py`, `codex_sessions.py`, `session_provider.py`, `session_skill.py`, `preflight.py`, `data_paths.py`, `usage_store.py`, `usage_scan.py`, folder adapters and Brains settings. Compared the earlier provider/session plans and merged communications repair.

The local generated protocol evidence is under `/tmp/jarvis-codex-support-schema`; regenerate during implementation rather than treating temporary files as a durable dependency. No real worker was launched by this audit and no claim is made that a managed-worker implementation has been proven. Repository tests validate existing behavior; they cannot establish that missing capabilities work.

Audit regression command: `.venv/bin/python -m pytest tests/test_codex_sessions.py tests/test_codex_provider.py tests/test_brain_provider.py tests/test_brains_api.py tests/test_start_build.py tests/test_run_executor.py tests/test_session_skill.py -q`. Result: 211 passed in 57.16 seconds; one existing Starlette/httpx deprecation warning. Tests ran without changing application source.
