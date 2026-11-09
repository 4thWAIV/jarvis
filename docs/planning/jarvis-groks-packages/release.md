# Package understanding release integration

Work: `work-afc13119d082`. Branch: `feature/jarvis-groks-packages`.

Release is also blocked on the source conflict in `authorization-conflict.md`: the existing result bridge and answer-file adoption path bypass the new dispatcher scope. New product changes are stopped for design reconciliation. The latency-only decision draft is not the complete blocking handoff until that conflict is addressed.

One approved follow-up case also remains unfinished: an explicitly historical reference cannot currently be prepared or delivered after the package changes. `PackageFollowups.prepare` requires the requested revisions to match the latest capture, and `context_guard` always rejects changed current revisions. The specification says: "An explicitly historical source reference is part of that wording, so an old revision is usable without pretending it is current." Completing this case needs an explicit historical evidence binding and approval coverage rather than parsing a phrase from draft prose. This is remaining implementation work under the approved historical-reference requirement; it is not supplied by the latency decision.

The implementation review is recorded in `review/report.md`. Root reported presentation signoff for `package-understanding-implementation-rev-1.0.0-locala03` (`pkg-d6f39bbb32b8`). That report does not establish successful runtime classification, display capture, spoken approval or external delivery.

## Main reconciliation

`3c924ac` merges `origin/main` at `8a5717a`. The package inbox sender-proof changes and one-channel conversation records merged without conflicts. The built frontend passes TypeScript and Vite compilation. The attachment handoff fixture supplies an exact user-control execute scope. The browser fixtures wait for the loaded frame and the observer handshake before requesting its current location.

The full local run exposed an ordinary-conversation regression: an unavailable classifier note was added to every user turn. The implementation now retains its internal failure code and reports it only when that turn attempts a package action. Ordinary conversation receives the same user text as before. The existing voice activity/socket tests exercise that behavior, and a targeted authority test ensures package mutation still receives `intent_unavailable` without running its handler.

Canceling classification now uses the existing voice-turn cleanup, ending the speech turn and releasing its lock. A regression first reproduced the unfinished turn, then passed after classification moved inside that cleanup block. The combined authority, ordinary voice activity and voice-socket rerun passes 49 tests. The Work-review fixture reuses the existing scoped request helper and passes its 22 tests. Screen tests pass with the primary checkout's existing machine-local `sips` skip setting copied into this worktree's ignored local config; the black-frame sampling test failed before that setting because its `sips` conversion could not run. No screen implementation was changed.

The new dispatch functions use the repository's `tool_` naming convention; public tool names are unchanged. The argument-echo audit passes all 11 cases after that rename. Final PR checks must run on the consolidated commits; the earlier PR workflow was canceled after a known fixture correction to avoid finishing a stale run.

## Runtime evidence and release gates

The approved specification says: "For voice, use a separate bounded intent-classification pass through the existing brain provider, with a strict schema and a two-second deadline. A timeout leaves reading available and returns `intent_unavailable` for mutation."

The isolated worktree's Anthropic/Sonnet requests returned `intent_unavailable`; Claude reported an expired OAuth session. The primary configuration selects OpenAI `gpt-6-sol`. That model returned `model_not_found`: "The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT account." Its private diagnostic `model/list` offered `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra` and `gpt-5.6-luna`. No live model setting was changed. Two isolated diagnostic requests using the shipped default `gpt-5.6-terra` also exceeded the production two-second deadline and returned `intent_unavailable`. Source inspection remains available; successful voice action classification has not been demonstrated.

An offered-model diagnostic using `gpt-5.6-luna`, allowed to complete outside the production gate, identified a sender question and a discussion separately in 3.269 and 3.067 seconds. The classifier prompt now explicitly separates a message's subject from its package target. Both responses after that clarification use target null for the implicit current package. The production deadline and persistent model settings remain unchanged. Raw diagnostic output is in `review/runtime-diagnostic.json`; this is successful provider parsing outside the deadline, not successful production authorization.

The worker process's non-prompting `screen_recording_granted()` probe returns false. This describes the worker's launch environment, not the running server's permission. No screenshot was taken and no permission dialog was requested. Remote-client capture remains explicitly unavailable.

The isolated application browser tests exercise the built frontend, pinned source routes, authenticated observations, multiple clients, narration independence and MCP image bytes. Controlled tests exercise capture approval and transmission boundaries. A normal-runtime trial must still exercise a successful direct intent, a spoken read-back followed by approval, the external recipient's recorded response and deliberate screen capture from the server's launch environment.

## Coordinated deployment procedure

Root owns the live primary checkout and its four capture edits. Root reported that those edits match the approved capture patch in PR 93. This worker does not modify or restart that checkout.

1. Complete the package PR checks and the capture PR checks. Merge the capture patch and ensure the package release contains it before deployment.
2. Wait for the chosen merged release commit's complete `ci.yml` workflow to pass. The updater reads the complete workflow, including the page job.
3. Root preserves the live capture edits as a recoverable patch or stash, confirms their approved change is present in the chosen release and reconciles the primary checkout to a clean tracked state. Keep unrelated edits intact. Do not blindly restore files or reapply a patch already included in the release.
4. Root uses the existing updater: `.venv/bin/python -m jarvis.update --check`, then `.venv/bin/python -m jarvis.update --to <package PR number>`. It selects only a CI-passed version, installs changed requirements, builds the frontend when applicable and asks the running server to restart itself. A modified tracked checkout is refused by this service.
5. Root checks the reported running version, opens the review package, navigates manually and requests the current location and source evidence. Exercise the runtime gates above and confirm capture behavior after the update. Retain the saved live patch until its behavior is confirmed.

Changing the approved classification deadline or choosing a different persistent model is not part of this integration. A failed runtime gate remains visible and must not be reported as deployed functionality that passed review.
