# Main-agent counter correction

Work: UI agent status (`work-6f00ebb716c2`). Recorded 2026-11-09, 10:18 CST. This is a local reproduction and correction record, not a deployment claim.

## Observed failure

Planning reported the live Diagnostics main agent as runtime-active with a recent UserPromptSubmit hook, while its brain-account model-work count was zero. Main agents were present in the inventory; the defect was the evidence reducer, not omission of main agents.

The Diagnostics native transcript for session `a5b4be76-7b3d-4a0c-9220-817d38b9e5f3` appends these records in this order:

| File order | Record | Event time UTC |
|---|---|---|
| First | api-request, request `f4359a7b-5a1e-43ca-87a5-bb694d656370` | 16:14:56.619 |
| Second | user record that caused the request | 16:14:55.667 |

Replaying that actual prefix, before the first assistant block, produced `awaiting_model` at 1794240895.667 in the released reducer. It retained the request ID but discarded its model-work phase. The corrected reducer produces `model_work` at 1794240896.619. The transcript's request at 16:14:38.577 shows the same delayed-write pattern.

No prompt, reply, tool arguments or model reasoning content is copied into this record.

## Correction

- Earlier timestamped records cannot undo a later observed lifecycle boundary, including across incremental reads.
- A new provider request consumes the prior tool batch; a delayed older tool-result record cannot reopen waiting.
- An asynchronous awaiting-model hook cannot replace a current, directly observed model-request phase merely because the hook recorder ran later. Tool, completion and permission evidence still apply.
- A prompt or runtime-active flag by itself remains insufficient for a model-work count. The counter still means an observed model-request phase, including possible provider wait, not exact backend token generation.

Focused regression checks cover the actual record order, incremental reads, expiry, terminal completion, delayed tool results, stronger tool hooks, and prompt-only uncertainty. The native hook, worker inventory, subagent and primary-session suites pass locally. The running app has not been changed by this builder.

## Release action

Planning's release owner receives the correction PR, completes its required checks, deploys through the existing updater, and compares a live native main-agent request boundary with `/api/workers` and the brain-account count. That last live comparison remains required; a local replay is not Tim seeing the corrected count.
