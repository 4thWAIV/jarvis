# Handoff and return

Use this as a short Markdown record, not a new application protocol. Link existing records instead of copying them. Omit irrelevant detail; write `unknown` where a consequential fact is missing.

## Origin to author

| Field | What the fresh author needs |
|---|---|
| Owner and return route | Originating session, existing Work item/piece and optional workflow; who will review, deliver and handle answers. |
| Assignment and authority | Package purpose; source-only or delivery; assigned folder; approved scope and exact approval-record location. Identify proposals still awaiting approval. |
| Audience | Who is reading, what they already know, what they do not know, relevant terminology and accessibility/language preferences. Distinguish observed knowledge from assumptions. |
| Human questions | What the reader wants to understand or do. Include the question behind the requested artifact, not just “make slides.” |
| Desired decision | The real choice and what an answer would authorize, or `information only`. Name any known constraints without inventing a decision to fill the field. |
| Raw evidence | Accessible files, code, data, source URLs and exact revisions when available; current tests and their actual outputs; material counterevidence. Explain how to reproduce a finding when it matters. |
| Origin findings | Conclusions, recommendation and uncertainty, clearly labeled as the origin's account. Include abandoned assumptions when they could otherwise mislead. |
| Missing information | Open questions, attempted research, known access limitations and who can supply the missing fact. |
| Delivery context | Current JARVIS production-guide location, existing package name if replacing one, association IDs and human-answer owner. |

The origin does not have to finish all research before handing off. It must give the author access to evidence and identify assumptions rather than hide them. The author researches the human's questions independently and returns a readiness finding. Receiving a complete-looking handoff is not grounds for accepting its claims.

## Fresh-author invocation

Use the existing package factory's handoff entrypoint described in `docs/making-a-package.md`. The factory creates a new session on the caller's own engine and supplies this skill. The author returns source only; the original command owns checking and delivery with the caller's original session, Work and workflow association. No manual second-agent launch or other-engine fallback is needed.

## Author to owner

Return one result containing:

- **Readiness:** `ready-for-delivery`, `needs-evidence` or `needs-scope-decision`. These describe package readiness, not human approval or Work completion. A source-only result can be content-ready with rendered checks still pending; say that explicitly.
- **Artifacts:** package source entry, referenced assets, supporting evidence, revision or commit, and the exact production-guide path used.
- **Human answer:** a short account of what the package enables the reader to understand or decide. Include the decision keys and precise authorized effect if it asks anything.
- **Reconciliation:** material corrections to the origin's findings, each with evidence. List remaining unknowns, whether they block a decision and the owner/next action for each.
- **Review:** comprehension findings separately from source validation, rendered frame, narration and vote checks. Link actual output or screenshots; identify tests not run.
- **Ownership:** the recipient who now has the work, remaining delivery action, Work/workflow association and answer route. If already authorized and delivered, return actual package/inbox identifiers and delivery receipt. Otherwise say `not delivered`.

The owner opens the returned source and resolves `needs-evidence` or `needs-scope-decision` before presenting an affected choice. They give factual corrections back to the author with evidence; they do not require preserving the earlier conclusion. They accept the author handoff explicitly or identify what is missing. For a ready package, the owner executes the production command in their own session so replies return to the responsible session, associates the existing Work item, and records evidence and the appropriate Work state using the existing reporting tools. Do not invent a new queue or set the user's completion state yourself.

After delivery, use the existing package-linked wait when a human answer is required. Read the recorded answer by its decision key, preserve unanswered decisions as unanswered, and execute only its actual scope. Record content fixes and work still due. A package sign-off reports review; a scoped build vote supplies build authority. Keep those meanings separate through follow-through.
