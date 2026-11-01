# Workspace visual polish proposal

Status: researched proposal; no visual implementation approved or delivered.
Matt requested a headless review of the **entire JARVIS interface**, preserving
Tim's layout, composition and interaction model. The conversation screenshot is
an example of the problem, not the scope boundary.

## Recommendation

Use the existing React and CSS, with semantic color tokens. Add no component
library for this pass. The main workspace has many individually specified colors;
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
- Orb/microphone/voice controls: preserve animation/state hues and transfer colors;
  match canvas background to theme ground, without recreating the renderer.
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

## Theme candidates for review

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

These are proposals, not selected product colors. Shared success/warning/error
colors and recognizable blue JARVIS orb remain in both themes. Actual composited
colors must be checked; solid palette contrast is not whole-app accessibility.

## Bounded delivery sequence

1. Capture baseline screenshots and bounding boxes. Introduce semantic color
   tokens without changing geometry or type metrics.
2. Apply consistent surface and control states across every surface above.
3. Preview both palettes on full workspace, Conversation, Queue review, Settings
   and Diagnostics for Matt's judgment before completing token replacement.
4. If accepted, add a native theme select inside existing Settings/System. Persist
   locally in the browser, apply before React mounts, tolerate unknown values and
   storage errors. No new backend route or team synchronization.
5. Bridge the selected ground color into the orb canvas without changing its
   state mapping, renderer lifetime, animation loop or reduced-motion behavior.

## Library research

[Radix Primitives](https://www.radix-ui.com/primitives/docs/overview/introduction)
and [React Aria](https://react-aria.adobe.com/styling) are unstyled: they may help a
specific deficient interaction later, but won't supply this visual polish.
[shadcn/ui](https://ui.shadcn.com/docs/theming) demonstrates useful semantic CSS
variables; adopting its tooling/components would create a larger migration and
make preserving geometry harder. Retain current controls for this pass.

## Acceptance

Compare bounding boxes for columns, header, composer/speaking boxes, agent rows,
footer and overlays. Run existing layout/overflow tests; inspect both themes at
normal/narrow desktop widths and 200% zoom. Exercise keyboard focus, contrast,
loading/empty/error/disconnected states and preference reload. Theme changes must
not add network requests, renderer recreation or animation loops.

Use W3C targets for [normal text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html),
[meaningful control/state contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html)
and [visible focus](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html).
Audit rendered alpha layers rather than relying on raw hex values.
