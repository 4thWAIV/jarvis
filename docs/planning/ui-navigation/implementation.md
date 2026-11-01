# UI navigation and white pipeline — Diagnostics execution plan

Status: implementation requested; local human validation required before any merge. Prepared 1 November 2026 from design/source baseline `6a37860`. Reconcile with the current base before editing. This is a background build, not another research or mockup task.

## Ownership and delivery boundary

Diagnostics owns the workflow, integration, working local preview and completion report. Use one isolated integration worktree and branch, and parallel workers where file ownership permits. Preserve the live JARVIS instance and unrelated work. Complete implementation, tests, review and a running local candidate; leave it available for Tim to validate. Do not merge, deploy production, or replace/restart the live instance. Publishing a branch or PR is not necessary to obtain local validation; avoid automatic deployment side effects.

The approved scope is navigation and white pipeline attention in the existing React application. The background map and complete orb-to-part animation are core functionality. A standalone HTML animation, an outline-only overlay, partial coverage of three example targets, or a second brain-only navigation implementation is not completion.

The design reference is `docs/design/ui-map/design.html`, specifically its latest persistent-pipeline revision. Older free-routing and outline-only drafts are superseded. The HTML embeds an illustrative preview using the actual orb renderer; it is a reference, not code to paste into the application. Its bundled script is not an application architecture.

## Handoff bundle and worktree setup

1. Read this plan, `diagnostics-prompt.txt`, the complete design HTML, `CLAUDE.md`, applicable `AGENTS.md` files, and any repository SOLID/DRY skills. Honor current repository rules.
2. The plan and HTML can be untracked in the source checkout. Read and copy the exact bundle from `/Users/timothystockstill/code/macos/4thWAIV/jarvis` into the integration worktree before relying on relative links. Copy only this plan directory and `docs/design/ui-map`; do not sweep up other untracked plans or `docs/vector-search-models.md`. Record SHA-256 digests and the actual base commit in the execution record.
3. Inspect git status, local branches, existing workflows and worktrees for a duplicate implementation. Adopt existing task work if it exists. Otherwise create a dedicated `feature/ui-navigation-pipeline` integration branch/worktree from the current appropriate base. Record the location. Do not reset the source checkout or commit unrelated files.
4. Adopt the workflow ID in `dispatch.json` if present; otherwise create a native JARVIS workflow named `UI navigation and white pipeline`. Attach Diagnostics and the actual implementation workers. Publish phase starts, task progress, failures, blockers and completion at meaningful checkpoints and at least every few minutes while actively working.
5. Use a separate test data directory and unused ports. The application must never point an experimental worker at the live database, provider auth homes, user messages or job state. Build with the repository's Python/Node toolchain and package locks.
6. Preserve the existing HTTPS development requirements. Vite currently proxies to `https://localhost:8340`; an isolated backend on another port needs a scoped, configurable proxy target. Do not silently send test requests to production localhost:8340. Record the candidate URL, TLS setup, PID, logs and shutdown command. Keep secrets outside the repository, in session memory or a restrictive external temporary file if a child process needs them. Reuse credentials for the session without logging, copying them into the bundle, or repeatedly prompting the user.
7. For workflow reporting, use the live workflow reporter separately from the isolated application. Use a scoped `workflows` access key and explicit live reporting URL/certificate. Never hand a tool-token credential to workers. Revoke temporary reporting credentials when the workflow is finished. A reporter key must not become an application fixture or be stored in this plan.
8. Keep a resumable `execution.md` with workflow ID, base/branch/worktrees, file ownership, completed tasks, test evidence, current blockers, next action and preview lifecycle. Store no credentials there.

## Required result and scope

- The existing left fixed column, right six-tab panel, centre tabs, nested navigation, settings/menu, objects and meaningful parts are represented in a discoverable map tied to actual components.
- Human controls and brain tools invoke one registry of navigation commands. Selecting a main tab is separate from keyboard focus. The fixed left column is not redesigned into a tabbed panel; selecting/revealing existing left objects is supported, and the abstraction permits future registered left views.
- A subdued background pipeline remains visible before and after animation. The main orb feeds the top main pipeline; fixed rails serve left, main and right regions. Objects have visible branch junctions and entry sockets. Meaningful parts have registered internal branches.
- White source emission, pulse travel along that network, arrival rings, target contour, hold and automatic reset are implemented as one lifecycle. No arbitrary shortest paths across content, floating endpoint-only circles or replacement of transmission colours.
- Existing transmission routing, agent/queue arrival semantics, readback, approvals, read receipts, microphone and human-activity reporting remain correct.
- Discovery, navigation and indication use stable entity/presentation IDs, validated arguments, per-page targeting, acknowledgements and cancellation. Brain requests do not broadcast navigation to every browser.
- New navigation requires registration and gets brain access from the same declaration. Enforcement includes static checks, registry checks and human/brain parity tests.
- Browser hosting, rebranding, Windows porting, project redesign, arbitrary third-party website control and OS automation are outside this task. Whole-canvas targeting is included; new internal 3D orb subparts are not fabricated without an adapter.

## Workflow phases and parallel execution

Use the following phase slugs. Do not label the work complete at the first working animation.

| Phase | Owner / dependencies | Exit evidence |
|---|---|---|
| `preflight` | Diagnostics integrator | Worktree, isolated ports/data, source/design digests, baseline checks, inventory |
| `shared-contract` | Integrator, with worker input | Shared typed schema, command/target/pipeline contracts and a runnable vertical slice |
| `navigation` | Navigation worker after contract | Shared route state and human/brain handler parity |
| `pipeline-animation` | Graphics worker after contract | Persistent graph, source emission, directed pulses, arrival, reset and arbitration |
| `brain-transport` | Backend worker after contract | Scoped discovery, page binding, validation, acknowledgements and expiry |
| `component-coverage` | Integrator splits files among workers | All current inventory families registered, including nested controls |
| `integration-review` | Integrator plus independent reviewer | Combined checks, cross-feature regression evidence and fixes |
| `local-validation-ready` | Integrator | Running isolated actual app, recorded demo and repeatable validation steps |
| `human-validation` | Diagnostics waits for Tim | Tim's validation result; no merge before it |

Once the shared contract is stable, navigation, graphics and backend transport can run in parallel. A coverage worker can instrument components as their shared APIs land. Use no more simultaneous workers than the environment supports. Assign `CentreArea.tsx`, `RightPanel.tsx`, `server.py`, `jarvis.ts`, `orb.ts` and `style.css` explicitly to avoid competing edits. The integrator owns shared wiring; workers deliver tested commits into the integration branch. Do not duplicate effect schedulers, stores, schemas or navigation functions to avoid coordination.

## Phase: preflight and inventory

1. Reread `App.tsx`, `CentreArea.tsx`, `RightPanel.tsx`, `Tabs.tsx`, all components linked in the design map, `transferEndpoints.ts`, `RayLane.tsx`, `rayGeometry.ts`, `transfers.ts`, `orb.ts`, `jarvis.ts`, `ws.ts`, `humanActivity.ts`, `server.py`, `jarvis_mcp.py` and tool authorization categories.
2. Compare existing work since the design baseline. Reuse any already-landed command or UI-control infrastructure instead of reimplementing it. Report actual conflicts or changed semantics before expanding scope.
3. Produce `coverage.md`: actual source/control, stable command ID, target and part IDs, current handler/state, availability, side effects, reveal strategy, pipeline socket/edges, tests and completion state. Include controls that are intentionally non-navigation and explain their classification.
4. Run baseline frontend build and relevant existing tests. Record pre-existing failures separately from introduced failures. Do not conceal failures or replace real checks with a static HTML demonstration.
5. Implement a narrow vertical test through the actual app: right Network -> main node detail -> CPU part -> white signal from orb -> acknowledgement. This validates the contract before broad migration; it is not the final scope.

## Phase: shared contract

1. Use one versioned wire schema packaged with the application. Generate or validate TypeScript and Python/tool definitions from it. Unknown protocol versions, fields, command IDs, target kinds/parts and arguments fail clearly.
2. Define region/view IDs, entity keys, presentation IDs, part keys, availability reasons, command argument schemas, allowed callers, declared side effects, route-state revision and layout revision. Distinguish an entity appearing on the right from the same entity appearing in the main view.
3. Define registration interfaces for navigation handlers, mounted target adapters, lazy discovery/reveal adapters, pipeline junctions/edges and object sockets. The design HTML includes initial TypeScript interface shapes; implement coherent checked versions rather than a parallel prose-only interface.
4. Each target has a parent, safe label, reveal command, visibility/occlusion status, bounds adapter and socket/branch IDs. Object identity comes from records, never array positions or translated text. Unavailable targets remain discoverable with a reason.
5. Pipeline geometry is a function of registered layout constraints. Each edge connects named junctions and has a region and valid measured polyline. Source-to-target resolution traverses this graph only. Reject disconnected sockets, duplicate presentation identities and branches crossing reserved content.
6. Define request ID, client ID, connection epoch, caller/turn binding, expected navigation revision, deadline and operation. Navigation and indication arguments are schema-checked. Indication success requires actual arrival or explicitly reported reduced-motion static arrival.
7. Define ordered journey steps for optional tab-then-detail demonstrations. Every leg starts at the main orb; navigation/readiness completes before a leg targets its object. Human intervention or stale revision cancels remaining steps.
8. Keep navigation, attention and domain mutations separate. The generic UI tool cannot evaluate code, click selectors, send/delete messages, change provider configuration or manufacture approval. Reveals with existing side effects must declare and report them.

## Phase: shared navigation and full component coverage

1. Replace independent `selected` tab state in RightPanel and CentreArea with a shared navigation model and dispatcher. Preserve current initial tabs, hidden legacy Main, dynamically opened detail slots, and kept Conversation/Folders mount/scroll behavior. Remove competing effect-based tab selection races.
2. Make `TabRow` and other navigation controls consume registered command bindings. Human pointer, keyboard, links and brain requests call the same handler, including availability checks and side effects.
3. Adapt existing `openWorkflow`, `openThread`, `openDiagnostics`, `openSubsystem`, `openNetwork`, `openQueue`, `showFolders` and `selectedFolder` into the shared orchestration. Do not leave old direct setters as an alternative route that bypasses registration.
4. Cover right Folders, Agents, Queue, Workflows, Network and Diagnostics. Register search, filters, paging/cycling, show-finished states, row reveal, open/close, related links and back behavior. Keep recipient selection distinct from navigation.
5. Cover main Conversation, Folders, legacy Main, Workflow/Subagent, AgentThread, voice Diagnostics, subsystem, Team Server/node and Queue item. Dynamic tab selection needs the intended entity; return unavailable rather than opening an empty or wrong detail.
6. Cover workflow Overview, Phases, Tasks, Transcript, Progress, Waits, Decisions, Blockers and Events; transcript paging and event filters. Register each meaningful task/phase/decision with stable keys.
7. Cover folder registered/discovered lists, search/role/availability filters, ignored/removed visibility, directory breadcrumbs, file preview, paging and form open/cancel states. Use authorized folder ID, relative path and location revision. Preserve existing reader containment and credential exclusions.
8. Cover conversation records, expanded text, agent links, older history, captures and source parts. Give records without stable keys a durable identity through their existing data owner; do not use display text as identity.
9. Cover queue Waiting, Active/Outbox, messages, attachment entries, receipts, ownership indicators, reply editor, next/back/related/close. `openItem` currently activates queued work; body visibility can mark read; leaving can save a draft. Preserve those semantics and report them. Highlighting a row alone must not activate/read it.
10. Cover the left orb/logo, status/hush target, microphone controls/health, Team Server readiness/server/database/write metrics, and each provider account's remaining/pace/reset parts. Display-only elements are targetable without fabricated actions.
11. Cover menu open/close, Settings open/close and Brains, Voice, You, Team and System sections, including existing nested navigation. Register sign-in reminder and footer targets. Keep save, restart, membership changes and sign-in actions on their existing authorization paths.
12. Reveal a hidden target by registered navigation and bounded data loading. Wait for mount/layout readiness, resolve the exact presentation, scroll/expand/page as needed, then measure. No fixed sleeps, synthetic clicks, first-match selectors or unexpected fallback to another entity.
13. Only move keyboard focus when explicitly requested and valid. Showing a view, selecting a tab and visual attention are separate. Programmatic activity must not count as human online activity or approval.

## Phase: persistent pipeline and animated attention

1. Render a subdued neutral-white map beneath active signals but legible on the current dark UI: orb source up to top horizontal backbone; regional rails in reserved gutters; object branches and sockets. Reuse the current upper/outer transmission lane geometry where it fits. Preserve existing content placement and compact tiles.
2. Reserve layout corridor space rather than putting rails through text, buttons or charts. Object components register clear entry sockets and local margins. Fine internal branches appear when addressed or selected, while the main map stays visible. Unmounted objects have no floating branch.
3. Traverse only registered graph edges from main orb to region to object to part. Resize/scroll updates geometry without changing topology. No free-space shortest-path fallback. Missing/occluded/disconnected graph paths return blocked or unavailable after permitted reveal attempts.
4. Extract shared lifecycle/rendering primitives from RayLane and transferEndpoints. Maintain one scheduler and geometry invalidation mechanism. Retain existing transmission colours, tab fallback semantics, agent receipts and incoming/outgoing motion.
5. Add a distinct attention event channel. Do not synthesize a message Transfer or call its business arrival callbacks. Attention arrival resolves only the UI-control result.
6. Use the existing orb transmission visual behavior in white. The current orb has one transmission overlay; implement source-effect ownership/arbitration so attention cannot overwrite or terminate a real transmission. Real transfers retain precedence; attention queues for a bounded time or returns busy/expired. Cleanup releases only its own effect token.
7. Shared default sequence: 220 ms source emission; first pulse travels for 1,400 ms; two further pulses follow at 170 ms offsets; arrival rings start at 1,620 ms; contour appears on arrival; hold to 4,400 ms; fade/reset by 5,000 ms. Make timing constants shared by rendering and acknowledgement logic. Permit a bounded longer explanatory hold without increasing travel duration.
8. Maintain a faint whole route plus bright advancing pulses so direction is evident. At the exact destination, rings resolve into outline/ellipse/brackets. Keep the line connected during hold. Final fade clears active effects and restores the underlying orb state; the background pipeline remains.
9. Use a dark under-stroke for contrast on light surfaces. Effects are pointer-transparent, do not cover target text and have a screen-reader relationship announcement. Reduced motion shows the full static source/network/target connection with no spin, travel or expanding rings; it is not an isolated highlight.
10. Track dirty geometry while active. Preserve progress on the registered edge. If a reroute would jump across content or the target disappears, cancel safely and report the actual outcome. Do not finish at a recycled row's coordinates.
11. Handle modals in their visible layer with registered branches and shared coordinate transforms. If the source is obscured and cannot be revealed legitimately, report blocked. Do not create a second orb. Canvas parts require projection and occlusion adapters; whole-canvas targeting is the initial fallback only when explicitly requested.
12. Support explicit clear, turn interruption, timeout, human navigation and disconnection. All timers/listeners/frames must be released. Respect existing Escape semantics and stop-speaking controls.

## Phase: brain transport and tools

1. Implement `ui_map`, `ui_navigate`, `ui_indicate` and `ui_clear` through the current provider-independent tool dispatch and MCP schemas. Keep payload validation in the shared schema boundary, not duplicated per provider.
2. Bind each UI client to its authorized connection and originating user turn. Add client/epoch/capability handshake and separate navigation/layout revisions. Only the selected authorized page executes a command. A background request without a bound page returns discoverable eligible clients; do not guess or broadcast.
3. Return a bounded map with static routes, current views, dynamic target pages and command availability. Do not serialize arbitrary DOM, secret field values, inaccessible data or full conversation history into discovery.
4. Serialize navigation transactions per page. Deduplicate request IDs, reject stale revision/epoch and expired requests, and make timeouts explicit. Reconnection does not replay a timed-out navigation or animate old cues.
5. Stream lifecycle events for resolve/reveal/emit/travel/arrival/hold/fade/cancel. Complete indication only at arrival or the explicit reduced-motion outcome. Distinguish dispatch accepted from browser-applied/arrived; do not report success on enqueue.
6. Human input supersedes stale asynchronous navigation. Check navigation revision before each reveal step. If some navigation already happened, report those effects; do not overwrite the person's new view by trying to restore the old one.
7. Integrate with existing origin/tool authorization and acting-tool policy. Make the caller/turn source explicit. A UI request never substitutes for human approval of a domain action.
8. Cover no client, multiple clients, hidden client, unsupported protocol, expired tool request, stale entity, failed load and disconnected return channel. No silent wrong-target fallback.

## Phase: prevention of future coverage gaps

1. Generate the navigation manifest and brain discovery from the same feature registrations used by UI controls. No second handwritten route inventory in Python.
2. Restrict route-state setters to their owning dispatcher modules with a TypeScript-aware rule. Require registered navigation bindings or a reviewed non-navigation classification for interactive controls, including keyboard handlers and conditional controls. Do not rely on text grep alone.
3. Check command schema/handler coverage, valid target parents, stable object keys, pipeline connectivity and part/socket declarations. Add a negative fixture showing CI rejection of an unregistered navigation control and a disconnected target.
4. Exercise actual DOM controls as well as the manifest. For every navigation family, run the human binding and brain command against equivalent fixtures and compare resulting navigation state and declared side effects.
5. Document the extension contract: adding a view requires ID, availability, registered navigation, mounted targets, socket/branch definitions and tests. Adding a meaningful subpart requires its key, bounds adapter and connection to the parent's graph.

## Acceptance matrix and test evidence

Use the repository's existing Python/JS/browser harnesses. Extend them instead of installing a second framework without need. Inspect existing test names and fixtures at the implementation base; use the project's documented test commands. At minimum run the frontend typecheck/build, focused unit and browser checks, affected backend tool/WebSocket tests and the relevant transmission/navigation regressions. Run the wider repository-required gates on the integrated candidate. Record exact commands, outcomes and remaining failures.

| Area | Required evidence |
|---|---|
| Fixed map | Backbone, region rails and visible object sockets exist when idle, through all three demos and after clear/reset |
| Route correctness | Every animated segment is a registered graph edge or registered internal leaf; all targets share the intended source/backbone; no line crosses protected content |
| Full animation | Frame/timing evidence shows source emission, moving white pulses, arrival before contour, hold, automatic reset and retained background |
| Actual application | Brain tool request reaches the real app and receives actual client arrival acknowledgement; standalone HTML does not count |
| Main/right/left coverage | All mapped view families, all nine workflow subtabs and representative parts in each region work |
| Hidden targets | Kept hidden views, filters, pagination, late mount, scroll, deleted entities and recycled rows behave correctly |
| Side effects | Row highlight leaves queue placement/read status unchanged; deliberate open follows existing semantics; no fabricated receipt/transmission/approval |
| Concurrent effects | Real outgoing/incoming transmissions retain colours and receipts; attention ownership cannot stop them; source queue/expiry is visible |
| Human precedence | Human navigation during reveal cancels stale steps; no stolen text focus; programmatic commands do not alter last-human-online |
| Multiple pages | Only the turn's authorized client changes; forged or stale IDs/epochs and unauthenticated requests are refused |
| Recovery | Disconnect, duplicate request, interruption, missing branch, resize and modal obstruction return truthful outcomes and leak no timers |
| Accessibility | Reduced motion preserves static source-to-part connection; keyboard navigation, screen reader labels and white contrast work |
| Future enforcement | Unregistered navigation and disconnected-target fixtures fail; adding a registered fixture view appears in brain discovery without a second edit |

The final visual evidence must include a recording of the actual local app showing left database value, right node CPU value and main workflow task. Include one hidden-target reveal and one simultaneous or queued real-transmission scenario. Do not claim production-provider execution from fixture-only tests. Use deterministic fixtures for regression and an authorized isolated provider path for the live brain-tool demonstration; report which was used.

## Local preview and human validation gate

1. Build and launch the integrated application from the integration worktree on isolated ports/data. Use realistic fixture records for safe navigation coverage; real external messages or jobs are not required to show the effect. Provide a brain-driven smoke journey as well as any developer replay controls.
2. Keep the preview process running independently of the worker's terminating shell, using the project's supported supervisor or a recorded detached process. Do not leave the preview dependent on a foreground tool call that ends when the agent replies. Record PID, port, base commit, logs and stop/restart instructions outside secret-bearing files.
3. Test readiness and both HTTP and WebSocket paths from the URL Tim will open. Ensure the candidate frontend talks to the candidate backend. The live JARVIS stays on its existing port and configuration.
4. Open the candidate with the macOS `open` command. Do not use a browser extension to present it. The URL must show the working application, not only this design page.
5. Produce `validation.md` with exact URL/worktree/branch/commit; tested commands and results; screenshots/video locations; local setup needed, if any; and a concise walkthrough: run three region journeys, navigate a hidden target, observe map persistence/reset, inspect nested navigation, and check ordinary transmissions still work.
6. Set `local-validation-ready` complete and open a `human-validation` wait/decision with the URL and concise question. Keep the workflow waiting for Tim, not succeeded/merged. Diagnostics reports the result through its normal JARVIS completion channel and its own transcript. Retain the evidence and preview if the planning conversation is no longer active.
7. On feedback, repair the same candidate and rerun affected checks. Passing automated checks is not Tim's visual acceptance. No merge or production deployment is authorized by this handoff. After explicit acceptance, follow whatever release instruction Tim gives then.

## CI/CD and reporting

Keep intermediate checks local and batch integration fixes. Do not repeatedly push worker branches or trigger Railway deployments. Inspect repository workflows/autodeploy bindings before any publication. Existing required checks remain required; minimizing runs does not mean skipping them. This task's first delivery is local validation, so remote CI/PR publication can wait for the appropriate later instruction.

Diagnostics may commit the task's own work in its isolated branch for review and recovery. It must not merge or alter unrelated work. Report material blockers with observed facts and a recommended resolution; continue independent authorized work where possible, but do not silently change the design.

Preliminary budget remains 82–132 active agent-assisted hours, not a delivery promise. Reforecast from the reconciled code if scope or existing infrastructure materially changes. Update progress from actual completed packages rather than percentage guesses. A working pipeline in three examples is a checkpoint; full mapped coverage, enforcement and the local validation candidate complete the build portion.
