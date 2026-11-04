# Diagnostics execution: voice and audio core

Status: Accepted by Diagnostics; preparing against the pending PR 62 repair base.
Workflow: `wf-fc70d73b6799` — Voice and audio core improvements.
Owner: Diagnostics (`a5b4be76-7b3d-4a0c-9220-817d38b9e5f3`).

## Start now

Tim authorized execution of one workflow for the remaining incoming and outgoing voice/audio work. Take ownership of the existing workflow ID above, create an isolated implementation worktree, and launch the build through your normal background workflow. Acknowledge in the workflow with the worktree and branch, then report phase_started when work begins. Do not create another core workflow or interrupt/cancel the existing Matt-PR repair agent.

Read [delivery-plan.html](delivery-plan.html) for detailed steps and acceptance checkpoints, and [execution-register.md](execution-register.md) for scope and backlog. The plan files currently live in the shared primary checkout at `docs/planning/quiet-conversation/`. Read them there before branching and bring the required planning artifacts into your worktree so they are included with the implementation and do not disappear from later handoffs.

## Settled scope

- First deliver retained Mute/Mic Off and explicit listening activation. Quiet preferences must be read before device acquisition; reload must not unmute or arm input.
- Both microphone-stop outcomes are user settings: keep previously captured words or discard unfinished input. Both tab-switch outcomes are user settings: continue an explicitly enabled session or stop listening. Persist per user; test all four combinations. Defaults are assessed separately and must not be used as a reason to postpone implementation. Keeping words is not permission to invent an automatic-send operation.
- Complete one-owner capture, accurate readiness, quiet PCM consumers, late-result/retry protection and disconnect-safe input.
- Implement patient, revision-safe turn completion with originating-capture recognition watermarks and no premature audio over continuations.
- Reuse Matt's full-text Conversation and reply-specific Skip; finish partial-error states, targeted interruption, truthful audio outcomes and current-platform echo qualification.
- Route routine events through durable attention/admission; remove scripted conversational announcements; use bounded read-only brain summaries or visible status. Preserve exact requested and approval readback.
- Keep settings and controls brain-callable through the same validation/result contract, localize English/Spanish and preserve transmission approval rules.

## Base and integration

The last inspected PR 62 head was `676a51a`, still open with CI queued. Check current state rather than assuming it remains there. You already have a Matt-PR repair agent; coordinate with its result and avoid overlapping edits. Use merged main when available or a clearly recorded integration base containing the accepted PR work. Keep new implementation in its own worktree. Do not duplicate Matt's components.

After one shared contract, parallelize input/turn work, attention/announcement work and playback/UI work with one integrator controlling shared server/protocol edits. Keep all phases in this single monitored workflow. Update the register if any genuine separate required workflow appears.

## Validation and completion

Use synthetic clocks, PCM and recognizer fixtures before real microphone tests. Run targeted checks locally, then integrated regressions before a batched CI push. Exercise i9 and Apple Silicon when available, with actual speaker/microphone overlap. Do not claim hardware testing that was unavailable. Bring the running local candidate, URL, worktree/revision and concise evidence back to Tim for validation before merge or replacing the live experience. Report blockers with their exact impact; continue independent work where possible.

Do not mark the workflow succeeded merely because implementation or tests finish: keep local-human-validation waiting until the user validates the candidate, then complete the agreed release handoff. No automatic merge/live deployment is authorized by this handoff.

## Deferred work

The eleven individual files in [backlog](backlog/) are filed, not scheduled. Do not launch them as part of this build. A reproduced echo failure in the current supported path is a core acceptance blocker and must be addressed before claiming readiness; the broader native-DSP redesign remains future work.

## Reporting and credentials

Use the existing workflow reporting API/CLI. Record phase/task transitions and material progress, including the implementation subagent and worktree. Use existing authenticated local tooling. Keep session credentials only in memory or an existing private runtime store; never place tokens in this repository, transcripts, handoff text or command-line output. Do not repeatedly ask for passwords already available through the authorized local configuration. If access is actually unavailable, report the exact missing capability without exposing a secret.
