# UI agent status: runtime integration and remaining execution decision

Work item: `work-6f00ebb716c2` (UI agent status).

## What remains

The complete current-token count is not available for Claude terminal sessions or Claude native subagents. Claude's partial-message stream covers its main session, but does not forward subagent token deltas. Claude terminal `logs` returns terminal output and `attach` opens the terminal; neither is a structured per-worker token subscription. The roster, API-request transcript entries, task lifecycle and completed assistant blocks establish phases but cannot distinguish generation from a stalled request. Standalone Codex sessions outside the running app-server likewise retain transcript-only coverage.

Closing this gap requires an execution change: run each counted Claude worker as its own hosted partial-message session, with its own stream and parent relation. Preserve subscription authentication, conversation continuity, approval handling, cancellation and messaging. Native subagents would become separately hosted workers. Existing named terminal sessions must hand over only at an idle boundary; there must never be two writers to one conversation. Codex workers should use the observable app-server runtime. This is a change to how workers execute, not another counter or a different label. `execution-decision.json` explains that choice for delivery through the package factory by Planning.

The UI does not replace missing measurement with zero. A number plus `+?` means there are observed generating workers plus workers whose current generation cannot be measured. Runtime-active, tool-waiting, queued and observed-generating remain separate. Generation means a stream delta arrived within the last two seconds; it is not a billing measurement or a claim about server-side computation between chunks.

## Implemented

- `/api/workers` and the brain's `list_workers` tool expose the same normalized worker inventory. Provider plus session/agent ID is the identity; workflow associations annotate existing workers rather than count duplicates.
- The Codex observer connects to the existing app-server's Unix WebSocket, reads loaded threads and runtime state, and rejoins active threads without configuration overrides to receive events. It never starts a thread or turn or answers approvals. It unsubscribes when idle and clears evidence on disconnect. Standalone transcript-only sessions remain explicitly unmeasured.
- Codex model deltas establish recent observed generation. Tool output and cumulative token-usage events do not. Runtime-active remains separate from generation, including during tool or approval waits.
- Claude managed runs request partial messages. Model deltas and thinking-token increments update the root worker. Task lifecycle and child messages create separate child records, preserve nested parents, exclude shell jobs, and never credit child output to the parent. Native child output remains lifecycle-only coverage. Process cleanup removes its in-memory observations.
- Claude CLI roster and transcripts provide process, turn and tool phases; native children retain their parent metadata. No transcript write is treated as current token generation.
- Managed run occupied, queued and free slot counts are independent of generation. External unreported slot reservations are unknown.
- The empty `weekly-avatar` positions in the existing `WeeklyUsage` brain accounts now show each provider's generation count and missing coverage. Opening a count shows the combined total and project/parent hierarchy. Existing agent rows show family counts. The surrounding layout remains intact.
- Discovery failures, stale snapshots, absent workflow workers and malformed parent cycles surface as coverage gaps. Poll failure clears the visible count. A restart cannot recover an active claim from stored job status.

## Local review

`brain-slots-review.png` and `inventory-review.png` show the production components with explicitly illustrative JARVIS-only data, in an isolated browser. They demonstrate placement and hierarchy, not a live activity claim. The review capture contains only illustrative JARVIS workers; no unrelated local project names are published.

Runtime/session/inventory/executor regression tests passed. The frontend production build passed. The local browser showed the OpenAI counter as `2` and Claude as `0+?`, matching the supplied fixture. Neither a live-app restart nor production deployment has occurred.

## Evidence

- Codex app-server protocol: https://learn.chatgpt.com/docs/app-server
- Claude partial-stream scope and main-session-only forwarding: https://code.claude.com/docs/en/agent-sdk/streaming-output
- Claude child IDs, task lifecycle, background membership and thinking-token events: https://code.claude.com/docs/en/agent-sdk/typescript
- Installed runtime inspection: Codex app-server 0.167.0 and Claude Code 2.1.319. The existing Codex daemon returned loaded-thread metadata and item lifecycle events through the documented WebSocket. Terminal transcripts and CLI help were inspected without restarting or modifying their sessions.

## Handoff

The inventory and runtime adapters are implemented in the isolated `feature/ui-agent-status` worktree. Planning owns delivery of the Work-linked decision package and any live deployment. The complete all-worker count remains unfinished until the execution integration covers workers that currently have no token stream. Do not mark this Work item complete because the inventory is present.
