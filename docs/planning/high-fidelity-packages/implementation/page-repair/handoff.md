# Page regression repair

The screenshot inventories omitted the added package-border capture. The standard vote remained visible but the test required it to be inside the old header. The navigation test first failed on the visible story group: the 36-pixel border holds 32-pixel targets and leaves no lane with the shared pipeline clearance. It also expected hidden Chapters selection and a running-only Stop control to be visible together.

Screenshot assertions now include the border capture. Vote assertions address the host vote directly while retaining answer persistence checks. Package control reveal uses the registered package.menu command; the page case exercises Chapters selection and switches to Stop using the actual server state frame. Geometry correction remains under review.

The corridor experiment restores pointing to story, Tell, Chapters and More. The full navigation case also passes after the covered inert iframe is marked visibility:hidden while the opaque host menu is open; its layout is preserved. The permanent routing gutter remains a Planning decision. The vote test checks the iframe contents and compares vote/frame bounds as well as persisting both answers.

## Decision needed before final navigation validation and push

The approved border proposal says: “Use one 36 CSS pixel top frame containing icon buttons. The slide begins directly below it.” It also says: “Retain the existing 16 pixel attention corridors on both sides.” Its height table subtracts only the 36-pixel frame from PackageView height. Adding the shared 16-pixel top routing gutter therefore changes the recorded canvas-height result.

Recommendation: approve the existing pipeline corridor above the unchanged 36-pixel border, with the overlay offset increased by the same corridor. This preserves 32-pixel buttons and the shared 4-pixel signal clearance. `proposed-corridor.patch` is reviewable and unapplied. The experiment injected that gutter only in the test; the injection has been removed from the final test source. The current production branch still cannot pass the compact-border pointing case until the geometry is decided and implemented. No push has been made.

Validation complete for independent repairs: decision-package, package-inbox vote and story-control page suites pass (12 tests); UI navigation, coverage and pipeline suites pass (14 tests). Compact-border navigation passed only with the documented experimental gutter. The independent repairs are preserved in a local commit following the independently approved Details fix `d5106892b3b046c69f5522975bb67454f489a1db`. Final production navigation validation and push remain blocked on the geometry decision. Planning owns the decision package and push timing. The proposed corridor remains unapplied; decision-package outputs and decision-handoff.json are untouched.
