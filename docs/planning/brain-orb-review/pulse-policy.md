# Shared pulse and motion policy

## Behaviour

All decorative luminance pulses use one shared application clock: an eight-second active sinusoidal cycle (0.125 Hz), or a twelve-second quiet/idle cycle (0.083 Hz), with amplitude at most ±4% of nominal intensity. No component starts an independent pulse train. State semantics are carried by shape, colour and steady detail distribution; a faster pulse never means a worse error. Red and error states use steady light, without blinking. No full-screen flash, alternating saturated-red flash, or abrupt on/off sparkle is used.

State lighting interpolates smoothly over approximately two seconds. A burst of incoming state changes is coalesced to the latest state before a new transition is started. The numerical reading may update immediately; it must not repeatedly disappear and redraw. Transmission movement is continuous with constant luminous intensity; event bursts do not trigger repeated screen brightness changes. Do not confuse movement speed with flash frequency.

An application-level Reduced motion / steady light setting honours the operating system preference and persists the user's choice. It freezes decorative orbital travel, shimmer, pulsing and travelling particles. Navigation, status updates and selection remain available through static state changes. A pause control remains available for prolonged animation. Production must apply these controls to CSS, WebGL, SVG and video effects alike.

## Current study

The brain is scaled to 0.64 instead of 0.82 (about 22% smaller). Its anatomical bounds center is at the orb's core. The core, brain, feeds and number window share the interpolated state colour. Its spatial highlighting ranks are fixed, so changing detail density fades the same structure instead of randomly sparkling.

Green: #278451, 92% target highlighted-detail coverage, target linear highlight luminance 0.035. White: 48%, 0.16. Yellow: 20%, 0.35. Red: 5.5%, 0.70, with a steady bright silhouette. Highlight colour is normalized by luminance; the red highlights mix toward hot pale rose so they can be perceptually brighter than white-state highlights. The state-coloured base remains visible. These are rendering targets before material compositing, not measurements of final screen luminance. Unhighlighted structure retains a state-coloured baseline. Contour lighting uses the surface normals relative to the viewer. Every shape is made from anatomy-derived filaments and particles.

The study implements the shared pulse helper, steady-red lighting, OS reduced-motion preference and a visible override. The surrounding production application is unchanged. No seizure-safety certification is claimed from these parameters alone.

## Production adoption

1. Promote the shared policy and clock to the application animation service; expose CSS timing and amplitude variables from the same settings.
2. Audit and replace independent brightness rhythms in frontend/src/orb.ts, frontend/src/style.css (ray-pulse and pulse-dot), frontend/src/conversation/conversation.css (playback-pulse), and frontend/src/dashboard/theme/primitives.css (flash-box and flash-glyph). Check remaining animation/keyframe definitions and video assets, not only animations named pulse.
3. Route transmission lifecycle events through one scheduler. Coalesce event bursts and avoid simultaneous independent luminance effects. Keep travel and destination information visible under reduced motion.
4. Make the persisted setting available before first render to prevent an initial flash or motion before preferences load. Apply operating-system preference changes live.
5. Record the complete screen at supported sizes and frame rates, including rapid events, multiple orbs, red states, loading, reconnect and state changes. Analyse composite output for general and red flashes, including overlapping effects. Test reduced motion, pause, keyboard operation and unchanged data availability.
6. Gate production adoption on the composite flash assessment and visual review. Low frequency alone is not a guarantee for every viewer; area, luminance contrast and saturated red also matter.

## Basis

WCAG 2.2, Success Criterion 2.3.1, requires content to remain below specified flashing thresholds: https://www.w3.org/TR/WCAG22/#three-flashes-or-below-threshold . This policy deliberately avoids using that threshold as a target. Guidance: https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html . Motion preference implementation: https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/Media_queries/Using_for_accessibility .
