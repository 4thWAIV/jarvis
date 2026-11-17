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

## Start from where the reader is

Before choosing slides, write in evidence.md, from the records rather than the handoff's account:

- **The reader's task:** what this package asks the reader to do, in one sentence. Usually it is deciding something, or understanding something before a later decision.
- **What the reader already knows and approved:** the earlier decisions, approvals and packages this one builds on, each quoted from its record with its date.
- **What happened since:** the events that brought this to the reader now, including any failure, its observed effect and its supported cause.
- **What each answer changes:** what changes in the reader's work, product or schedule under each option.

Assume the reader is coming from other work. They made the earlier decisions but don't hold their details, and the names this work uses for its parts (a stage, a contract, a wall, a check, a slug) mean nothing to them until the package says what each one is. An audience note such as "he approved this an hour ago" is a reason to say briefly what he approved, not to leave it out.

## Order it as a decision memo

Order the package the way a decision memo is ordered: the situation, what went wrong or changed, the options and their consequences, the recommendation, then the decision. This is the order of a government action memo and of the situation–complication–question–answer pyramid. Use it for every package that asks something:

1. **Opening.** Say what this is about in plain words someone new to the work would follow. Say where things stand, then the bottom line: what was found and what is recommended. Name the reader's task as the thing they will do at the end ("At the end you decide whether …"). Do not make the request itself the opening. The deck's `conclusion` is that bottom line, worded for the same reader: never a bare "Approve X".
2. **What was already decided.** Cover what the reader approved or decided that this touches, quoted exactly, and what stays as it was.
3. **What happened and why.** Explain the observed effect, its supported cause and its consequence, in the reader's terms. Name a failure plainly, with its cause. Do not invent a cause to complete the pattern.
4. **The options and what changes.** Set the alternatives side by side, each with its consequence in the reader's experience: what they would see, what work it starts or stops, what it costs in time. Give one recommendation and its reason.
5. **The decision.** Say what each answer sets going.
6. **Detail last.** Identifiers, file and function names, internal mechanisms, ledgers and logs go on slides placed after the decision (place it with `{"decision": "<id>"}` before them) or in the evidence. Any slide before the decision that cannot avoid a code name says in plain words what it is, on that slide.

Explain a problem through its observed effect, supported cause and proposed remedy when those facts exist. Each slide makes one point, and its title states that point. Where a handoff prescribes a slide order or forbids explanation (`shape`), follow it only where it agrees with this order. Where it puts the request first, or leaves out what the thing is or what happened, follow this order, and record in evidence.md what you changed and why.

## Write for an expert who was not there

Write plainly: short sentences, everyday words and the standard name for each thing. Plain does not mean simplified. Keep the full substance, and do not explain general ideas an expert in the field knows. Do explain this work's own names and history, because those are what the reader was not there for. Leave out praise, reassurance and grading ("critical", "simple", "minor"), sentences that tell the reader what matters or what to think, and any point already made on another slide.

## Build a visual explanation

For architecture, walk through one concrete input to its result. Name the components at the point each does something. Say what each receives, what it changes or decides, what it hands on, and what happens when it cannot proceed. Distinguish existing components from proposed ones in both the visual and the narration. A list of component names, a diagram caption or a spoken title is not an architecture explanation.

When the assignment names a checked canonical architecture document, the page is JARVIS's projection of that exact revision and you write `package/UNDERSTANDING.md`: its plain explanation, in that walk-through form, of what the YAML declares and nothing it does not. Say what its coverage leaves unresolved or outside the language. You may add `package/story.json`, the walk-through JARVIS tells over the rendered page, in the strict form the guide's Architecture documents section gives: beats that point only at targets the guide lists, and each question the reader should answer declared in its `questions` with the slide that waits for it. The factory adds the standard vote last and states the handoff's purpose; when the handoff says `"approval": true` it also asks his approval of the design, once, before the vote, so walk him through what he is approving and what he could ask to change. Never ask the vote or the approval yourself, never ask for approval of a review, and never write anything else under `package/`. Validate with the exact check command the assignment gives.

Write substantive `say` or `beats` for every explanatory slide. Narration supplies the causal connections that sparse visuals leave out; it must not merely read headings or repeat labels. Explain why the arrangement matters to the reader. Let a viewer understand the visible argument without listening, and let a listener follow without decoding the diagram. Use meaningful slide names, short sentences, readable type and generous space. Prefer a concrete example, a small flow or an option preview to a dense table. Keep source hashes, exhaustive logs and technical detail in supporting material, reachable from the evidence note rather than recited in the main story.

## Ask only for the actual decision

If a decision is ready, show concrete alternatives and their consequences in the human's experience, with one recommendation and its evidence-based reason. State exactly what each answer authorizes, including whether it approves a proposal, a build or a completed result. Every decision gives the reader a way to turn it down. An approval question offers exactly Approve (`approve`) and Reject (`reject`, listed in the decision's `needs_words` so a rejection carries what to change). A choice between alternatives adds Reject the same way when none of them is "do nothing". Never relabel a rejection as "Change it" or similar. Use real decision controls from the existing package API, with stable meaningful decision and option keys. Picture each visual alternative; use the API's text-only exception only when the choice cannot meaningfully be pictured. Do not substitute decorative buttons or an implicit yes in narration for a recorded vote.

Make routine technical authoring and implementation choices within existing authority. Do not ask the human to select flags, internal limits or packaging mechanics. A package with no human decision is a presentation. Closing or reviewing a package does not authorize a build. A request to explain a proposal does not authorize adopting it. A vote's effect comes from its question and scoped answer, not from an author's status field.

## Use the existing production path

Locate and read JARVIS's current `docs/making-a-package.md`; use the exact validation command supplied by the factory. For independent use, the installed `talk-to-jarvis` skill resolves the correct checkout and Python command. That document is the single package production API. Follow its current source format, canvas limits, narration, decision and delivery rules. Do not copy its schema or create another renderer, zip builder, answering protocol or delivery mechanism here. If the assigned worktree predates that API, author the source there and use the current JARVIS command; do not port runtime code to make the worktree look current.

Prepare package source and its referenced assets in the assigned work folder. Use `--check-only` for content validation without delivery, and run it last, after your final change: its printed source digest, in your own transcript, is the proof JARVIS reads that you wrote exactly the source delivered. It does not establish rendered fit, readable diagrams, playback or working votes. When authorized to deliver, the owner runs the production command in their own session, with the existing Work/workflow association and return route. If source-only work was assigned, stop at the return contract; do not create inbox traffic to obtain screenshots.

## Review comprehension separately from mechanics

Read the package cold, as the reader coming from other work. Use only the slides and narration before the first decision, without the handoff or your notes. Write in evidence.md the answer each one gives to these questions: What is this about? What did I already approve that it touches? What happened, and why? What are my options, and what changes for me under each? What is recommended, and why? What am I asked to decide? Rewrite until every answer is on those slides, and until no slide before the decision needs a name it has not explained. Then: can the reader state the problem, what exists, what is proposed, how the components interact, what remains unknown and what each answer changes? For a narrative slide, could the narration answer “how?” or “why?” rather than merely “what is this called?” Trace those answers to the actual source. Rewrite where the reader would need hidden agent context. When a fresh reviewer is available and authorized, give them the audience brief and package, ask those questions, and examine their answers rather than asking them to grade it.

Keep that comprehension review separate from source validation and rendered checks. The production checker exercises the actual frame, story and voting behavior; inspect its screenshots for visual meaning and legibility as well. A passing checker is not evidence that the story answers the human's questions. A fluent story is not evidence that its votes work. Record what ran and what is still untested, without claiming human acceptance from an agent review.

Return the source, evidence, unresolved facts and review results using [references/handoff.md](references/handoff.md). Remain accountable for content corrections until the owner accepts the handoff. Delivery and a later human answer still need an owner; a finished authoring turn does not complete the Work item.


## Inspect the built package before delivery

The factory keeps your session open after source creation and returns the actual rendered screenshots and exact archive in a visual-review request. Open every listed screenshot with the existing image-viewing tool on your engine: Claude Read or Codex view_image. Check that illustrations appear, their internal labels are readable and unclipped, and they communicate the view's point. Do not substitute DOM dimensions or a source-file read for this inspection.

Use the story and authored understanding to identify every intended reader view. For interactive pages, include the relevant tabs and expanded views in existing story `show`/`click` actions so the factory renders and checks them. Inspect supplied image/document pages intended for human viewing with existing browser/document tools. Account for every built member in the response the factory requests. Supporting code, unused assets and decorative files do not become extra reader pages. If an intended view cannot be inspected, return blocked and name the exact missing check. Use not-applicable only when the package's contents have no visual material and record why.

Return the requested review JSON with specific findings and the screenshot and attachment evidence you actually inspected. Acceptance covers the unchanged built bytes and template named in that request. If repair is needed, change your source/assets, run the supplied check-only command after the final edit, and return repair; the factory builds and renders again before another review. It allows one repair attempt in this same session. Do not launch a reviewer, deliver, or ask the human to debug the package. A machine pass cannot establish the readability of content inside SVG, raster, canvas or PDF material.
