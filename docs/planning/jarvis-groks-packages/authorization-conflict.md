# Package answer authority conflict

Work: `work-afc13119d082`. Finding during release integration. New product changes stopped pending root/design reconciliation; existing test processes may finish.

## Approved requirement

`technical-spec.md`, `followups-and-authorization`:

> Add a host-owned `PackageActionScope` for package mutations.

> Explicit UI controls mint the corresponding exact scope without classification.

> Require that scope at `prepare_package_followup` and the existing package decision, sign-off, and execution dispatcher entries when operating on package content.

> Inspection and observation never create a mutation scope. A story action inside the sandbox cannot provide one.

## Current source path

1. `frontend/src/packageBridge.ts`, `onMessage`, accepts a `save` message from `frame.contentWindow`. Its `save` function forwards the package-supplied name and bytes through `packages.saveResult`. It does not establish a host-owned action or inspect a trusted user event.
2. `jarvis/packages_api.py`, `save_result`, saves the bytes and invokes `on_saved(package)`.
3. The installed callback is `server._inbox_saved`, which calls `_inbox_recount`.
4. `server._inbox_recount` reads `package_inbox.questions(package)`, settles answered waits and calls `_review_from_package` for review packages.
5. `work_api.review_from_package` reads `asked.answer` from the result record and invokes `review` using its answer and note. For an approvable Work item, the `approve-item` answer becomes its approval. No `PackageActionScope` or host-owned direct action is checked along this path.
6. Saved answers are also adopted at open/startup and after signoff. `tests/test_review_packages.py` intentionally exercises those existing behaviors with `_decide` writing the package's own answer record, followed by recount/startup.

The implemented dispatcher guard does not cover this result adoption path. The new snapshot and observation services do not supply authorization for it. Code-level source tracing establishes the path; an adversarial browser demonstration has not been run, and no live package or Work record was changed.

## Reconciliation needed

The broad mutation requirement and the existing frame/file answer adoption path cannot both be described as covered by the new scope guard. The approved specification explicitly preserves existing package decision behavior and names dispatcher guards, while the existing page persists decisions through a separate result bridge. The design owner must state how a package answer becomes a trusted user act, including old answer-file adoption, without relying on package-controlled content as authority. This worker has not removed the bridge, changed the existing voting UI, changed backfill semantics or invented another answering protocol.

The latency-only draft at `release-decision/content.json` is not a complete terminal blocking package until this conflict is reconciled and reflected in the author handoff. A latency vote would authorize only its stated pause and retest, not a resolution of this authority path.
