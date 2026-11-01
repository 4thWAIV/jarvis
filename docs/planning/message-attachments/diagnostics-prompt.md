# Diagnostics task: local files on drafted team messages

Implement the plan in `docs/planning/message-attachments/implementation.md` in your own isolated worktree. Read current repository instructions and record the base commit before editing. If this documentation is not yet committed, copy these two plan files from the source checkout; do not copy unrelated untracked work or secrets.

Start and report a JARVIS workflow named **Local message attachments**, with baseline, file-attachment-service, tool-integration, verification and handoff phases. Follow the installed `talk-to-jarvis` reporting instructions. Use the existing workflow monitor, not a new workflow system.

Deliver a brain-accessible `attach_message_files` operation for a saved draft and exact revision. Reuse `FileSnapshots.snapshot`, `LocalMessaging.write_draft`, existing file limits and existing revision-bound review/send machinery. Add local source streaming through the current access policy, including the explicitly named ordinary `.agents/in-progress` document case. Preserve previous attachments and all draft metadata. Bind asynchronous staging and commit to the original team identity. Partial failure or stale revision leaves the draft unchanged. Require fresh review after attachments change. Do not send or upload a real message merely to test drafting.

Wire the shared MCP schema, runtime, acting-tool gate, async dispatch and JARVIS instructions. Test source access, actual byte limits, batch failure, cancellation, response-loss retries, concurrent edits, team changes, approval invalidation, browser parity and isolated self-delivery. Use harmless fixtures; do not send anything to Matt during automated verification.

One implementation owner may finish this; a second worker can prepare tests under agreed file ownership and a reviewer can inspect the merged result. Keep source/writer integration together. Budget 4–8 active agent-hours subject to the access-boundary assumption in the plan. Prefer focused local tests and one integrated PR to repeated CI runs. No Team Server redesign, protocol change, new database or Windows port is part of this task.

Return implementation evidence, remaining limitations, worktree/branch and PR as permitted by repository instructions. State any normal app/brain reload required to expose the tool; do not silently restart an active user conversation. Mark the workflow complete only when its implementation and verification are complete.
