# Workspace visual polish

Status: implemented in this checkout; visual acceptance remains with the user.
The pass covers the **entire JARVIS interface**, preserving Tim's layout,
composition and interaction model. The conversation screenshot supplied for the
review was an example of the problem, not the scope boundary.

## Recommendation

The implementation uses the existing React and CSS, with semantic color tokens
and no new component library or dependencies. The main workspace had many individually specified colors;
the separate dashboard already demonstrates useful token roles in
`frontend/src/dashboard/theme/tokens.css`. Reuse its principles, not its stylesheet:
its font metrics and geometry differ from the main workspace.

Preserve column widths, panel positions, box dimensions, measured agent rows,
line heights, labels, wrapping, navigation, collapse behavior and all workflows.
The empty speaking box can become quieter; do not remove, shrink or move it.
No chat bubbles, new layout, replacement icon suite, font download or new motion.

## Whole-app scope

- Conversation/threads: legible primary text, quieter assistant color, readable
  notices instead of faint italics alone. Keep chronology and collapse behavior.
- Speaking/composer/captures/sources: consistent surfaces and edges, visible
  focus-within treatment, restrained inactive borders and existing semantic tints.
- Header/tabs/menu: consistent selected, hover, disabled and keyboard focus states.
- Orb/microphone/voice controls: preserve animation/state hues and transfer colors.
  The canvases were already transparent and inherit the themed ground; no renderer
  or animation changes were necessary.
- Brains/usage/agent rows: improve numeric and secondary-label contrast within the
  existing measured row geometry and provider marks.
- Folders/files: consistent field, row, chip, loading/empty/error treatment.
- Queue/messages/attachments/review: preserve owner/correspondent identity colors,
  delivery states, approval steps and composer; clarify metadata and failures.
- Workflows/decisions: retain phase/progress meaning and existing orb palettes.
- Network/team: consistent tile hierarchy, numerals and stale/unknown readings.
- Diagnostics: readable summaries and controls; preserve red/amber/green meaning.
- Settings/dialogs/overlays: unified fields/buttons, readable help, visible focus,
  consistent scrims, unchanged modal and navigation geometry.
- Footer/dashboard: compatible color roles without rewriting dashboard structure.

## Theme choices

| Role | Classic Refined | Graphite |
|---|---|---|
| Ground | `#080D14` | `#101114` |
| Surface | `#0E1622` | `#181A1F` |
| Raised | `#162234` | `#22252C` |
| Primary text | `#E4EDF7` | `#E6E8EC` |
| Secondary text | `#A7B5C6` | `#B2B7C2` |
| Muted readable text | `#8190A3` | `#929BA9` |
| Accent / assistant | `#73C8FA` | `#A8BFFF` |
| Functional edge | `#607892` | `#768293` |
| Decorative divider | `#34485F` | `#4E5663` |

Classic Refined is the default. Settings → System → Appearance switches to
Graphite or back without remounting the app. The preference is local to the
browser, applies before the first paint and follows changes in other tabs on the
same origin, including the run dashboard. Unknown saved values fall back to
Classic; unavailable storage still permits a choice for the current page.

Shared success/warning/error colors, owner/correspondent identity colors and the
recognizable blue JARVIS orb remain in both themes. Saturated owner and failure
badges use dark foregrounds; cancelled badges use light secondary text. Actual
composited colors matter; palette checks are not whole-app accessibility certification.

## Implementation boundaries

`frontend/src/theme.css` holds shared paint roles and bridges the dashboard's
existing roles. All original geometry and typography declarations are retained.
The Appearance select is appended after existing System controls so their
positions stay intact. Theme changes make no backend request. Orb/ray palettes,
renderer lifetime, animation loops and reduced-motion behavior are unchanged.

## Library research

[Radix Primitives](https://www.radix-ui.com/primitives/docs/overview/introduction)
and [React Aria](https://react-aria.adobe.com/styling) are unstyled: they may help a
specific deficient interaction later, but won't supply this visual polish.
[shadcn/ui](https://ui.shadcn.com/docs/theming) demonstrates useful semantic CSS
variables; adopting its tooling/components would create a larger migration and
make preserving geometry harder. Retain current controls for this pass.

## Verification and visual review

Baseline captures compare columns, header, composer/speaking boxes, agent rows,
footer and existing detail panels against both themes, including typography and
spacing metrics. The capture script accepts `--theme classic` or `--theme graphite`
and `--metrics` writes adjacent geometry JSON. Browser tests cover preference reload,
unknown/denied storage, dashboard tab updates, narrow widths, focus, draft and
panel preservation, canvas identity and stable connections. Existing browser
journeys cover layout/overflow, conversation, settings, folders, Queue/review,
workflow, Network and diagnostic states. Screenshots use fictional fixtures and
include empty and failed states; they are not live account evidence.

Human judgment on the finished appearance and OS/browser-specific rendering
remains a review step. This pass deliberately retains existing responsive layout
behavior, including tight space at extreme zoom, rather than redesigning it.

Use W3C targets for [normal text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html),
[meaningful control/state contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
and [visible focus](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html).
Audit rendered alpha layers rather than relying on raw hex values.
