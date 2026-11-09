# Package understanding release integration

Work: `work-afc13119d082`. Branch: `feature/jarvis-groks-packages`.

The implementation review is recorded in `review/report.md`. Root reported presentation signoff for `package-understanding-implementation-rev-1.0.0-locala03` (`pkg-d6f39bbb32b8`). That report does not establish successful runtime classification, display capture, spoken approval or external delivery.

## Main reconciliation

`3c924ac` merges `origin/main` at `8a5717a`. The package inbox sender-proof changes and one-channel conversation records merged without conflicts. The built frontend passes TypeScript and Vite compilation. The attachment handoff fixture supplies an exact user-control execute scope. The browser fixtures wait for the loaded frame and the observer handshake before requesting its current location.

## Runtime evidence and release gates

The approved specification says: "For voice, use a separate bounded intent-classification pass through the existing brain provider, with a strict schema and a two-second deadline. A timeout leaves reading available and returns `intent_unavailable` for mutation."

The isolated worktree's Anthropic/Sonnet requests returned `intent_unavailable`; Claude reported an expired OAuth session. The primary configuration selects OpenAI `gpt-6-sol`. That model returned `model_not_found`: "The 'gpt-6-sol' model is not supported when using Codex with a ChatGPT account." Its private diagnostic `model/list` offered `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra` and `gpt-5.6-luna`. No live model setting was changed. Two isolated diagnostic requests using the shipped default `gpt-5.6-terra` also exceeded the production two-second deadline and returned `intent_unavailable`. Source inspection remains available; successful voice action classification has not been demonstrated.

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
