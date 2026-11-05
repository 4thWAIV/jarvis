# Durable typed delivery and safe retry

Status: Backlog — not started.
Owner: Diagnostics planning, for later scheduling.
Tracking slug: `typed-delivery`.
Parent: [Voice and audio execution register](../execution-register.md).

## Entry condition

Core draft recovery is retained and the acceptance/idempotency contract is specified.

## Work

Add durable request acceptance, uncertain-send status, recoverable outbox and idempotent retry using existing local storage.

## Acceptance

Disconnect before/after acceptance never silently loses or duplicates a request; reloading preserves unresolved delivery state.

## Execution boundary

This is filed future work, not part of the current voice-and-audio-core workflow. When promoted, record its workflow ID in the parent register. Do not start a duplicate if an existing workflow already owns it.
