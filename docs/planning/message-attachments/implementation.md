# Local file attachments from JARVIS: Diagnostics execution plan

Prepared 31 October 2026 against `61c9761f47eed67c1754144e1ba4bcea5abbec49`. Status: implementation handoff, not implemented. Execute in a separate worktree as a reported JARVIS workflow. This plan does not send a message, modify the running app, or start implementation.

## Result to deliver

A user can ask JARVIS to attach a named local file to a drafted team message. JARVIS adds the file to that exact draft, reports the resulting revision and attachment names/sizes, and uses the existing review/send flow. The file shown in review is the immutable snapshot whose bytes are eventually sent. Browser-created and brain-created drafts use the same attachment and approval machinery.

Concrete acceptance example: attach `.agents/in-progress/orb-icon-transform-study.html` to a draft for Matt. That file is 536,615 bytes at this audit. An implementation test uses a harmless fixture, not a live message to Matt. The real study is an acceptance example, not a special-case filename or path.

## Cause and existing implementation

| Boundary | Current source | Finding |
|---|---|---|
| Tool definition | `jarvis/jarvis_mcp.py`, `draft_message` around line 1075 | Text, subject, recipient/reply and draft revision are exposed; no file inputs or attachment tool. |
| Tool implementation | `jarvis/message_approvals.py`, `MessagingTools.draft_message` around line 521 | Existing attachment IDs are preserved on edits; no new local bytes can be staged. |
| Browser attachment path | `frontend/src/MessageComposer.tsx:88`, `queue.ts:addFiles`, `messagingApi.ts:addFile` | The page creates/saves a draft, streams selected file bytes, appends returned IDs, then saves the draft. |
| Snapshot service | `jarvis/messaging_files.py`, `FileSnapshots.snapshot` | Copies bytes privately, enforces actual streamed size, hashes them and records an owner/server/message-bound attachment. |
| Local upload route | `messaging_files.router`, POST `/api/team/messages/drafts/{message_id}/files` | Receives browser bytes; it does not resolve local filesystem paths for the brain. Staging alone does not put the attachment into a draft. |
| Shared writer | `jarvis/messaging_api.py`, `LocalMessaging.write_draft` / `save_draft` | Checks revision and attachment ownership; preserves the normal content-revision, capture invalidation and delivery-hint path. |
| Runtime | `jarvis/messaging_runtime.py`, `MessagingRuntime.__init__` | Already holds `files`, `local`, `reviews` and `tools`; connect the new operation here. |
| Async dispatch | `jarvis/server.py:3961` | Internal tool dispatch awaits awaitable handler results. Existing `_messaging_tool` returns the called method's result. Test all actual dispatch paths rather than adding another event loop. |
| Existing limits | `jarvis/messaging_wire.py` | 10 attachments, 25 MiB per file, 100 MiB per package. Reuse these constants. |

No new Team Server endpoint, transfer protocol, upload engine, database, attachment manifest or approval subsystem is needed. The change is a local-file-to-existing-draft integration.

## Proposed tool contract

Add a dedicated acting tool, `attach_message_files`, leaving `draft_message` backward compatible. Requiring a previously saved draft makes destination, identity and revision explicit before any snapshot is created. This is a proposed implementation contract; Diagnostics should reconcile it with current repository interface rules before editing.

```json
{
  "name": "attach_message_files",
  "description": "Attach the local files the user requested to an existing team-message draft. Nothing is sent. Requires the exact draft revision last read. Returns the new revision and attachment names and sizes; review that revision before sending.",
  "inputSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "message": {"type": "string", "format": "uuid"},
      "draft_revision": {"type": "integer", "minimum": 1},
      "paths": {
        "type": "array",
        "minItems": 1,
        "maxItems": 10,
        "uniqueItems": true,
        "items": {"type": "string", "minLength": 1}
      }
    },
    "required": ["message", "draft_revision", "paths"]
  }
}
```

The actual schema's limit must be generated from the shared attachment constant, not maintained as another independent value. Validate all arguments in the handler too, including rejecting booleans as integer revisions. `paths` contains absolute local paths: no implicit cwd, shell expansion, globbing, directory recursion or URL downloading. A path must name one regular file. The brain resolves conversational names before calling; it must not guess among ambiguous candidates.

Proposed injectable Python boundary:

```python
class DraftFileAttacher(Protocol):
    async def attach(
        self,
        identity: Identity,
        message_id: UUID,
        expected_draft_revision: int,
        paths: Sequence[Path],
    ) -> ports.Draft: ...
```

Use existing `Identity`, `ports.Draft` and contract errors. Place the implementation in the existing messaging module structure, using focused helpers if necessary; do not duplicate snapshot or draft-writing logic. Return metadata to the brain, never attachment bytes or a private snapshot path. Successful output must name the draft, new revision, added filenames and sizes and state that nothing was sent. Invalid inputs and refusals must state that the draft was unchanged.

## Workflow: prepare → attach → integrate → verify → hand off

### Prepare the isolated worktree

1. Read repository instructions and record the actual source branch/commit. Compare current files with the audit locations above; refresh moved symbols and changed contracts.
2. Create an isolated branch/worktree, for example `fix/brain-local-message-attachments`. Check that this plan and the handoff prompt are present. They may still be uncommitted in the source checkout, so copy them explicitly when necessary; never copy secrets or unrelated untracked design work.
3. Start a JARVIS workflow named **Local message attachments** with phases `baseline`, `file-attachment-service`, `tool-integration`, `verification`, and `handoff`, using `jarvis/workflow_report.py` and a workflows-scoped access key. Follow the installed reporting skill for authentication. Keep credentials out of the worktree and output. Attach the executing session when its real session ID is available; do not invent one.
4. Record baseline focused test results before changes. Keep unrelated failures separate and report them with their raw result. Avoid full CI for a planning-only checkpoint.
5. Agree module ownership and the concrete tool/service interfaces once. One implementation owner can finish this change; a second worker can prepare fixtures/tests against the agreed contract. An independent reviewer checks the merged diff. Do not split tightly coupled runtime/writer edits among several agents.

### Implement the local file attachment service

1. Capture the current server/owner/node identity and verify messaging availability, draft ownership, exact integer revision and editable state. Explicitly reject approved/submitting/accepted or otherwise non-editable drafts using existing send-state rules. Preflight attachment count against existing attachments plus requested files.
2. Resolve source access through existing policy and filesystem helpers. Inspect `repo_read.private_reason`, `repo_read.sensitive_reason`, `folder_policy.py` and `folder_readers.py` before choosing the reader boundary. Keep private credentials, keys, tool tokens and provider-private data blocked. Do not require users to register a new folder merely to attach a specifically named ordinary local file.
3. Cover the motivating hidden-path case explicitly: `.agents/in-progress/orb-icon-transform-study.html` is a named user document. The current folder policy distinguishes explicitly named dot-folders from unrestricted hidden-file access. Reuse that distinction; do not reject every hidden parent and do not broadly enable hidden/credential files under the repository. Add a focused fixture for an ordinary file inside an explicitly named `.agents/in-progress` folder and for blocked sensitive siblings.
4. Open and stream through a held, validated file handle. Reject directories, sockets/devices/FIFOs and unsafe symlink/reparse traversal according to the existing access rules. Prevent a path swap between policy checking and reading; a `resolve()` followed by an unguarded `open()` is insufficient. Reuse appropriate existing traversal primitives rather than copying them. Retain binary bytes exactly; do not route arbitrary attachments through a text preview reader or its smaller preview limit.
5. Keep disk I/O off the event loop while using the existing async snapshot service. Read bounded chunks and use cancellation-safe handle ownership. Never use `asyncio.run()` inside the running server or hold an unbounded read in memory.
6. Call `FileSnapshots.snapshot()` for each authorized source with the captured namespace and draft ID. Enforce count, actual file bytes and aggregate package bytes, including existing attachments. MIME type is only a hint; use existing normalization. Do not trust an initial stat size as the streamed size limit. Use the snapshot hash and bytes as the later review/send authority.
7. Make batch visibility atomic: stage all requested files, then append their attachment IDs in one `LocalMessaging.write_draft()` save with the exact expected revision. Preserve subject, text, recipient, reply/thread links, related messages and existing attachment order. Mark the edit agent-drafted. Never replace the existing attachment list accidentally.
8. Immediately before the save, revalidate current identity/connection generation and draft revision/state. This is necessary because staging awaits. The existing runtime writer lambda accepts `who` but calls a writer that resolves the current pairing; do not assume that parameter alone binds an asynchronous operation. A sign-out, team switch or concurrent edit must refuse the commit, not attach to another namespace or draft.
9. On any file failure, limit failure, cancellation or revision/identity conflict, leave the draft unchanged. Remove partial copies using existing snapshot cleanup behavior. Completed but unattached staging records may be reclaimed by the existing grace-period cleanup; document and test that path. Never broadly delete snapshots or remove anything referenced by another draft or approved revision.
10. Retain existing approval invalidation through the shared writer. An attachment added after review must close the old capture and require review of the new content revision. The attachment operation itself never approves, uploads for delivery or sends.

### Retry behavior without a new subsystem

Require `draft_revision`; never default to the latest revision. If the first call committed but its reply was lost, retrying with the original revision must fail as a conflict and add no files. JARVIS then reads the current draft, reports what is attached, and proceeds to review it rather than retrying blindly with an updated revision. This is safe conflict-based retry behavior, not a claim of idempotent success.

Reject duplicate paths within a batch after the same path-identity normalization used by the source reader. Do not deduplicate unrelated files solely because their contents match. Deliberate repeated calls at newly read revisions are separate edits; no new request-ID database is needed for this first fix. Test cancellation both before and after the draft-save boundary, and report the resulting revision if the commit already happened.

A source edited after attachment does not change the saved snapshot. If ordinary mutation occurs while streaming, reject a detected size/mtime/identity change rather than presenting a knowingly mixed read. File-system metadata checks are not a universal atomic snapshot guarantee; review and delivery refer to the exact copied bytes and hash. No copy-on-write filesystem feature is required by this change.

### Integrate the brain tool and UI behavior

1. Register the tool schema in `jarvis_mcp.py` and the handler in `server.py`. Inject the attachment service through `MessagingRuntime`; keep a single snapshot/writer path.
2. Add the tool to the acting-tool classification and test the existing authorization/taint gate. Do not copy `draft_message`'s exemption reason: its current wording says it does not read, whereas this operation reads local bytes. Decide classification from the actual gate and metadata-only result; preserve existing untrusted-content protections without introducing another approval system.
3. Test that async results are awaited through the real internal tool dispatch and unavailable messaging yields a useful refusal. Both existing brain providers consume the shared tool definition.
4. Update `jarvis/jarvis_home/CLAUDE.md`: obtain/resolve the recipient, create or read a draft, attach requested files, use the returned revision, request review, and send only through the existing user-approved flow. Never claim a filename in the message body is an attachment.
5. Include an example with the orb study and one with multiple binary files, plus the stale-revision recovery rule. Keep prompts free of private paths/credentials except the public repo example.
6. Reuse delivery hints so the attachment list and review capture update in the current UI. Verify that browser-added files remain when JARVIS attaches more and vice versa. No new composer UI is required unless a current rendering defect is found.

## Verification matrix

| Case | Required result |
|---|---|
| Tool happy path, HTML and binary fixture | Files visible in the exact draft; size/hash and bytes match; new revision returned; no send or remote upload during attach. |
| Explicit ordinary hidden-folder document | The `.agents/in-progress` example succeeds through the intended source policy. Private/credential siblings remain blocked. |
| Missing, unreadable, directory, special-file, link swap | Bounded refusal; draft and review unchanged; handles/partials reclaimed. |
| Existing attachments and text edit | Attach preserves existing files, text, recipient and reply metadata; subsequent text-only `draft_message` edit preserves all attachments. |
| File-only message | Existing empty-text draft plus attachment reaches review through normal validation. If current draft creation rejects empty content, resolve through the common writer without fake placeholder text. |
| Count, file and aggregate limits | Shared 10-file/25-MiB/100-MiB limits enforced against actual streamed bytes and existing attachments; boundary cases included. |
| Multi-file partial failure | No partially attached batch; completed staging records safely reclaimable; prior attachments untouched. |
| Source grows or changes during read | No unbounded read; detected mutation refused; source change after commit cannot change snapshot bytes. |
| Retry after response loss | Original-revision retry refuses without duplication; reading current draft reveals the committed attachment. |
| Concurrent page edit or discard | Revision/state conflict; other edits retained; no silent merge or resurrection. |
| Sign-out/team switch during snapshot | No cross-owner/server commit or leaked attachment binding. |
| Reviewed then modified draft | Old capture/send approval cannot authorize the changed attachment manifest; review of new revision required. |
| Cancellation before/after save | Draft remains unchanged before commit; after commit, current draft is authoritative and recoverable without duplication. |
| Acting-tool gate | Tainted/unapproved invocation blocked using existing rules; file content never inserted into model/tool response. |
| Self-message integration | Isolated local fixture delivers the reviewed snapshot to self with existing receipts, verified download and immutable hash. |
| Mac UI regression | Composer picker, removal, review/readback and send still operate on the shared writer. |

Start with `tests/test_message_approvals.py`, `test_message_approvals_server.py`, `test_messaging_files.py`, `test_messaging_api.py`, `test_messaging_integration.py`, `test_messaging_e2e.py`, and `test_brain_page_parity.py`. Inspect their fixtures before selecting the smallest meaningful subset. Add focused tests for the new source reader and async orchestration. Extend existing fixtures rather than writing another fake team server. Run affected frontend/page tests only where behavior or contracts change, then required repository CI checks on integrated bytes.

Do not deliver the study to Matt as an automated test. Use fake identities and harmless fixtures. A live self-message/staging check should use the normal explicit review/send path and the repository's test identity facilities; do not bypass approval to call the test successful.

## Scope, estimate and CI

Planning allowance: **4–8 active agent-hours**, not a promised duration. Allocate 0.5–1 hour to baseline/contracts, 1.5–3 hours to source streaming/snapshot/writer integration, 0.5–1 hour to tool/runtime/instruction wiring, and 1.5–3 hours to focused tests and review. This assumes existing source-access primitives can support the authorized ordinary-file case. Reforecast that package if a new access design is required; do not weaken the existing policy to meet the estimate.

Use one implementation branch and one PR. Run focused tests locally during development, push an integrated checkpoint for CI, and repeat affected checks after repairs. No new Team Server deployment should be required. The Windows plan remains separate: isolate the local source-opening boundary so its eventual Windows implementation can be supplied, but do not expand this fix into a Windows port.

## Completion and workflow reporting

Report phase starts/finishes and meaningful findings to the workflow record, including blocked decisions and test failures. Keep it current during long test runs. Complete the workflow only after code, tool registration, instructions and integration tests are finished and the handoff records validation and limitations.

Return the worktree/branch, commit and PR if authorized by the repository flow; changed files; exact tests and results; any remaining gaps; and instructions for JARVIS to use the new tool. The live brain may need its normal restart/reload to discover the changed tool catalog. Identify that requirement; do not silently restart an active conversation or production process. The user can then review and send the actual study through the normal message flow.
