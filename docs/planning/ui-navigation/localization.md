# Shared language setting: Spanish interface

Implementation scope update, 1 November 2026. Integrate into the active UI navigation and white pipeline workflow and its local-validation candidate. The existing no-merge/local-human-validation gate remains in force.

## Outcome

Selecting Español changes the application-owned interface text to Latin American Spanish as well as the speech language. Selecting English changes it back. One setting controls both; do not introduce a second independently stored UI-language preference.

## Current source

`jarvis/language_setting.py` owns the saved language, active speech language and change transaction. `change()` switches the speech model before saving and activating the new language; refusal leaves the prior choice in force. Both the Settings route and the brain tool use this path. `frontend/src/LanguageControl.tsx` reads `/api/settings/language` but renders hard-coded English labels, as do other components. The current `transferEndpoints.ts` locates tabs through English `aria-label` values, which must not survive localization as navigation identities.

## Implementation

1. Reconcile the newly merged Spanish-language changes with the integration branch. Coordinate this as part of the current workflow, with explicit file ownership; do not run a competing edit of the same UI files in the source checkout.
2. Create typed English and Spanish message catalogs with identical keys and interpolation parameters, and one shared reactive locale store. Render from the active language reported by the backend. Use the existing persisted setting; do not duplicate persistence in localStorage or a new database.
3. Publish a language-state update from the common successful change path, so Settings, brain-driven changes and other connected pages behave alike. Include the active language in client initialization/reconnect state and refresh it after reconnection. Guard against stale fetch responses overwriting newer events. A failed switch must leave the UI in the prior active language.
4. Localize all application-owned text in the current React interface: left controls and health/usage labels; right and centre tabs; nested views; Settings and LanguageControl; buttons, menus, tooltips, accessibility labels, placeholders, empty/loading/error states, confirmation text, notifications and pipeline-attention descriptions. Inventory coverage rather than stopping at the six tabs.
5. Keep entity IDs, command IDs, target/part IDs, pipeline edge IDs, API enum values and stored records language-neutral. Replace English-label-based endpoint selectors with the registered semantic IDs. Labels in brain discovery may follow the active language; command identities cannot change. Switching language preserves the current view, entity selection, drafts and focus rather than remounting the whole application.
6. Use full translated messages with named interpolation and plural handling, not concatenated English fragments. Format dates, relative times, numbers and percentages with the shared locale. Set the document language to the active language. Use consistent Latin American Spanish terminology.
7. Separate application status/error codes and parameters from display text where backend English currently reaches the interface. Translate owned messages through catalog keys. Retain raw diagnostic detail as distinguishable original detail where needed; do not pretend arbitrary external errors or user/agent content are translated application copy.
8. Preserve names, file paths, source code, user-authored subjects/messages, conversation history and provider/product identifiers. Changing the UI language must not rewrite stored content or cause extra model calls to translate each render.
9. Recompute target geometry and pipeline branches after translated text reflows. Account for longer labels; do not truncate essential controls or route lines across text. Preserve the active cue's entity identity and cancel/relaunch safely if geometry invalidates its branch.
10. Add catalog-key/placeholder parity checks and a missing-translation development diagnostic. Extend navigation coverage checks to discourage new unlocalized application-owned labels. Review exceptions; do not use a brittle search-and-replace translation pass.

## Acceptance

- Switch English -> Español -> English through Settings and the brain's existing language tool. The complete visible interface updates after successful change without reload or loss of drafts/navigation state.
- Refresh, reconnect and restart restore the backend's active language. Failed model load/download/change does not falsely switch the interface. Verify cross-page synchronization and stale-response handling.
- Exercise every right tab, central detail family, nested workflow tab, Settings section and main status/error/empty state in both languages, including accessibility labels.
- Run all three white-pipeline demonstrations and existing agent/queue transmissions in both languages. Stable commands resolve the same objects and parts; translated labels never determine endpoint identity.
- Inspect Spanish layout at supported widths, text scaling, dynamic status values and plural counts. The actual local validation candidate must expose both language paths.
- Include localized screenshots and language-switch steps in the final validation report. Update workflow task coverage and estimate to include this added work; do not silently substitute partial localization or claim it already existed.

Diagnostics should integrate this update into the canonical execution plan in its worktree, attach a localization work package to the existing workflow, and report completion with the same local candidate. No merge or production deployment is authorized here.
