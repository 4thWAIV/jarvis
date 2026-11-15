# Repeated inspection repair

Independent review found that repeated inspection requests created multiple dialogs and the first close cleared stage inertness while another dialog remained. The regression test reproduced three dialogs from the public API and host message path.

The shared renderer now owns one inspector. An active request leaves that inspector and its selected source description in place. Its close handler restores the stage state captured on opening and ignores stale close callbacks. Both host controls and brain commands use this renderer boundary.

The presentation suite passes (31 tests). The actual-app frame check passes after sending repeated ui_navigate package.inspect calls through the brain tool, registered command, mounted adapter and frame message. It checks one dialog, stage inertness, all six navigation keys, Escape, reopening by brain command, Return, focus restoration and navigation after close. Answers remain unchanged. No push was performed; Planning owns replacement timing.

Evidence: `before.log` records the regression before the fix; `tests.log` and `frame.log` record the passing runs; `frame/result.json` binds the compiled renderer hash to current source and records the actual brain path result. The frame screenshots are from this final source.
