# Brain orb review

Permanent review page: index.html. Update this page and its source in place for subsequent revisions. Commit each completed revision before starting the next; use Git history to inspect or restore an earlier rendering. Do not create another numbered or differently named review page.

Build from the repository root: python3 docs/planning/brain-orb-review/build.py

The source directory contains the study and a preserved copy of its existing orb renderer and living core, so rebuilding does not depend on ignored working notes. Three.js and esbuild come from the repository's frontend dependencies.

Current rendering: 900 connected surface paths traced over the model, with a core-to-surface branch for each path. The triangle mesh is used as a tracing source, not drawn as the brain. Path ranks are stable and distributed across the anatomical volume; state changes light whole paths. The former sinusoidal stripe mask is removed. The spherical window and shared pulse controls are retained.

Baseline commit: db0e1f7, before the anatomical-filament redraw. No production application code is changed.

Regenerate surface paths with `python3 docs/planning/brain-orb-review/source/trace-filaments.py`, then run the build. The tracing script fits a local surface-curvature operator to the authored normals, follows the lower-curvature tangent direction with continuity constraints, and traces only along connected mesh vertices. Catmull–Rom interpolation smooths the resulting paths for display. This is an anatomical visualization, not a reconstruction of biological neuronal connectivity.

Validation covers all four states, core alignment, reduced motion, smooth state transitions, three fixed anatomical views, portal facing and pulse controls. The previous source and rendering are recoverable through Git history.

Anatomy: human-brain by Yash_Dandavate, CC BY 4.0. https://sketchfab.com/3d-models/human-brain-e073c2590bc24daaa7323f4daa5b7784 . Source provenance is stored alongside the anatomy data.
