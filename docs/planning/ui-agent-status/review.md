# UI agent status: runtime integration and remaining execution decision

Work item: `work-6f00ebb716c2` (UI agent status).

## Current direction

The proposal to replace native Claude delegation with hosted workers is withdrawn. The revised [native activity plan](native-activity-plan.md) preserves native terminal sessions and native subagents. It separates model-request work, tool execution, idle and unconfirmed observation, using request boundaries and native lifecycle hooks. It includes the observed premature-completion defect caused by thinking blocks carrying `end_turn`.

The committed counters now show model-work phases, tool execution and unconfirmed coverage. Native hooks, request/response matching and completion-sidecar handling are implemented. Deployment and authenticated live validation remain; see deployment-handoff.md. No active sessions have been changed.

## Implemented

- `/api/workers` and the brain's `list_workers` tool expose the same normalized worker inventory. Provider plus session/agent ID is the identity; workflow associations annotate existing workers rather than count duplicates.
- The Codex observer connects to the existing app-server's Unix WebSocket, reads loaded threads and runtime state, and rejoins active threads without configuration overrides to receive events. It never starts a thread or turn or answers approvals. It unsubscribes when idle and clears evidence on disconnect. Standalone transcript-only sessions remain explicitly unmeasured.
- Codex model deltas establish recent observed generation. Tool output and cumulative token-usage events do not. Runtime-active remains separate from generation, including during tool or approval waits.
- Claude managed runs request partial messages. Model deltas and thinking-token increments update the root worker. Task lifecycle and child messages create separate child records, preserve nested parents, exclude shell jobs, and never credit child output to the parent. Native child output remains lifecycle-only coverage. Process cleanup removes its in-memory observations.
- Claude CLI roster and transcripts provide process, turn and tool phases; native children retain their parent metadata. No transcript write is treated as current token generation.
- Managed run occupied, queued and free slot counts are independent of generation. External unreported slot reservations are unknown.
- The empty `weekly-avatar` positions in the existing `WeeklyUsage` brain accounts now show each provider's model-work count and missing coverage. Opening a count shows the combined total and project/parent hierarchy. Existing agent rows show family counts. The surrounding layout remains intact.
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

The inventory and runtime adapters are implemented in the isolated `feature/ui-agent-status` worktree. Planning owns delivery of the Work-linked decision package and any live deployment. The implementation awaits release and live coverage validation, including an authenticated fresh Claude test. Do not mark this Work item complete because the inventory is present.
