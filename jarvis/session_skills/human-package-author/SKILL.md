---
name: human-package-author
description: Independently research and author a human-readable JARVIS package from an agent handoff, raw evidence and the reader's questions. Use for decision packages, explanations and review presentations; not for merely polishing an agent's draft or implementing the proposed product.
---

# Human package author

Make the reader able to explain what is happening, why it matters, and what their answer would change. The originating agent's report is an input to investigate, not the answer to preserve.

## Start with a fresh author

The package factory launches the fresh author on the originating session's own engine. If running as that author, continue without recursive delegation. Read the supplied handoff and primary evidence; the originating conversation is not your context. The originating factory process owns validation and delivery after your source is ready. Use the return guidance in [references/handoff.md](references/handoff.md).

Read the handoff, open its sources and load the applicable communication skill. Resolve audience unknowns from available context before asking. Label consequential assumptions. When a missing fact prevents an informed decision, return the precise gap and its owner; continue the explanation that the evidence supports. Do not turn missing information into a vote.

## Find out what the human needs to know

Write a short working answer to each human question before choosing slides. Open primary evidence behind material claims, including claims the origin considers settled. Inspect implementation when describing current behavior; consult current primary documentation or research when the question reaches beyond local evidence. Follow new questions raised by that evidence. A missing link in the handoff is a research task, not permission to omit the issue.

Keep a compact evidence note linking material claims to a source revision and location. Separate observed behavior, reported findings, proposals, inferences and unknowns. Reconcile conflicts explicitly: record what each source establishes and why one applies to this question. Read current source before repeating an earlier conclusion. Approval records establish only their actual scope. Quote source rules, decisions and requirements exactly when citing them; keep agent-authored explanations distinct from those records. Never promote a proposed policy into a prerequisite for using this skill.

Challenge the origin's framing. A successful check may establish syntax, not truth; a mockup may demonstrate a proposal, not deployed behavior; a working dependency may not establish a working integration. If the original recommendation no longer follows, replace it and explain the evidence to the owner.

## Build a visual explanation

Choose the sequence from the reader's questions, known context and intended action. Lead with the unresolved impact or decision, then supply only the context needed to understand it. Explain a problem through its observed effect, supported cause and proposed remedy when those facts exist. Do not invent a cause to complete that pattern.

For architecture, walk through one concrete input to its result. Name the components at the point each does something. Say what each receives, what it changes or decides, what it hands on, and what happens when it cannot proceed. Distinguish existing components from proposed ones in both the visual and the narration. A list of component names, a diagram caption or a spoken title is not an architecture explanation.

Write substantive `say` or `beats` for every explanatory slide. Narration supplies the causal connections that sparse visuals leave out; it must not merely read headings or repeat labels. Explain why the arrangement matters to the reader. Let a viewer understand the visible argument without listening, and let a listener follow without decoding the diagram. Use meaningful slide names, short sentences, readable type and generous space. Prefer a concrete example, a small flow or an option preview to a dense table. Keep source hashes, exhaustive logs and technical detail in supporting material, reachable from the evidence note rather than recited in the main story.

## Ask only for the actual decision

If a decision is ready, show concrete alternatives and their consequences in the human's experience, with one recommendation and its evidence-based reason. State exactly what each answer authorizes, including whether it approves a proposal, a build or a completed result. Use real decision controls from the existing package API, with stable meaningful decision and option keys. Picture each visual alternative; use the API's text-only exception only when the choice cannot meaningfully be pictured. Do not substitute decorative buttons or an implicit yes in narration for a recorded vote.

Make routine technical authoring and implementation choices within existing authority. Do not ask the human to select flags, internal limits or packaging mechanics. A package with no human decision is a presentation. Closing or reviewing a package does not authorize a build. A request to explain a proposal does not authorize adopting it. A vote's effect comes from its question and scoped answer, not from an author's status field.

## Use the existing production path

Locate and read JARVIS's current `docs/making-a-package.md`; use the exact validation command supplied by the factory. For independent use, the installed `talk-to-jarvis` skill resolves the correct checkout and Python command. That document is the single package production API. Follow its current source format, canvas limits, narration, decision and delivery rules. Do not copy its schema or create another renderer, zip builder, answering protocol or delivery mechanism here. If the assigned worktree predates that API, author the source there and use the current JARVIS command; do not port runtime code to make the worktree look current.

Prepare package source and its referenced assets in the assigned work folder. Use `--check-only` for content validation without delivery, and run it last, after your final change: its printed source digest, in your own transcript, is the proof JARVIS reads that you wrote exactly the source delivered. It does not establish rendered fit, readable diagrams, playback or working votes. When authorized to deliver, the owner runs the production command in their own session, with the existing Work/workflow association and return route. If source-only work was assigned, stop at the return contract; do not create inbox traffic to obtain screenshots.

## Review comprehension separately from mechanics

Read the package as the intended audience, without relying on the originating report. Can the reader state the problem, what exists, what is proposed, how the components interact, what remains unknown and what each answer changes? For a narrative slide, could the narration answer “how?” or “why?” rather than merely “what is this called?” Trace those answers to the actual source. Rewrite where the reader would need hidden agent context. When a fresh reviewer is available and authorized, give them the audience brief and package, ask those questions, and examine their answers rather than asking them to grade it.

Keep that comprehension review separate from source validation and rendered checks. The production checker exercises the actual frame, story and voting behavior; inspect its screenshots for visual meaning and legibility as well. A passing checker is not evidence that the story answers the human's questions. A fluent story is not evidence that its votes work. Record what ran and what is still untested, without claiming human acceptance from an agent review.

Return the source, evidence, unresolved facts and review results using [references/handoff.md](references/handoff.md). Remain accountable for content corrections until the owner accepts the handoff. Delivery and a later human answer still need an owner; a finished authoring turn does not complete the Work item.
