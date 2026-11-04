# Provider conversation/context recovery

Status: Backlog — not started.
Owner: Diagnostics planning, for later scheduling.
Tracking slug: `provider-context-recovery`.
Parent: [Voice and audio execution register](../execution-register.md).

## Entry condition

Core UI reconnect behavior is stable and both provider session capabilities are reconciled.

## Work

Recover provider/session references and bounded context deliberately; distinguish restored UI from resumed computation.

## Acceptance

Unavailable sessions produce explicit recovery options; no repeated tool actions, revived approvals or automatic backlog playback.

## Execution boundary

This is filed future work, not part of the current voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
