# Approved routing-strip amendment

Tim approved the routing-strip amendment as written on 2026-11-15. The approval is recorded in `data/packages/room-to-point-at-presentation-controls-1.0.0-local89b/results/decisions.json`, decision `approve-routing-strip`, answer `approve`. This amends the first-release contract and border-controls proposal for Work `work-a7e4bcb1b514`.

The existing 16 CSS pixel attention corridor also runs above the package border. The normal border remains 36 pixels high with 32-pixel hit targets; coarse-pointer dimensions remain 48 and 44 pixels. The shared pipeline keeps its 4-pixel clearance. At a fixed PackageView height the slide receives 16 fewer vertical pixels than the earlier border proposal. The opaque host menu begins beneath the border, including the added strip in its top offset.

The amendment changes this geometry only. Existing decision writers, narration behavior and later playback contracts retain their scope.

The approved proposed-corridor.patch was applied in the original presentation worktree. Current main `bdd9ab3c5a03e9bd67031af99ceba19a9d9decee` now includes merged PR144; integrating it merged automatically. No unmerged branch was included.
