# UI capability parity — Diagnostics execution plan

Prepared 1 November 2026. Status: prepared for execution, not implemented by this planning task. Source baseline: main `2c10f72`; navigation work is on `feature/ui-navigation-pipeline`. Reconcile both before editing.

## Delivery

Extend the existing UI navigation workflow with callable equivalents for every application control, structured data parity, enforced registration and untrusted-result handling. Deliver a running isolated local candidate for Tim to validate before merge. This plan covers data reads, domain actions and local UI state, not only navigation or animation.

Use the existing native workflow `wf-dc741957b946`, named UI navigation and white pipeline. Diagnostics owns integration. Read `implementation.md`, `localization.md`, `dispatch.json` and the integration worktree's `execution.md` and `coverage.md`. The current integration worktree is `.claude/worktrees/ui-navigation-pipeline`; do not edit its workers' files from the source checkout. Add dependent work packages to this workflow, not a duplicate build. Record this plan's digest when copying it into that worktree.

## Source requirements and findings

The standing memory file is `data/jarvis/memory/every-ui-element-needs-an-equivalent-call-available-to-jarvi.md`. Its exact requirement includes: “nothing that goes into the UI can be done without a call that is also made available to JARVIS, returning the same information as JSON that the UI shows.”

Current repository `CLAUDE.md` says: “every page feature has a brain tool that calls the same service or function as its route”. It also says: “Left out on purpose: settings that hold secrets (API keys and the Fish key test), `/api/restart` on its own (`update_jarvis` restarts, through the same `_restart_server`, once an update is in), and the sign-in and connect flows that need a browser approval”.

The new parity scope must inventory those controls too. A browser approval or secret-entry step remains a human step with a callable initiation/status equivalent; it is not an excuse to omit the control from discovery. Do not expose stored credentials, impersonate consent, bypass message readback, or weaken existing authorization. If exact execution would require changing an existing approval boundary, record that specific conflict for Tim rather than silently exempting the control or changing the boundary.

Observed implementation:

- PR brain-page-parity merged as `61c9761`. Team, network, people messages, member management, folder candidates, diagnostics and workflow operations exist. Do not rebuild them.
- `server.py:tool_team_network` reads the same snapshot as the page but formats it into prose. Other tools likewise omit structured fields. Sharing a service is not sufficient evidence of JSON parity.
- `jarvis_mcp.py:_forward` retains only `ok`, `text` and `image`; `_result` produces text/image content. A dictionary added in one handler would currently be lost at this boundary.
- `message_approvals.py:MessagingTools._recipient` interpolates recipient names into ambiguity/no-match replies. `draft_message` returns those replies directly, while its tool is categorized as taint-exempt.
- `tests/test_brain_page_parity.py` checks an explicit tool list. It does not discover newly added UI controls. It passed at the source baseline.
- Defined MCP tools and `claude_provider.ALLOWED_TOOLS` differ. The earlier investigation found MCP calls were not blocked by that flag; reconcile the declarations rather than relying on that behavior.
- The active navigation implementation already has `jarvis/ui_contract/protocol.json`, family declarations, `scripts/generate_ui_contract.py`, generated `frontend/src/uiContract.ts`, `uiRegistry.ts`, `uiNavigation.ts`, `uiClient.ts` and `jarvis/ui_control.py`. Reuse these rather than creating another command registry or page-binding protocol.

## Worktree and ownership

1. Read applicable repository instructions and inspect current workflow tasks, worktrees and dirty files. Identify work already implemented since this baseline, including localization and message attachments.
2. Diagnostics chooses a stable integration commit and creates a child worktree/branch for this package from that commit. Record base, path, assigned files and dependencies in `execution.md`. Integrate back into the existing navigation branch; never reset an active worker.
3. The integrator owns contract generation, shared result transport, `server.py` wiring, MCP/provider registration, CI configuration and final integration. Feature workers own disjoint frontend families and corresponding service adapters. The wrapper fix can be a separate commit but coordinate ownership of messaging and server files.
4. Preserve isolated application ports/data and the live JARVIS instance. Use the existing workflow reporter. Keep credential reuse outside tracked files and logs; reuse the parent plan's procedure.
5. Create an independent review task after integration. Local validation precedes any release. Minimize remote runs by merging/test-fixing locally; do not push each worker iteration or trigger deployments.

## Work package: inventory and contract

1. Expand the existing coverage record into a machine-readable, checked inventory. Each entry identifies source component/control, stable control ID, capability ID, entity scope, input/output schema, service or page-state owner, UI binding, brain binding, authorization, effects, untrusted fields, availability and tests. Render `coverage.md` from this inventory rather than maintaining a second list.
2. Inspect every shipped entry point, mounted view, menu, modal, keyboard shortcut and conditional control. Include controls hidden by role, sign-in, empty data, errors, feature state and narrow layout. Include accessible non-button interactions, drag/drop, file selection, inline editing and custom canvas interactions. Display-only information needs a read capability and target, not a fictitious action.
3. Inventory existing served secondary pages too. Do not exclude a shipped page merely because it is not promoted in the current UI. Mark an actually unreachable/removed control with evidence.
4. Cover shell/navigation; people/network/team; queue, approvals, attachments and drafts; agents/sessions/runs; workflows; folders/files; conversation/captures; diagnostics/voice; settings/provider accounts/preferences/language; update/restart/connect flows. Use stable IDs independent of English/Spanish labels.
5. Extend existing family declarations with read/action/state capability references and schemas. Keep navigation, indication and business operations distinct. A generic navigation command must not become a way to send messages or change membership.
6. Define one versioned result envelope with capability, status, data, error, effects and revision/freshness metadata. `data` is the same typed view model consumed by the UI, preserving nulls, types, IDs, units, pagination, status and permissions. Error codes and parameter data are shared; human wording is a localized presentation.
7. Define outcomes for completed, rejected, unavailable, cancelled, pending and human-step-required. Pending is not success. Browser-bound state uses the existing client/epoch/revision binding; backend reads remain usable when no page is open.
8. For computed UI values, move the computation into the shared data owner or expose the bound page's derived state with input revision. Do not recreate a second calculation in Python. Identify joins explicitly, such as team status plus member roster; compare component data projections rather than pretending every widget has one HTTP endpoint.
9. Freeze shared schemas and implement a vertical slice: people discovery, exact network node data and one authorized reversible action. Prove equality through both transports before migrating the remaining families.

## Work package: shared results and adapters

1. Extract typed service results from prose-producing handlers where necessary. Existing API routes and tools call the same service/query/command owner. Preserve current authorization and request validation. Keep compatibility adapters for existing tool names; avoid parallel handlers with copied business logic.
2. Carry structured JSON through the actual internal tool endpoint, result limits, MCP forwarding and both brain-provider paths. Inspect current client capabilities before using optional protocol fields. Publish schemas with tools where supported; preserve image content and error signaling.
3. Ensure the result available to the model contains valid complete JSON. If a compatibility text representation is required, serialize the same result, never rebuild selected fields. Optional spoken summaries are derived from that data, not its replacement. Do not duplicate full large payloads unnecessarily.
4. Replace blind string truncation of structured output with bounded pagination and explicit continuation. Never truncate JSON mid-object or silently drop fields. File content uses existing authorized readers and bounded pages; binary data returns the same authorized metadata/reference the UI uses.
5. Reads return the same permitted field set as the UI for the same identity and snapshot. Secret inputs are never returned; configured/not-configured and masked status match the UI. No credentials in discovery, logs, evidence or fixtures.
6. Mutations return action outcome plus the resulting view model/revision or a documented asynchronous operation reference. Deduplicate retries for non-idempotent actions. A timeout after dispatch must distinguish unknown outcome from safe-to-retry refusal.
7. Implement equivalent local UI operations through the existing bound page dispatcher, including filters, sorting, selection, expansion, pagination and form draft state. Return the selected state as JSON. Do not use DOM selectors or synthetic clicks as an API.
8. Reconcile legacy named tools, generated capabilities, provider declarations, handlers and authorization/trust classifications. Derive lists from shared metadata where appropriate and assert consistency. Confirm actual discovery and invocation through each configured brain provider, not just imports.
9. Update brain instructions to discover and use these capabilities. Prefer bounded family discovery to sending every control description on every turn. Measure tool-catalog size before/after; do not reintroduce a second prose inventory.

## Work package: all feature families

For each family, migrate data read, command execution, UI binding, tool binding, availability, result schema and tests as one unit. A registered identifier with no working handler is unfinished.

| Family | Specific completion cases |
|---|---|
| People, network, team | Distinguish roster, message recipients and online nodes; include self; inactive/approved/suspended roles; node CPU/memory/usage/freshness; membership operations enforce server roles. |
| Queue and messages | Draft/edit/attach/review/send; read receipts; queued/active/inactive state; reply/related links; cancellation/retry; attachment metadata and authorized access. Preserve the existing approval of the exact outgoing revision. |
| Agents and workflows | Listing, selection, activation/deactivation, rename, session detail, steering, run lifecycle, decisions and nested workflow views. Preserve existing human authorization and task state transitions. |
| Folders and files | Registered/discovered/ignored/removed; path/role filters; contents/breadcrumbs/preview; registration and changes; existing containment and sensitive-file policies. |
| Conversation and diagnostics | History paging, expand/collapse, capture controls, subsystem detail, voice health, diagnostics filters and available actions. |
| Settings and shell | Left/right/main/nested navigation, menu, language, preferences, mic and voice controls, provider state, update and restart. Browser permission/sign-in flows return initiation/progress/human-step status without claiming completion. |

Match current English/Spanish behavior: data identifiers and machine errors remain stable; labels and descriptions follow the locale. A locale change must not alter capability identity, authorization or returned domain data.

## Work package: untrusted output

1. Make no-match and ambiguous-recipient responses structured outcomes with recipient candidates in data. Keep trusted error code/summary separate from names, logins, echoed input and external refusal text.
2. Use the existing untrusted-content boundary for every model-visible rendering of those fields. JSON alone is not a trust boundary. Ensure the same fields cannot escape through an optional summary, error string, structured-result channel, attachment filename or log copied into the model.
3. Inspect the existing turn-taint mechanism and apply it when a result includes external data, including a failed acting tool. Authorization to create a draft does not make returned recipient names trusted. Do not remove the acting-tool gate or silently permit subsequent actions on a tainted turn.
4. Keep the wrapper/serialization logic shared. Preserve exact stored data; escape delimiter-breaking text in the model representation, not by rewriting names in storage. Document how structured data receives the same trust treatment as textual data.
5. Test no match, ambiguity, successful match, unavailable server, external refusal, malicious names/logins, quotes/newlines, wrapper terminators and multilingual text. Assert no draft/send on failed resolution and no follow-on unauthorized action after untrusted results.

## Work package: automatic enforcement

1. Use the existing TypeScript compiler tooling to find interactive JSX/native elements, custom control components, handlers and keyboard/gesture bindings. Extend the navigation worker's scanner; do not build a competing coverage checker. Resolve imports/wrappers rather than checking only names such as Button.
2. Require registered control bindings with declared capabilities. Restrict direct navigation setters and domain network mutations to their registered owners/adapters. Check event delegation, imperative listeners and dynamically created controls too. Explicitly classify presentation-only elements; blanket file/folder exclusions cannot satisfy coverage.
3. Add runtime browser auditing across route/state/role fixtures. Compare mounted interactive and accessible controls against declarations, including disabled controls with reasons. Exercise conditional branches and custom canvas hit regions. Static analysis and runtime inspection complement each other; neither proves unexecuted arbitrary code covered.
4. At startup/build validate uniqueness, schema completeness, registered handlers, brain exposure, trust/authorization classification, referenced targets and capability availability. A visible human-only capability must expose its initiation/status contract and reason, not disappear.
5. Differential tests call UI route/service and brain capability under the same fixture identity, revision and clock; compare JSON projections recursively. Do not discard keys to make tests pass. Mutations run on separately reset identical fixtures, then compare effects and resulting data, avoiding double sends.
6. Add negative fixtures proving failure for an unregistered button, keyboard shortcut, custom wrapper, missing brain handler, missing response field, wrong field type, stale generated contract and unwrapped external text. Assert precise diagnostics identify source and capability.
7. Add checks to the existing local/CI test commands. A developer adding a control must supply its binding, schemas and parity test before CI passes. Review narrow exceptions with reason and owner; never quietly mark missing work covered.

## Work package: integration and acceptance

1. Run focused tests for messaging wrappers and result transport; then family parity, generated-contract checks, static/runtime coverage, frontend build and the repository's required regression suite. Reuse `tests/test_brain_page_parity.py`, `tests/test_ui_contract.py` and existing browser harnesses.
2. Test ordinary/member/admin/signed-out states, self-messaging, missing recipient, disconnected page, stale revision, reconnect, pagination, empty results and backend error. Verify equivalent actions still require their existing approval.
3. Test actual model-facing tool transport in both supported provider paths: structured fields survive, errors remain distinguishable, untrusted content remains bounded, and a model can use returned IDs for a permitted next read.
4. Test Spanish and English, navigation pipelines and ordinary transmissions alongside parity changes. Programmatic navigation must not create human-presence activity or false delivery/read/approval events.
5. Produce a generated coverage report with uncovered controls listed explicitly, response-schema comparison evidence, wrapper regression evidence and a short local walkthrough. No completion claim while uncovered controls remain unexplained.
6. Keep the isolated candidate running. Demonstrate finding Matt and self from fixtures, selecting the corresponding network view, inspecting the same metrics by JSON, changing a reversible setting through UI and tool, and observing the same result. Demonstrate a new unregistered control making the check fail and a malicious recipient response remaining data.
7. Diagnostics reports URL, branch/commit, remaining human-step flows and evidence, then waits for Tim's local validation before merge. This planning handoff does not authorize production deployment or live destructive testing.

## Execution order and estimate

Use workflow task names `parity-inventory`, `parity-result-contract`, `parity-transports`, `parity-feature-adapters`, `parity-untrusted-results`, `parity-coverage-gates`, `parity-integration` and `parity-local-validation`.

Inventory and shared contract come first. Then transport, wrapper repair and coverage tooling can proceed concurrently with explicit file ownership. Feature adapter workers start after the vertical slice and divide by UI family; the integrator owns shared files. Integration and independent review follow all streams. Do not assign workers before resolving ownership with the active navigation/localization tasks.

Planning range, additional to already completed navigation work: inventory/contract 4–7 active agent hours; transport 5–9; feature migration 10–20; wrapper work 2–4; enforcement 6–12; integration/review/local evidence 5–9. Total 32–61 active agent hours. Parallel work may reduce elapsed execution to roughly 14–26 hours after a stable integration base, excluding waits for human validation and newly observed blockers. These are planning ranges, not measured durations. Diagnostics must replace them after inventory, credit existing implementation and avoid adding overlapping navigation/localization hours twice.

## Completion record

The implementation owner records each task's commit, test command/result and remaining gap in the workflow. A plan file, tool name, mocked data response or passing handpicked list alone is not completion. The deliverable is the working shared capability path, complete structured results, enforcement that rejects missing controls, fixed trust handling, and the locally reviewable application.
