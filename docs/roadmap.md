# Roadmap

Updated 2026-10-26. This is the project direction, not a release schedule. **Current** means present in this checkout. **Next** describes the next development focus. **Evaluation** means a candidate still needs acceptance. **Future** describes the successor product direction rather than functionality delivered by JARVIS.

## One interface for conversation and work

**Current:** The React interface brings spoken and typed conversation, captures, sources, agent messages, workflow progress and voice diagnostics into one workspace. Agent threads and workflow details open in the centre without requiring a separate monitoring application.

**Next:** Make this interface the complete everyday work surface. Keep the conversation understandable while several agents are active: show who is working, what was sent, whether it arrived, which answer belongs to which request, and what needs the user's decision. Prefer a concise overview with detail available on demand.

## Reliable listening and speaking under load

**Current:** Microphone audio goes to local Kroko streaming recognition through sherpa-onnx. Fish Audio supplies speech output. The diagnostics interface exposes the voice pipeline, recognition headroom, incident history, system load and notification activity.

**Next:** Treat speech reliability as an end-to-end property. Measure capture, browser scheduling, transport, recognition, synthesis and playback together while development work is running. A fast model in isolation does not establish a responsive assistant. Prioritize complete first words, bounded queues, useful failure explanations and predictable recovery after interruption or reconnect.

Use the diagnostic record to investigate whether frequent notifications and rendering contribute to lag. Compare normal operation with the quiet-page mode rather than assuming that either the recognizer or the interface is always responsible. Keep the primary view quiet enough to read while retaining the evidence needed to explain an incident.

**Evaluation:** Alternative speech-output providers and local models must match the accepted voice quality and preserve listening performance. Local output is an option to evaluate, not a completed migration or a guarantee that the whole assistant works offline. Recognition, synthesis and the conversational model have separate resource and network requirements.

## Coordination that survives a long piece of work

**Current:** Claude Code and Codex sessions can appear in the agent interface. Conversation records track outgoing messages and replies. Workflows record phases, tasks, waits, blockers and decisions, with detail and attached transcripts available in the workspace.

**Next:** Make those records sufficient to resume work after a page reload, process restart or agent handoff. Keep delivery state distinct from an agent accepting a task, doing it and answering it. Make decisions traceable to the work that needs them. The objective is to let the user supervise several pieces of work without reconstructing their state from unrelated notifications.

A workflow monitor reports what its participating sessions have recorded. It should not imply that a visual progress indicator independently proves the work correct. Completion evidence, review and user decisions remain part of the workflow.

## Provider choice without a different workflow

**Current:** The conversational brain has Claude and Codex provider implementations. Settings expose provider configuration, and the left column shows brain account usage. Development runs retain their own execution path; selecting a different conversational brain does not automatically replace the development executor.

**Next:** Keep conversation, tools, messages and diagnostics consistent when a provider is unavailable or changed. Account failure, model failure, transport failure and exhausted capacity should have distinguishable symptoms and recovery steps. Evaluate integrations through the same user workflow rather than adding a separate interface for every provider.

## From the prototype to Denice

**Future:** JARVIS is the Prototype Engine used to develop and evaluate the interaction model. Denice, pronounced Denise, is the successor product direction. A repository rebrand and a successor engine are separate pieces of work; changing names alone does not deliver the planned architecture.

The product plan describes the engine progression as:

| Engine | Planned role |
|---|---|
| Prototype Engine | The current JARVIS prototype, used to bootstrap Denice. |
| Parris Island Engine | Denice on the new language and system. The planned system “Plans each request as typed steps, offers only the tools the person may use, and checks every call against its step.” |
| Bridge Engine | The next stage adds Agent Guardrails and enforcement capabilities. |
| Proteus Engine | The longer-term AI OS direction brings the Spine system and a node-mesh architecture. |

These are product-planning stages, not four implemented backends in this repository. Their detailed designs and release commitments remain separate from this prototype's current capabilities. The existing repository's license and attribution still apply; documenting a successor does not change them.

The quoted Parris Island description and engine sequence come from the Denice product plan, updated 2026-10-25. That plan is maintained separately from this repository. This page records only the direction relevant to understanding JARVIS's role.

## Experiments outside the delivery commitment

Eye tracking remains an evaluation of whether broad screen attention can help the assistant understand context. It is not a shipped input mechanism or a replacement for explicit instructions. Hardware compatibility, calibration, accuracy and privacy behavior need evidence before it becomes part of the interface.

Other platform and client ideas should be assessed against the same priorities: reliable interaction, understandable work state and measured performance. They are not dated commitments in this roadmap.

## Evidence for a release

A release should demonstrate the complete interaction it changes: spoken and typed input, agent message delivery and replies, workflow decisions, interrupted speech, restart/reconnect behavior and relevant failures. Voice changes additionally need listening comparisons and concurrent-load tests on the intended hardware. Documentation should identify what is present, what has been measured and what remains an evaluation.
