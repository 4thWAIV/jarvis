# Approved routing-strip implementation

The approved 16-pixel top strip is applied above the existing border. The host menus include the strip in their top offset. The 36-pixel normal border and 32-pixel targets, 48/44 coarse-pointer dimensions and shared 4-pixel routing clearance remain intact. Approval path and hash are in `approval.json`; the contract amendment is in `contract-amendment.md`.

This branch includes the independent Details lifecycle repair and the saved screenshot, vote and registered-reveal corrections. It also integrates current main `bdd9ab3c5a03e9bd67031af99ceba19a9d9decee`, which contains merged PR144; the merge was automatic and included no unmerged branch.

## Acceptance evidence

- `build.log`: production TypeScript and Vite build.
- `source-ui.log`: presentation, UI navigation, coverage and pipeline checks.
- `pages.log`: actual brain-pointer navigation, package layout, standard vote, story controls, takeover and decision-package checks. One initial architecture-layout setup failed because its earlier temporary language-kit asset no longer existed; the raw result is retained.
- `fixture.json` and `architecture-pages.log`: restored exact pinned kit checksum, followed by architecture layout/walkthrough/selector acceptance.
- `frame/result.json` and screenshots: actual app geometry, normal/coarse menus, narrow widths, text enlargement, reading order, repeated brain inspection, focus and keyboard isolation, Close and Done with answers preserved.
- `street/result.json`: YAML compiled through existing package construction and checked with the current shared deck renderer, including generated decision and lead-in slides at both frame sizes.
- `source-hashes.json`: current source used for these checks.

Focused acceptance is complete: production build passes; source/UI checks pass (45); the page run passes 27 cases with the missing-fixture setup error retained; all four architecture cases, including that layout case, pass after restoring the pinned asset. Current frame and YAML street production acceptance both report ok with matching source/compiled renderer hashes. Planning owns independent review, hosted CI and the fresh-author Merge/Hold package. No merge approval is asserted.
