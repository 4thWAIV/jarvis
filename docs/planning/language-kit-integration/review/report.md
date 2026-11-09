# Canonical architecture integration review

Work: `work-4429d24c58b0`. The prior broad integration is preserved at `saved/language-kit-before-author-content` (`e565d78`). This port uses minimal package PR97 at `7e7061d`. It does not include the archived generic inspection, snapshot, observer, classifier, history, follow-up or PDF-generation services.

## Approved language source

The approval is `adopt-architecture-first = approve-architecture-first` in `jarvis-language-kit-one-canonical-source-1.0.1-local663/results/decisions.json`. The plan is retained in the work-view-design worktree at `docs/planning/language-kit-integration/plan.md`. Its text includes:

> The destination is all supported communication, not a permanent optional prose bypass. Initially enable architecture. Carry MetaLang and propose in the pinned kit, but activate proposal-to-vote integration only after its mappings are validated. EvoLang has no file and is unavailable, not a substitute name for the implemented languages. Existing records migrate when revised; do not bulk-convert historical statements as if their accuracy were checked.

> Acceptance requires the same source revision in checker, render, voice grounding, package discussion and outbound references; covered architecture cannot bypass publication validation; failures expose cause/owner/next step; offline reads use the pinned kit; upgrades never rewrite prior accepted artifacts. Estimates must be based on implementation reconciliation, particularly package understanding and renderer observation hooks, rather than a fabricated delivery date. 

## Implementation boundary

`language_kit.py` installs the pinned upstream kit outside application executable source and the presentation inbox. It verifies publisher/release/source/check/digest/manifest policy, safe archive limits and actual check/render fixtures. Node permissions, the macOS sandbox and input/output/time/memory limits bound execution. The checker/renderer are unchanged upstream code. Node 22, 23 and 24 are the supported runtime policy.

`language_documents.py` uses existing local SQLite and package storage for immutable YAML/check/upstream-view bytes, Work linkage, coverage and provenance. An author supplies Markdown understanding; its hash belongs to the canonical revision. No new PDF is generated. YAML remains canonical, and Markdown correspondence remains an author/review responsibility.

`language_projection.py` reuses the existing factory and receiver. Its shared gate checks exact source, upstream view, check evidence, canonical identity, author Markdown and fixed package declarations. Known package materials map to `UNDERSTANDING.md` through PR97's `understanding` declaration and creation test. The existing safe package reader serves the fixed canonical members. The existing file API accepts the expected canonical revision, rejects drift and returns the same bytes verified by the gate; it does not add a generic snapshot service.

`language_document_api.py` exposes one paged envelope to UI, brain and agent submission. Grounding returns the exact known source, declared authored understanding and canonical reference wording. It does not search or discover arbitrary files. Consumed continuations are released and live continuation bytes are bounded. The existing capture/read-back/approval/steer sender carries the reference wording with the reviewed message; no package dispatcher or second sender is included.

The approved updater remains implemented. It performs no checks/downloads before explicit adoption, then applies conditional requests/backoff, visible status, compatibility holds, atomic activation and retained rollback. Explicit adoption after server startup enables its existing scheduler without another restart. Prior accepted artifacts retain their kit/source bytes.

## Enabled and staged

Architecture is enabled for canonical storage, checked publication, authored source discussion and revision-linked reviewed messages. MetaLang and propose are carried but authoring/vote adoption remains staged. EvoLang is unavailable in the pinned release. Current DOM-to-declaration association is unavailable; browser test inspection does not establish a product observer service. Code correspondence and human approval remain separate from language conformance.

## Current verification

The [actual frame](architecture-frame.png) passed on the minimal path, with exact source/check/author links and the original upstream HTML. The [source](source.yaml), [checker evidence](check.json), [author understanding](UNDERSTANDING.md) and [follow-up trial](correction-followup.json) belong to that exercise. Browser inspection is explicitly test evidence, not a product observer service. The capture trial uses simulated next-word approval and a provider test double. Focused storage/gate/API/capture and header-audit cases passed; the [broad local regression](post-integration.xml) passed after final PR97 reconciliation, with 428 passed and two existing skips. Required PR/main CI and actual deployment remain release gates. No deployment or actual external-agent delivery is claimed. The prepared Deployment Package remains a draft until root records the merged/running revision, passing required checks and live result.


The first-open inbox path is covered by [inbox regression evidence](inbox-regression.xml): the actual package_inbox author reply supplies the package ID, then ground derives and verifies its canonical identity without requiring the producer's prior validate response. Supplied document/revision/source IDs remain expected-value checks. Grounding still validates the existing immutable local document; no import or discovery service is added. The reproducing test first failed on missing required IDs, then the focused API/projection/capture regression passed after the fix. The actual frame/capture trial also passed with package-ID-only grounding. The earlier broad regression precedes this caller fix; fresh complete CI remains required on the final head.


## Shared CI fixture repair and main reconciliation

PR95 unit run `42574996120` failed `test_stop_ends_the_voice_and_the_story` and `test_a_drop_the_page_reports_on_its_abort_is_recorded_with_its_reason_cause_and_lost_seconds`. The former acknowledged audio before Stop and could reach an inter-slide interval without active speech; the latter exited its TestClient socket before the server finished recording the abort. Controlled 200 ms answer-read and 50 ms incident-worker delays reproduced both failures. Test synchronization was repaired while retaining every original assertion: Stop waits for emitted unacknowledged audio, and the abort case waits for server closure before reconnect/history assertions. Both passed with the same delays; after removing those delays, the [complete story/transcription files](ci-race-repair.xml) passed. Production story/transcription code is unchanged.

The branch is reconciled onto package integration `108d901`, which contains the capture/UI main changes once. The sole CSS conflict retained both worker-inventory and canonical-package styles. Frontend build and generated contract checks passed; the [release integration regression](release-integration.xml) passed 120 cases and the refreshed actual frame/capture trial passed. Latest-head CI remains required before root's release executor deploys.

## Standard package review integration

Reconciliation with main `ae6d135` retains the canonical source links and the standard package review controls. Canonical publication now accepts only the existing factory's exact generated review story, in its supported language. The factory and gate share the story encoder and standard-vote definition. The vote bytes enter the validation identity; extra narrative, another answer target, another story member and changed bytes are rejected even after the projection was checked. Canonical source, check, rendered view and author bytes retain their existing binding. The acknowledgment does not change the document's proposed status.

The combined language, factory, inbox, author-understanding, contract and header regression passed with the actual pinned kit asset. The actual architecture frame and standard package review browser cases passed afterward; the frame shows the canonical links and review controls together. These are isolated checks, not live adoption. Release receipts are under the release executor's `build/release-20261109/`: `language-vote-integration.xml`, `language-vote-page.xml` and `language-vote-frame/`. Current-head PR checks and containing-main CI remain required before deployment.
