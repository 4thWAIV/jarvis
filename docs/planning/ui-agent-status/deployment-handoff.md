# UI agent status deployment handoff

## Current result

Native observation is implemented on `feature/ui-agent-status`. Claude still creates its own subagents. The new model-work total is a count of observed model-request phases, not an instantaneous billing counter. Tool execution, unknown coverage and slot occupancy remain separate. The same inventory JSON is available to the UI and the brain tool.

The implementation uses native request IDs and response references, incremental transcript observation, native completion sidecars, nested parent metadata and neutral lifecycle hooks. Thinking-block `end_turn` no longer finishes a child. Completion-sidecar changes invalidate the transcript cache, and a newer resumed request supersedes an older completion marker. Long model phases without renewed evidence become unconfirmed after 120 seconds, retaining the last phase and evidence age; they are not called idle.

The default Claude subscription login prevented a fresh model-stream smoke test: `Failed to authenticate: OAuth session expired and could not be refreshed`. The isolated CLI did execute SessionStart, UserPromptSubmit and SessionEnd through the recorder. This is not evidence that a new authenticated model turn succeeded. Existing sessions and credentials were not changed. Historical native request/response records and controlled lifecycle tests cover child and tool transitions.

## Deployment owner: Planning

1. Wait for the feature PR checks and review. Merge only through the authorized release path; preserve the live checkout's capture changes.
2. Deploy the resulting application using the existing update/restart process. The worker route, frontend counters and Codex observer ship together.
3. From the deployed checkout and its Python, install the native observer into the Claude settings file actually used by the local workers. Use the actual application's data directory, not this worktree's data directory:

   ```sh
   .venv/bin/python -m jarvis.worker_hooks --data-dir "$JARVIS_DATA_DIR" --install "$HOME/.claude/settings.json"
   ```

   `JARVIS_DATA_DIR` must be set to the live application's data directory before this command. If a worker uses a different Claude config root, install into that root's settings too, retaining its existing hooks. Installation is explicit and has not been run against live settings by the builder. The recorder's command points at the deployed Python and module; rerunning installation replaces only its own marker and preserves unrelated settings. Symbolic-link settings require choosing the actual target rather than replacing the link.
4. The hooks run asynchronously and return no approval, denial, prompt content or injected instructions. They record only IDs, lifecycle kinds, tool names and receipt time. Reordered tool starts cannot reopen a completed call. Observation files are under `data/worker-observations`, excluded from source control, with a ten-minute reading window and bounded reconstruction. Recorder process startup measured 182.4 ms median and 189.1 ms maximum across twenty isolated invocations; asynchronous configuration avoids blocking tools for that startup.
5. Confirm `/api/workers` reports the provider inventories, model-work count, tool count, unknown count, parent links and coverage. The empty circle in each existing brain account shows that provider's model-work total; opening it shows the combined hierarchy. Each existing agent row includes its family count.
6. Do not claim hooks are loaded merely because installation succeeded. Each worker displays whether native lifecycle events have actually been observed. An already-running session's next event establishes coverage; do not restart a busy worker to force it. If its current configuration does not reload, report it as not yet observed until a normal idle-boundary restart is possible.
7. Refresh the expired Claude login through its normal supported login flow before requiring a new authenticated CLI smoke turn. Exercise a native parent and child, parallel tools, parent idle/background child, and end-of-turn completion; compare the visible counts to the source events. Record any missing coverage on the Work item.
8. After release and the live check, update and deliver `deployment-package.json` through the package factory under Planning's session, linked to UI agent status. Do not present an unreleased branch as a deployed result.

## Rollback

Roll back the application through the existing release path. Remove only hook entries whose command contains `jarvis-native-worker-v1`; preserve all other hook entries and settings. No Claude conversations or native delegation state need migrating or restoring. The observer never starts model turns or answers approvals.

## Validation

The worker/native lifecycle/subagent/runtime/run-executor suites pass locally. The header-line audit now explicitly classifies new metadata-only formatters and retains the untrusted wrapper on `list_workers`. The frontend production build passes. Isolated production components render the expected fixture counts without browser exceptions. Screenshots are illustrative data, not a live deployment claim. CI remains the final merged-branch check.
