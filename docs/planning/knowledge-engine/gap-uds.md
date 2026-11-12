# Knowledge engine against the UDS requirements: gap analysis

Work item: Knowledge engine (`work-d4f52552897e`), piece `uds-requirements`. Written 2026-11-10 from the code on main at f4b56fa.

Each requirement in ./docs/planning/knowledge-engine/requirements-uds.md is checked against the engine as built. Verdicts: **met**, **partly** (exactly what is there and what is not), **not met**, or **conflicts** (what is built works against it, and what would be removed or reworked). References are `file:function`.

## Summary by stream

| Stream or index | Today | Verdict |
|---|---|---|
| PathEntry | No paths. Keys are hashes (`doc:<sha256>`, `chunk:<sha256>`); the source's reference is a property. No parent, no type suffix, no hierarchy. | not met |
| Document | A JSON document store with an index over every property, but only 16 fixed kinds with fixed property lists; JARVIS's records are not stored beyond a key. | partly, conflicts (I8) |
| Content | Not stored. Only line-packed chunks of Markdown text; no file hash; no PDF or Office text. | not met |
| Embedding | One vector per chunk, kept in step with the text. No whole-item vector; hits are chunks, not items. | partly |
| AccessRights | None. One engine token sees everything; only `_trust` (own or untrusted) filters. | not met |
| Data index | Inverted index over every property of the allowed kinds, with =, !=, <, >, in, prefix, exists. No parent or ancestors. | partly |
| Full-text index | BM25 keyword search over chunks. No positions, so no exact phrase. | partly |
| Graph index | Typed graph with a fixed table of 22 edge kinds; only chunk-to-document, chunk-to-agent, chunk-to-topic and topic links are written. No hierarchy or field references. | partly, conflicts |
| History | Point-in-time reads by log number for `from` and `walk` only; compaction throws old versions away. | not met |

## One path, five streams

1. `one-path` — **not met.** Items are keyed by hashes: `knowledge_ingest.py:doc_key` (`doc:` + SHA-256 of the source) and `chunk_key`. The workspace reference (`jref:1:<node>:folder_file:<id>/<path>`, `knowledge_ingest.py:jref`) is stored as `_source`, not as the key. Getting an item by its path needs a `find` on `_source`. A chunk and its document are two documents joined by an `in_document` edge (`knowledge_ingest.py:Writer.put`).
2. `five-streams` — **not met.** `jarvis/knowledge/kinds.py:KINDS` has no notion of streams. The document holds a few properties, the chunks hold the text, the vectors are rows in `jarvis/knowledge/vectors.py`, and there is no Content or AccessRights.
3. `ownership-namespace` — **not met.** `_ns` and `_owner` default to `"local"` for everything (`jarvis/knowledge/documents.py:DocumentStore._check`). There is no path layout.
4. `counted-once` — **conflicts.** The status counts chunks, not items: `jarvis/knowledge_api.py:status` returns `chunks`, and the Knowledge view shows it (`frontend/src/KnowledgeView.tsx`, `main.knowledge.status`). `knowledge_api.py:embedding_model` counts vector rows and waiting chunks. A document with 25 chunks is 1 document, 25 chunks and 25 vectors. Rework: count items once, with embedded and waiting as coverage.
5. `atomic-write` — **partly.** `jarvis/knowledge/engine.py:Engine.transaction` writes documents, deletes and edges in one log transaction; readers see all or none. `Writer.put` sends the document, its chunks and its stale-chunk deletes in one transaction. Vectors are not part of it: `jarvis/knowledge/vectors.py:VectorIndex._on_apply` deletes stale rows in the transaction and `_embed_loop` embeds in the background. A vector never answers for text it was not made from (stale rows are masked), so search is never wrong, but the item is visible before its vectors. See decision `vectors-follow-writes`.
6. `delete-all-streams` — **partly.** A document's stale chunks are deleted with it; `forget` removes every copy by source (`DocumentStore.forget_sources`). Deleting a document does not delete its chunks unless the writer lists them.
7. `content-reference` — **partly.** `_source` names the file; `_rev` is the SHA-256 of the extracted text, not of the file.
8. `type-suffix` — **not met.** Type is `_kind` from a fixed list.

## Document stream and data index

9. `any-shape` — **conflicts.** `DocumentStore._check` refuses any property not in the kind's list ("a {kind} has no property ..."), and `kinds.kind_of` refuses any kind not among the 16. This is invariant I8. Rework: drop the property lists and the closed kind list; keep `kinds.flatten` and the reserved system names.
10. `every-field-indexed` — **partly.** `documents.py:index_entries` indexes every flattened property, nested paths and array elements, in one store, with the operators in `documents.OPS`; the advisor adds compound indexes (`jarvis/knowledge/advisor.py`). Missing: path, parent and ancestors (no paths), version. Chunks index only three properties (`kinds.py`, `indexed=("language", "heading_path[]", "at")`).
11. `records-in-full` — **not met** (now: records move into the engine and SQLite is retired; see "Records in the engine, SQLite retired"). Ingest writes only document, chunk, topic and agent (`knowledge_ingest.py`: `Writer.put`, `Writer.append`, `Topics.run`, `agent_node`). Runs and workflow events are written as chunks of text, not as records (`knowledge_ingest.py:Runs.run`, `Workflows.run`). Work items, pieces, decisions, messages, packages, projects and folders are not written at all.
12. `vectors-are-not-a-document-db` — **met in code, not in the words used.** The vector rows and the document store are separate (`vectors.py`, `documents.py`); the earlier status package counted them as two databases, which `counted-once` corrects.

## Content stream and sections

13. `content-stream` — **not met; now defined by Tim's revision.** Content stays in the folders on disk and is never copied into the engine; the engine holds each file's PathEntry (size, modified time, inode, SHA-256), its Document data, its extracted text and sections, its vectors, its rights and its history. Today the engine stores no file hash and no content reference, and changes are found only by re-walking every folder every 10 minutes (`knowledge_runtime.py:INGEST_EVERY_SEC`) against a ledger of size and modified time (`knowledge_ingest.py:Ledgered`). There is no file watcher anywhere in JARVIS (no FSEvents or kqueue use). Rework: an FSEvents watcher with replay from the last event ID, a backup scan at startup, after wake and every 6 hours, and the ledger replaced by PathEntry.
14. `sections` — **conflicts.** `knowledge_ingest.py:chunk_text` splits at headings, then packs whole lines to `CHUNK_MAX` (1000 characters). Each chunk is its own document of kind `chunk`, counted and returned on its own. Rework: sections become part of the item, keyed by index, never their own documents; the `chunk` kind is dropped.
15. `section-quality` — **not met.** `chunk_text` cuts a long line at exactly 1000 characters (mid-word), overlaps only the previous chunk's last sentence when it is 200 characters or less (`OVERLAP_MAX`), splits code blocks and tables like prose, and does not put the title or heading path into the embedded text (`vectors.py:embedded_prop` embeds `text` only). Conversation items are one chunk whatever their length (`Writer.append`, `markdown=False`).
16. `content-formats` — **not met.** `knowledge_ingest.py:Folders.wanted` takes only Markdown, at depth two or under `docs/`, at most 400 files a folder, each under 512 KB. JARVIS already has readers for PDF, Word, Excel, PowerPoint, HTML and text (`jarvis/bundle_readers/`), used today only for received files.

## Embedding stream and vector index

17. `item-and-section-vectors` — **partly.** Section-level vectors exist as chunk vectors (`vectors.py:VectorIndex._embed_batch`, one row per document's one embedded property). No whole-item vector.
18. `embed-on-write` — **met, in the background.** Changed text has its rows deleted in the same transaction and is embedded again (`VectorIndex._on_apply`, `_queue_missing`); nobody manages vectors. The model is multilingual-e5-small (`jarvis/knowledge/e5_onnx.py`).
19. `one-hit-per-item` — **not met.** `jarvis/knowledge/query.py:run` fuses `match`, `near` and `about` by reciprocal rank over chunk ordinals; each chunk is its own hit with its own text (`knowledge_api.py:_hits`). Five chunks of one file are five hits. `why` says which index found each hit; that part carries over as `matched_by`.

## Full-text index

20. `full-text` — **partly.** `jarvis/knowledge/lexical.py:Lexical.search` is BM25 over chunks only (`INDEXED_KINDS = ("chunk",)`), with English and Spanish stop words. `jarvis/knowledge/postings.py:encode` stores ordinals and term counts, no positions, so an exact phrase cannot be found, and stop words ("Type", "of") are dropped from the index. Rework: positions per section, phrase match, no stop-word loss for phrases.

## Graph index

21. `graph-first-class` — **partly, conflicts.** `jarvis/knowledge/graph.py` is a real typed graph (both directions stored, walks with budgets). But `graph.py:check_edge` allows only the 22 kinds in `EDGE_KINDS`, each between fixed kinds; origins are `record`, `authored`, `extracted`, `imported`. No hierarchy edges are written: documents carry no `folder`, so `in_folder` is never made. No field references, because records are not stored. Rework: hierarchy edges from paths, reference edges from any field holding a path, open edge kinds, origins mapped to hierarchy, reference, derived.
22. `derived-link-provenance` — **partly.** `jarvis/knowledge/linker.py:Linker.run` writes `mentions` edges with origin `extracted` and makes proposals that wait for Tim's yes (`knowledge_api.py:change_links`). It records no model and no time on the edge.

## AccessRights stream and the security gate

23. `data-content-rights` — **not met.** No rights exist. `jarvis/knowledge/auth.py:check` admits any request with the engine's token.
24. `gate-everything` — **not met.** The only filters are `_trust` and `_ns`, chosen by the caller (`query.py:run`, `visible`). Cards and hot cards (`jarvis/knowledge/cards.py`, `knowledge_client.py:HotCards`) and `resolve` return without any check.
25. `edge-both-ends` — **not met.** `graph.py:Graph.edges` and `walk` return every edge.
26. `hidden-field-predicates` — **not met.** No field groups.
27. `denied-looks-missing` — **not met.** Nothing is denied.
28. `filter-order-free` — **met by design once a gate exists.** The query already pre-filters through `within` sets (`query.py:run`, `vectors.py:VectorIndex.search(within=...)`), so a rights set can be applied the same way.
29. `rights-inherit` — **not met** in the engine. JARVIS's folder policy decides which reader may read a folder by where it is (`jarvis/folder_policy.py`); that is the closest thing and stays the gate on reading files.
30. `four-layers` — **not met.** No policy layer; out of scope for now (see requirements conflict `four-layers-in-jarvis`).

## The AI at a path

31. `ai-persons-rights` — **partly by accident.** The brain and the run context pack (`knowledge_api.py:run_pack`, `work_context`) see everything Tim owns, which today is everything; nothing records whom a search is for.
32. `ai-data-first` — **not met.** A search returns the chunk text directly (`knowledge_api.py:RETURN` includes `text`).
33. `ai-writes-tagged` — **not met.** No versions; writes carry no agent, person or model.
34. `ai-content-reported` — **met.** Every document carries `_trust`; tool results are walled as untrusted (`knowledge_api.py` module notes, `_hits`).

## History

35. `item-history` — **not met.** `jarvis/knowledge/store.py:Store.reading(at)` reads as of a log number, and `query.py:run` allows `at` only with `from` and `walk`. `store.py:compact_plan` keeps "every version newer than the oldest pin and the newest at or below it", so older versions are gone after compaction. No `getAt` by time, no version numbers per item, no diff. Snapshots exist (`Store.snapshot`) but nothing takes them on a schedule.
36. `no-history-index` — **met.** Indexes cover only the newest state.

## What is in and out

37. `in-out-control` — **not met.** No include or exclude control. `Folders.wanted` is hard-coded.
38. `folder-scope-honoured` — **not met.** `jarvis/project_store.py` keeps `project_folders.index_scope` (`docs`, `text`, `off`); `knowledge_ingest.py` never reads it.

## Interface and parity

39. `uds-interface` — **not met.** The engine's HTTP API (`jarvis/knowledge/serve.py`: `/v1/txn`, `/v1/nodes`, `/v1/edges`, `/v1/walk`, `/v1/query`, `/v1/search`, `/v1/find`, `/v1/forget`) is a document-and-graph API, not put, get, get_at, history, delete, count, search, links, propose_link.
40. `brain-parity` — **met for what exists.** `knowledge_api.py` functions are shared by `knowledge_routes.py` and the brain's tools.

## User interface

41. `ui-top-folders-count` — **not met.** The Folders view lists folders; it shows no document count from the engine. The Knowledge view shows chunk counts.

## Carried from the engine's own design

42. `never-compete-with-hearing` — **met.** `jarvis/knowledge_runtime.py:Pace.decide`, engine at nice 10, compaction stopped on hold.
43. `local-only` — **met.** `jarvis/knowledge/auth.py:host_allowed`, loopback bind.
44. `deadlines` — **met.** `query.py:run` `budget_ms`, `knowledge_client.py` deadlines.

## Records in the engine, SQLite retired (Tim's answer to `records-mirrored`)

Under Tim's revision the 76 data tables are redesigned as 18 document types, not ported one to one; the grouping is in ./docs/planning/knowledge-engine/plan-uds.md ("JARVIS's records become documents, redesigned"). Append-only logs read whole (`run_events`, `voice_samples`, `voice_events`) become Content files on disk.

Tim chose neither option: SQLite is replaced by the document engine and retired in this effort. Requirement `records-in-full` therefore becomes: every JARVIS record lives only in the engine, at its path. JARVIS has one SQLite file (`data/jarvis.db`, 52 MB) holding 16 stores and 88 tables, 12 of which only hold schema versions. Against what a record store needs, the engine today:

| Needed to hold records | Today | Verdict |
|---|---|---|
| Atomic writes of several documents | `engine.py:Engine.transaction`: puts, deletes, edges and watermarks in one log transaction; readers see all or none. | met |
| Write acknowledged before any notice | The engine acknowledges after the log write (build-plan invariant I7); hints come from JARVIS. | met |
| Conditional writes (expected revision, path not yet taken) for Work's same-transaction rules, wait links, a decision key asked once, the inbox's same-bytes row | No preconditions; `documents.py:DocumentStore.plan` writes whatever it is given. | not met |
| History for records whose changes are logged today (`work_events`, `workflow_events`, `package_inbox_events`, `project_events`, `attention_events`, `agent_key_events`) | Under Tim's revision these become each document's versions, tagged with who wrote them and why. The engine keeps versions only until compaction (`store.py:compact_plan`) and tags none. | not met |
| Ordered reads with limit and cursor, counts, grouped counts, a change feed | `documents.py:DocumentStore.find` takes `where`, an order and a limit; no cursor, no counts or groups; ingest follows changes by its own watermarks, not a feed of every write. | partly |
| Always up | Runs only when `JARVIS_KNOWLEDGE` is on (`knowledge_client.py:enabled`); `knowledge_client.py:Supervisor` restarts after 1, 5 and 30 s and stops after three restarts in ten minutes (`RESTARTS_MAX`, `RESTART_WINDOW_SEC`); it is restarted past 1.5 times its memory budget (`FOOTPRINT_LIMIT`). | conflicts |
| Record reads and writes never paused | `knowledge_runtime.py:Pace.decide` holds the whole engine while the floor is busy or CPU is over 70 percent; compaction is stopped with SIGSTOP; the process runs at nice 10 (`serve.py:ENGINE_NICE`). Writes still go through while held, but the process is deprioritised as a whole. | conflicts |
| Never dropped | `python -m jarvis.knowledge rebuild` drops the directory (invariant I1); snapshots exist (`store.py:Store.snapshot`) but none is taken on a schedule. | conflicts |
| Durable answers | Each commit is fsynced; a full flush (`wal.py:full_fsync`) runs every 10 s (`BARRIER_EVERY_SEC`). Same exposure as SQLite on macOS today. | met, with a full flush at commit added for Tim's answers |
| Fast reads for every request | Loopback HTTP with a bearer token, about a millisecond a call, against SQLite in-process. | partly: JARVIS caches what it reads per request |
| Tests | `knowledge_client.py:InProcessTransport` runs the engine inside a test; 26 test files open SQLite directly. | partly |

Rework: the supervisor, pace and priority (two lanes: records never paused; ingest, embedding, compaction, linker and cards paced), conditions in transactions, versions tagged with who and why and kept through compaction, ordered scans, counts and a change feed in the document store, the FSEvents watcher and backup scan, rebuild limited to indexes, daily snapshots, and the inside of all 16 store modules. Dropped: SQLite, `run_store._connect`, `run_store.migrate`, the 12 schema tables and `migrations/001_dispatches_to_runs.py`.

## What is kept, reworked and dropped

**Kept as built:** the write-ahead log (`wal.py`), segments and block pool (`segment.py`), manifest and snapshots (`manifest.py`, `Store.snapshot`), the store's reads and point-in-time pins (`store.py`), the inverted document index and its predicates (`documents.py`: `index_entries`, `Predicate`), the index advisor and his pins (`advisor.py`, `knowledge_links.py:choose_index`), the vector files, Accelerate scan, stale-row masks and deadlines (`vectors.py`, `vecmath.py`), the e5 model, its tokenizer and install (`e5_onnx.py`, `unigram.py`, `embedding_models.py`), the embedding queue (`embedder.py`), BM25 scoring (`lexical.py`), the supervisor, pace and ingest scheduler (`knowledge_client.py`, `knowledge_runtime.py`), the loopback server and token (`serve.py`, `auth.py`), topics and link proposals (`knowledge_links.py`, `linker.py`), seeds, the context pack.

**Reworked:** `kinds.py` (types become open path suffixes), `documents.py:_check` (no property lists), `graph.py` (open edge kinds; hierarchy, reference, derived), `query.py:run` (one hit per item, phrase, rights), `vectors.py` (whole-item plus section rows per item), `lexical.py` and `postings.py` (positions over sections), `store.py:compact_plan` (keep each item's version chain), `knowledge_ingest.py` (paths, sections, every record, every readable format, the index scope), `knowledge_api.py` and `knowledge_routes.py` and the brain's tools (caller, counts, the UDS calls), `KnowledgeView.tsx` (counts once), `cards.py` (through the gate).

**Dropped:** SQLite and every SQL statement; the engine's restart limit and memory kill; the `chunk` kind and `chunk_text`'s line packing; hash keys (`doc_key`, `chunk_key`); the closed list of 16 kinds and the fixed edge table; invariant I8 (refer by ID); invariant I1 (the engine holds JARVIS's records and their history).
