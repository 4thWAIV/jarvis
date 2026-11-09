# Language-kit integration and adoption

Work: `work-4429d24c58b0`. Root coordinates the live primary checkout; this worker uses only its isolated language worktree. Minimal package PR97 is the release dependency.

## Validate the release candidate

The pinned source is `4thWAIV/agent-guard`, release `0.1.108172209-pre-release`, commit `c918581ffda7a6fad7c05dde13f54aeb5760fdfb`, asset `evolang-kit.zip`, SHA256 `0112ee38bec63affd08d952d249336c6dbf8602076baab94ff7982b996297ef0`.

Run the language/package/capture regression and actual frame sequentially, so frontend rebuilds cannot remove assets during another server fixture's import. `JARVIS_LANGUAGE_KIT_ASSET` supplies the external pinned ZIP; `JARVIS_LANGUAGE_REVIEW_DIR` selects isolated frame evidence.

```
python -m pytest tests/test_language_kit.py tests/test_language_kit_updates.py tests/test_language_documents.py tests/test_language_projection.py tests/test_language_document_api.py tests/test_language_document_followup.py tests/test_package_understanding.py
python -m pytest -m page tests/test_voice_page_language_documents.py
python scripts/generate_ui_contract.py --check
```

After package PR97 merges, reconcile onto its final main revision and pass the required PR and complete merged-main workflows. Root preserves the primary checkout's existing edits and uses the existing updater for the reviewed language version:

```
.venv/bin/python -m jarvis.update --check
.venv/bin/python -m jarvis.update --to 95
```

## Adopt and inspect the kit

From the deployed runtime, with its normal data directory and supported Node on PATH:

```
python -m jarvis.language_kit_updates status
python -m jarvis.language_kit_updates adopt-pinned
python -m jarvis.language_kit_updates status
```

Initial adoption is explicit. Starting the server alone performs no kit checks/downloads. The existing scheduler notices later explicit adoption and honors its recorded next-check/backoff policy. Settings → Updates and language_document capabilities expose the active version and errors. Failed installation/gates retain the working version.

## Canonical operations

UI/agent HTTP uses POST `/api/language-documents`; the brain uses `language_document`. RequestBody is the shared schema. Concatenate `data` while following `next` through `continue` to recover the complete envelope. Draft validate takes source, Work ID, scope revision, author, coverage, optional document/parent revision and evidence. `readable_markdown` supplies the author understanding required for new publication. A correction proposes a child revision; it does not edit the original.

Render/publish require document ID, canonical revision and expected source_revision. Publication uses the existing factory/receiver and retains source.yaml, check.json, canonical.json, index.html and UNDERSTANDING.md. The manifest brief and material-to-Markdown associations feed the minimal package creation check and existing authored-content reading path. No arbitrary source-format parser or PDF builder is needed.

Ground additionally takes package_id and optional inbox_id/declaration. It checks the fixed canonical files and declared author Markdown against the requested revision. It returns source/understanding plus followup_reference. Include that reference in the addressed text passed to the existing capture tool, then read_capture and the existing approval/steer_session flow. Approval binds the existing full capture wording and recipient. No new sender, classifier or package-history context is introduced.

PackageView entry and source/check/author links use the existing file route with `canonical_revision`. The handler verifies expected identity and fixed source/view bytes before returning them; changed bytes or identity are refused. Current DOM observation is not claimed.

## Updates and rollback

```
python -m jarvis.language_kit_updates check
python -m jarvis.language_kit_updates status
python -m jarvis.language_kit_updates rollback
```

Language changes hold candidates for compatibility review. `activate-reviewed --digest DIGEST_FROM_REVIEW` selects an explicitly reviewed candidate and clears its hold. Rollback holds the rejected candidate against automatic reactivation. Existing document revisions remain pinned and are not rewritten.

## Actual deployment handoff

After installation, root checks the running version, active kit/runtime, a newly published authored architecture, revision-bound source/check/author links and existing reviewed follow-up behavior. Root fills review/deployment-package.draft.json with actual merged/live evidence, factory-delivers the Deployment Package with `--work work-4429d24c58b0` and links it to Work. This architecture milestone does not complete every supported communication capability.

`python -m jarvis.session_skill install` refreshes the existing installed agent skill from the shared shipped authoring contract. A new brain generation loads that same contract. This worker has not changed the live installed skill or restarted the primary server.
