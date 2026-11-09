# Author-supplied package understanding

The deliverable has two responsibilities: expose the author's declared understanding and validate its coverage/readability during package creation. The branch starts from main `8a5717a`. The earlier broad implementation remains on `saved/jarvis-groks-packages-broad-20261108` and is not included.

## Declaration and creation

`Manifest.understanding` is an optional member-to-Markdown mapping, preserved in package metadata. The existing optional `brief` remains the primary authored document; page folders can infer it from the first declared explanation. A Markdown explanation can cover several members. Package metadata and the explanations themselves need no circular self-map. Supplied technical assets can use the same brief.

`package_understanding.validate(folder, manifest, members)` receives the existing zipper's exact member list. It checks complete coverage, existing ordinary members, and actual usable UTF-8 Markdown through the existing safe folder reader. It never parses the source PDF, HTML, image or other opaque material. Missing coverage, missing files, unreadable/binary/empty or linked explanations refuse creation. A passing check establishes readable authored text and declared coverage, not semantic equivalence.

`prepare_deck(folder, deck, template, source_dir)` runs after the existing shared deck build and before its zip step. It serializes the author's existing structured content and separately labeled narration into `UNDERSTANDING.md`. Standard shared template files are covered there. Existing authored alt text and option name/says retain their associations with pictures; other pictures require declared author Markdown. The factory copies only those explicitly declared Markdown files. No PDF builder, arbitrary-content reader or discovery system is added.

## Existing JARVIS access

The existing package-open path points to the authored material. The existing read-only `package_inbox` tool accepts `understanding_offset`, reads only manifest-declared Markdown and returns bounded pages with `next_offset`. Continue until null to expose the complete supplied text through the existing 1,500-character result funnel. Each page stays inside the existing untrusted wrapper. This is not a claim that opening a package alone read all its content.

Existing installed manifests, story/navigation, answers, read-back approval, sender proof, transport, bundle readers and screen tools retain their baseline behavior. There are no new observer, search/index, snapshot, classifier, authority/receipt, history or follow-up services in this deliverable.

## Evidence and release gates

Focused tests exercise opaque source material with author Markdown, missing member coverage, missing/unreadable/empty/linked explanations, manifest preservation, complete multi-page access through the actual internal tool, delimiter safety, existing package modes and the ordinary factory. The isolated browser factory trial exercises the existing page and decision flow; it does not send to the live service.

Universal fresh-author enforcement is deferred and is not part of this change. Final release requires CI and root-coordinated merge/deploy, including the primary checkout's existing capture-edit reconciliation. No deployment is claimed here.

## Validation checkpoint

The combined focused run exercised factory creation, existing package modes and the complete declared-author read path. The wrapper and argument-echo checks passed. Its header fixture failures used an opaque placeholder instead of a package manifest; the fixture now models the optional understanding field, and the full header suite passes. Final focused creation/declared-access/wrapper checks pass with the aggregate author-view bound enforced at creation and read time. The actual isolated factory browser trial passed with the existing decision/page controls.

The complete declared author view uses the existing 4 MiB text-read budget in total, preventing a package from passing creation with authored material the read path cannot serve. This is a readability/access limit, not a semantic check. Required PR CI remains the release gate; no live service has been changed by this worker.
