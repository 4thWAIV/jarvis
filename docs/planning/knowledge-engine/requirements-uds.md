# Knowledge engine requirements under UDS

Work item: Knowledge engine (`work-d4f52552897e`), piece `uds-requirements`. Written 2026-11-10.

This list merges Tim's approved UDS logical data design with his earlier requirements for the knowledge engine. Each requirement has a slug, the requirement, and its source. Where two sources disagree, the conflict is called out at the end of the requirement and again in the conflicts section.

## Sources

- `uds-approved`: ./data/packages/uds-logical-data-capabilities-1.0.1-localc36/design/APPROVED-LOGICAL-DESIGN.md, approved by Tim through pkg-5a88591089ff (v1) and pkg-39129b7ff121 (v2, "approve-design: approve").
- `uds-python`: design/PYTHON-DESIGN.md in the same package (the same design as Python interfaces, with ten acceptance checks).
- `uds-v1-decisions` and `uds-v2-decisions`: the two packages' results/decisions.md and decisions.json under ./data/packages/.
- `proteus-reqs`: ~/code/macos/nuevco/proteus/planning/original/authoritative/requirements/03-namespace-storage-and-data.md (DATA-xxx), 06-identity-session-and-trust.md (ID-xxx); `proteus-arch-38`: architecture/38-access-control-and-role-model.md. Found in the proteus checkout; the same tree is in three other proteus worktrees.
- `uds-brief`: ~/code/macos/nuevco/proteus-render-codex/run-records/2026-11-10-data-capabilities-package/DATA-CAPABILITIES-BRIEF.md (the evidence behind the design).
- `4w-uds`: ~/code/macos/4thWAIV/fourth-waiv-ai/docs/uds.md.
- `tim-earlier`: Tim's requirements for the engine given before UDS, as recorded on the work item (cleaned wording).
- `engine-design`: the engine's own build plan, ./docs/planning/work-package-bundles/build-plan.md (invariants I1 to I8). Not approved by Tim; carried only where nothing above replaces it.
- `docdb-fix`: the package "Document database gap and chunking fix" (pkg-f807379c5085), which Tim answered Change it. A proposal, not an approval.

## One path, five streams

1. `one-path`: Every item has exactly one path, and the path is the one entry point to all of its streams. Reaching any stream from another is a direct step, not a second query. Sources: `uds-approved` section 2; DATA-050, DATA-051; `tim-earlier` (he wants one namespace for all stores).
2. `five-streams`: A path can hold five streams: PathEntry (path metadata, parent, type suffix), Document (structured fields about the item), Content (the item itself, such as a PDF; folders have none), Embedding (vectors), AccessRights (who may do what). Sources: `uds-approved` section 2; DATA-049, DATA-061; `proteus-arch-38` line 212; `4w-uds` line 226 ("Folders don't carry content streams").
3. `ownership-namespace`: The namespace reflects ownership: an item lives where it is managed from, and rights follow the paths. Source: `4w-uds` line 257, cited by `uds-approved` section 2.
4. `counted-once`: An item is counted once. Its data, content, sections and vectors are streams of one item, never separate databases; indexes are views over items. Every count, status line and search result counts the item once, and vector coverage shows as embedded and waiting. Sources: `uds-python` central rule and acceptance check "Counted once"; Tim said the document database and the vector search database were being counted twice.
5. `atomic-write`: A write that touches several streams at one path becomes visible all at once, and the streams of one path share one commit order. Sources: DATA-053, DATA-054; `uds-approved` acceptance check 1. Conflict: see `embed-on-write` and conflict `vectors-in-the-write`.
6. `delete-all-streams`: Deleting an item removes all its streams together, and no index returns it afterwards. Source: `uds-python` check 4.
7. `content-reference`: The Document carries the reference to its Content, so going from data to content is cheap. Source: DATA-052.
8. `type-suffix`: An item's type is its path's type suffix (`.pdf`, `.work`, `.project`); a folder path has none. Sources: `uds-approved` section 2; `4w-uds` naming rules.

## Document stream and data index

9. `any-shape`: The Document stream holds structured JSON of any shape (nested objects, arrays), like MongoDB, DynamoDB or Cosmos DB. No fixed list of properties per type. Sources: `tim-earlier`; `uds-approved` section 2 (structured typed fields; the type is the suffix, the fields are the item's own).
10. `every-field-indexed`: Every field of every document is in one inverted index, together with path, parent, ancestors, type and version. Sources: `uds-approved` section 3; DATA-048; `uds-brief` 4.2 (database-design-requirements.md:509); `tim-earlier`.
11. `records-in-full`: JARVIS's own structured records (projects, folders, Work items and pieces, decisions, runs, workflows, messages, packages, agents, topics) are stored as full documents at their paths and kept current as they change. Sources: `tim-earlier` (document database over his records); `docdb-fix` finding that only a key and links are stored today. Conflict: engine-design I8 (see conflicts).
12. `vectors-are-not-a-document-db`: The vector index holding text is not a document database; the data index is its own capability. Source: `tim-earlier`.

## Content stream and sections

13. `content-stream`: The Content stream is the item itself (the bytes of a PDF, a Markdown file, a Word file). Source: `uds-approved` section 2. For JARVIS, files stay where they are in his folders (folder catalog rule), so Content is the file in place plus its hash and its extracted text. See decision `history-holds`.
14. `sections`: Content is split into sections at its headings (and at pages or slides where a format has them). Sections belong to the item; they are never separate items and never counted. Source: `uds-python` sections 1 and 3.
15. `section-quality`: A section's embedded text carries the item's title and its heading path; a long section is split at paragraph and then sentence boundaries, never mid-word, with code blocks and tables kept whole where they fit. Source: `docdb-fix` proposal, carried here as the quality bar for `sections`.
16. `content-formats`: The Content of every readable document format gets sections: Markdown, plain text, PDF, Word, Excel, PowerPoint, HTML. Source: `uds-approved` (the CV example is a PDF); the readers already in JARVIS (`bundle_readers`).

## Embedding stream and vector index

17. `item-and-section-vectors`: Every item with content gets one whole-item vector plus one vector per section. The whole-item vector ranks the item; the section vectors say where it matched. Sources: `uds-v1-decisions` item-vectors ("Whole item plus sections"); `uds-approved` section 3.
18. `embed-on-write`: Embeddings are made when an item is written and kept in step when it changes. Users never manage vectors or indexes. Sources: `uds-approved` section 2; DATA-062. DATA-063 adds: "Writes shall be acknowledged immediately; embeddings shall be computed in background and indexed when ready." Conflict with `atomic-write`: see decision `vectors-follow-writes`.
19. `one-hit-per-item`: Every search hit is a whole item with its path, never a loose chunk. Hits from several indexes on one item merge into one hit that lists every index that found it and the sections where it matched. Sources: `uds-approved` section 3; `uds-python` section 3 and check 2.

## Full-text index

20. `full-text`: A full-text index over content finds exact words, phrases, names and codes ("SOC 2 Type II"). A phrase search finds every readable item that holds the phrase, and only those. Sources: `uds-v2-decisions` fulltext-index (add-fulltext); `uds-approved` section 3 and check 4.

## Graph index

21. `graph-first-class`: The graph is a first-class index with three kinds of link: hierarchy (parent and child), references (a field that points to a path) and derived links (found in content). Sources: `uds-v1-decisions` graph-index ("First-class graph index"); `uds-approved` section 3; `tim-earlier` (a graph database). Supersedes "Full graph traversal is not required" in database-design-requirements.md:1183.
22. `derived-link-provenance`: Every derived link records the model that made it and when. AI-made links are proposals until the owner accepts them. Sources: `uds-approved` sections 3 and 5; `uds-python` section 4.

## AccessRights stream and the security gate

23. `data-content-rights`: The data (Document) and the content (Content) have separate read rights. Vectors, sections and full-text hits reveal content, so they need the content right. Sources: `uds-v1-decisions` stream-rights ("Data and content apart"); `uds-approved` section 4; `4w-uds` line 518.
24. `gate-everything`: Nothing any index returns shows what the caller cannot read: vector hits, document matches, full-text hits, graph edges, counts. Sources: `uds-approved` section 3; DATA-067.
25. `edge-both-ends`: A graph edge is visible only if the caller can read both ends. Source: `uds-approved` section 3 and check 5.
26. `hidden-field-predicates`: A field the caller cannot see cannot be used in a search predicate; such a predicate matches nothing. Sources: `uds-approved` section 3 and check 6; closes the leak in `proteus-arch-38` lines 483 to 487.
27. `denied-looks-missing`: A denied read looks the same as a missing item. Sources: `uds-approved` section 3; `4w-uds` line 518.
28. `filter-order-free`: When the filter runs (before, during or after search) is an implementation choice; only correct results are required. Source: DATA-099.
29. `rights-inherit`: Rights are set on a path and inherited by its children; a child can add rights. Source: `uds-python` section 4.
30. `four-layers`: Policy, path rights, document conditions and field groups stay in force, broadest first; policy can deny anything. Source: `uds-approved` section 4 (`proteus-arch-38` lines 17 to 37). Conflict: the Python slant leaves the policy layer out (see conflicts).

## The AI at a path

31. `ai-persons-rights`: The AI reads and searches with the rights of the person it works for, never wider; an AI search never returns more than that person's own search. Sources: `uds-v1-decisions` ai-rights ("The person's rights, never wider"); `uds-approved` section 5 and check 7; ID-015 to ID-019.
32. `ai-data-first`: The AI asks for a path and the streams it needs, gets the data first, and the content only when it needs it and is allowed. Source: `uds-approved` section 5.
33. `ai-writes-tagged`: Everything the AI writes is a new version tagged with the agent, the person and the model. Sources: `uds-approved` section 5 and check 9.
34. `ai-content-reported`: Content the AI reads is reported, never obeyed. Source: `uds-approved` section 5 (JARVIS's walled untrusted text already follows this rule).

## History

35. `item-history`: Each item's past stays available through `get(path, version)`, its history, `diff` and `getAt(path, time)`. A version is the whole item, all streams, at one point in time. Sources: `uds-approved` section 3 and check 8; database-design-requirements.md:928-931.
36. `no-history-index`: There is no history index. Old versions stay out of the search indexes. Sources: `uds-v2-decisions` history-index (no-history-index); `uds-approved` section 3.

## What is in and out

37. `in-out-control`: There is a way to manage what is in and out of indexing, per folder and below it. Source: `tim-earlier`.
38. `folder-scope-honoured`: The per-folder index scope already saved with each project folder (docs, text, off) is honoured by ingest. Source: finding recorded on the work item; follows from `in-out-control`.

## Interface and parity

39. `uds-interface`: The engine offers the UDS interface: put, get (with the streams asked for), get_at, history, delete, count, search (meaning, where, phrase, words, linked_to, under), links and propose_link. Source: `uds-python` section 2. Tim asked for "the same conceptual interface built with the tools we have here in Python".
40. `brain-parity`: Anything the page lets Tim see or do with the engine, JARVIS can do by voice through the same function. Source: JARVIS's standing convention (CLAUDE.md, Conventions).

## User interface

41. `ui-top-folders-count`: For now the only UI is the top-level folders, in the Folders view that already exists, each with its document count. A folder or data browser is a separate project, later. Source: `uds-v1-decisions` path-tabs note (Tim: show only the top-level folders and the document count for now; capability first, UI later once it works).

## Carried from the engine's own design

42. `never-compete-with-hearing`: The engine never slows hearing or speech: it pauses while the conversation is busy or the CPU is over 70 percent. Source: `engine-design` I4; CLAUDE.md "The knowledge engine".
43. `local-only`: The engine listens on loopback only, with its own token. Source: `engine-design` I6.
44. `deadlines`: Every engine call has a deadline and answers partial past it. Source: `engine-design` I3.

## Out of scope

- A history index (`uds-v2-decisions`).
- Any folder or data UI beyond `ui-top-folders-count` (`uds-v1-decisions`).
- Tenants, physical storage design beyond what the engine already is, replication, C# and Rust (`uds-approved` section 6; `uds-python` section 6).

## Conflicts

- `vectors-in-the-write`: `atomic-write` (approved check 1: Document, Content and Embedding visible together or none) against DATA-063 (embeddings computed in the background) and the Python design's own `waiting` count. Embedding a 17,000-character document takes about 25 section embeddings on this Mac, and ingest pauses whenever Tim talks, so a write that waits for its vectors would wait seconds and stall during conversation. Needs Tim: decision `vectors-follow-writes`.
- `history-vs-rebuild`: `item-history` against engine-design I1 ("no unique truth": the engine can be dropped and rebuilt). Past versions of files and records exist nowhere else once kept, so the engine stops being fully rebuildable and needs backups. What a version holds is decision `history-holds`.
- `content-in-place`: `content-stream` (the bytes are a stream at the path) against JARVIS's folder catalog rule (files stay where they are; nothing copies them). Resolved for the current version by keeping Content as the file in place with its hash; for past versions it is decision `history-holds`.
- `refer-by-id`: `records-in-full` and `any-shape` against engine-design I8 ("kinds declare their properties; nothing else is stored"). Tim's requirements win; I8 is dropped.
- `truth-or-mirror`: `one-path` (one namespace for all stores) could mean the engine replaces JARVIS's SQLite records as the place of truth, or holds a full copy of each at its path. Needs Tim: decision `records-mirrored`.
- `four-layers-in-jarvis`: `four-layers` (approved) against `uds-python`, which leaves the policy layer and tenants out. JARVIS has one owner and no policy language, so path rights and field groups are built; the policy layer and document conditions are recorded as not built yet.
- `render-scope`: `uds-python` says its coding package must not change the knowledge engine. That limited Render's package only; Tim has now asked for the design to be applied to the engine. The engine and Render's in-memory `uds` package should share one interface and one set of acceptance tests.
