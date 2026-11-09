# UI agent status deployment handoff

## Current result

Native observation is implemented on `feature/ui-agent-status`. Claude still creates its own subagents. The new model-work total is a count of observed model-request phases, not an instantaneous billing counter. Tool execution, unknown coverage and slot occupancy remain separate. The same inventory JSON is available to the UI and the brain tool.

The implementation uses native request IDs and response references, incremental transcript observation, native completion sidecars, nested parent metadata and neutral lifecycle hooks. Thinking-block `end_turn` no longer finishes a child. Completion-sidecar changes invalidate the transcript cache, and a newer resumed request supersedes an older completion marker. Long model phases without renewed evidence become unconfirmed after 120 seconds, retaining the last phase and evidence age; they are not called idle.

An isolated CLI smoke invocation, with API-key environment variables removed, could not authenticate through its default subscription login: `Failed to authenticate: OAuth session expired and could not be refreshed`. The isolated CLI did execute SessionStart, UserPromptSubmit and SessionEnd through the recorder. This is not evidence that a new authenticated model turn succeeded. That result applies to the isolated invocation only; it does not establish that active named Claude sessions have expired credentials. Their authentication was not inspected or changed, and they were not restarted. Hook installation and observation do not require a model login. Historical native request/response records and controlled lifecycle tests cover child and tool transitions.

## Native hook installation prerequisites

The deployed application must include `jarvis.worker_hooks`, and the installer must run with the Python that will remain available after deployment. Set `JARVIS_DATA_DIR` to the running application's data directory. Identify the actual Claude settings file for each worker config root; it must be writable valid JSON, and any symbolic link must be resolved to its target. Existing hooks are retained. The installed command records absolute interpreter, recorder and data paths, so rerun installation if deployment changes those paths.

No OAuth refresh, API key, model request or session restart is needed to install or record hooks. Coverage is established only when that worker emits a recognized event. A worker that has not loaded the hook configuration remains explicitly unobserved until an event arrives; if necessary, load the new settings at its normal idle boundary.

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
7. Observe a normally authenticated native parent and child, parallel tools, parent idle/background child, and end-of-turn completion; compare the visible counts to the source events. The isolated smoke invocation's OAuth failure is not a blanket blocker for active workers. If that isolated invocation is used again, its own authentication must be resolved through the normal supported login flow; do not change credentials or restart active sessions silently. Record any missing coverage on the Work item.
8. After release and the live check, update and deliver `deployment-package.json` through the package factory under Planning's session, linked to UI agent status. Do not present an unreleased branch as a deployed result.

## Rollback

Roll back the application through the existing release path. Remove only hook entries whose command contains `jarvis-native-worker-v1`; preserve all other hook entries and settings. No Claude conversations or native delegation state need migrating or restoring. The observer never starts model turns or answers approvals.

## Validation

The worker/native lifecycle/subagent/runtime/run-executor suites pass locally. The header-line audit now explicitly classifies new metadata-only formatters and retains the untrusted wrapper on `list_workers`. The frontend production build passes. Isolated production components render the expected fixture counts without browser exceptions. Screenshots are illustrative data, not a live deployment claim. CI remains the final merged-branch check.
