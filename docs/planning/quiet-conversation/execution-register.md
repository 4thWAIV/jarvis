# Voice and audio execution register

Updated: 2026-11-04.

## Count and scope

13 tracked workstreams: 1 authorized execution workflow and 12 deferred backlog workflows. The immediate incoming/outgoing audio delivery requires the one core workflow; the 11 future workflows are enhancements or separate projects, not prerequisites except when their stated entry condition exposes a current release failure.

The core workflow contains seven phases/tasks, not seven separately launched workflows. GitHub Issues is disabled; the live workflow is tracked by JARVIS and each backlog item is filed below in the repository. Backlog entries have no live workflow ID until promoted.

| Workstream | State | Owner | Record |
|---|---|---|---|
| Voice and audio core improvements | Accepted by Diagnostics; awaiting PR 62 repair base | Diagnostics | `wf-fc70d73b6799`; [execution handoff](diagnostics-execution.md), [step-by-step plan](delivery-plan.html) |
| Voice capture cues: start, stop and turn-completion sounds, with an on/off setting | Built: start and stop sounds, the tentative and likely-end pause cues, and their tuning in Settings and by the brain; the sounds are placeholders to tune | Diagnostics | [completion-cues](backlog/completion-cues.md) |
| Camera presence and hand gestures | Backlog; not started | Diagnostics planning | [camera-and-gestures](backlog/camera-and-gestures.md) |
| Explicitly armed wake-word mode | Backlog; not started | Diagnostics planning | [wake-word](backlog/wake-word.md) |
| Full interaction-mode experience | Backlog; not started | Diagnostics planning | [interaction-modes](backlog/interaction-modes.md) |
| Recognition and output model alternatives | Backlog; not started | Diagnostics planning | [speech-model-evaluation](backlog/speech-model-evaluation.md) |
| Native echo-reference and audio processing | Backlog; not started | Diagnostics planning | [native-echo-reference](backlog/native-echo-reference.md) |
| Adaptive patience and spoken pacing | Backlog; not started | Diagnostics planning | [adaptive-pacing](backlog/adaptive-pacing.md) |
| Durable typed delivery and safe retry | Backlog; not started | Diagnostics planning | [typed-delivery](backlog/typed-delivery.md) |
| Provider conversation/context recovery | Backlog; not started | Diagnostics planning | [provider-context-recovery](backlog/provider-context-recovery.md) |
| Windows and mobile audio qualification | Backlog; not started | Diagnostics planning | [platform-audio](backlog/platform-audio.md) |
| Cancelling a reply in progress | Backlog; not started (left out of the core build by Tim, 2026-11-04) | Diagnostics | [reply-cancellation](backlog/reply-cancellation.md) |
| Additional notification channels | Backlog; not started | Diagnostics planning | [notification-channels](backlog/notification-channels.md) |

## Core phases

1. baseline-and-contract: reconcile the live Matt PR and main; establish an isolated worktree and shared identities.
2. quiet-input-settings: preserve Mute/Off across reloads and implement both user-selectable capture-stop and tab-switch settings.
3. patient-turns: protect input deadlines, recognition watermarks and user continuations.
4. playback-and-echo: targeted Skip/Stop behavior, accurate outcomes, partial-error display and double-talk checks.
5. quiet-announcements: durable attention records, scripted-speech migration, bounded brain summaries and quiet return.
6. integration-review: local tests, English/Spanish, callable UI parity and integrated candidate.
7. local-human-validation: show Tim the running candidate; keep merge and live deployment pending that validation.

## Tracking rules

Diagnostics owns progress and reports it on the existing core workflow ID. Record worktree, branch, revision, phase changes, blockers, test evidence and local URL. Do not create a duplicate core workflow. File any scope added later in this register before counting it. Promote a backlog item only as a separately authorized workflow and fill in its live ID then.

Defaults are assessed during candidate validation; they do not block implementing both user choices. Acknowledgment of this handoff and actual implementation start are recorded separately; assignment alone is not evidence that coding has started.

## Latest acknowledgment

Diagnostics accepted ownership in the live workflow. Its next action is to wait for its existing PR 62 repair agent to push, then create `feature/voice-audio-core` in an isolated worktree from that repaired base. The core implementation has not yet been reported started. The existing repair is a base dependency, not a newly requested voice workflow.

## Visual journey

[Open the seven-phase execution journey and backlog explanations](execution-journey.html). This is a status snapshot; current execution state remains in the JARVIS workflow monitor.
