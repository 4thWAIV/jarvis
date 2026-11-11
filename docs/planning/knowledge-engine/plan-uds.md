# Knowledge engine plan under UDS

Work item: Knowledge engine (`work-d4f52552897e`), piece `uds-requirements`. Written 2026-11-10; rewritten the same evening after Tim's revisions (below). Requirements: ./docs/planning/knowledge-engine/requirements-uds.md. Gap analysis: ./docs/planning/knowledge-engine/gap-uds.md.

## Status

Tim's feedback on the recalculated plan is incomplete; a second message follows. Nothing in this plan starts before it arrives. Decisions waiting on Tim: none; this plan asks only whether it is understood.

## Tim's answers (2026-11-10, package knowledge-engine-under-uds 1.0.0)

- `records-mirrored`: neither option. SQLite is replaced by the document engine and retired as part of this effort; JARVIS's records move into the engine and SQLite is no longer needed.
- `vectors-follow-writes`: vectors follow. A write is visible at once; its vectors are made just after; until then the item counts as waiting. This amends approved check 1 (`atomic-write`) to cover every stream but Embedding.
- `history-holds`: text and data per version. Each version keeps its data, extracted text, sections and the file's hash; the file's bytes are only the current file in his folder.

## Tim's revisions to the recalculated plan (2026-11-10, package knowledge-engine-plan-recalculated: "Change the plan"; incomplete, a second message follows)

1. UDS has Data and Content. Content (documents, files) stays in normal folders on disk (the physical folders JARVIS has now) and is indexed: changes found with the operating system's file-watching tools, plus backup scans that catch anything a watch missed.
2. JARVIS's SQLite tables move to documents, which means tables may collapse into fewer document types. Do not port them one-to-one into an awkward document database: redesign the records as documents.
3. Minimise CI builds and use one PR branch for the whole work, as on the One Channel item: one PR branch and one PR to main for the whole build; each round developed and tested locally on a child branch, merged into the PR branch and pushed only after the previous CI build passed; the next child branch continues while CI runs; if CI fails, the PR branch is fixed and reconciled with the child branch before the next push. Three pushes, three CI builds in all.

## How the plan reads his revisions

### Content stays on disk; the engine indexes it

Content is the files in the folders on this Mac. The engine never holds a copy of a file's bytes. For every file under a watched folder, the engine holds at the file's path:

| Stream | What the engine holds |
|---|---|
| PathEntry | The path, its parent, its type suffix, the folder it belongs to, the file's size, modified time, inode and SHA-256. |
| Document | Data about the file: title, language, headings, page count, format, and any fields JARVIS or Tim add. |
| Content | A reference to the file on disk, plus its extracted text and its sections (the text is what the indexes need; the bytes stay in the folder). |
| Embedding | One whole-item vector and one per section, made just after the write. |
| AccessRights | The rights set on the folder's path, inherited by the file. |
| History | Each version's data, extracted text, sections and file hash (his `history-holds` answer). |

Which folders hold Content:

- Every folder registered in the Folders view (the physical folders JARVIS has now), under each project folder's saved index scope.
- JARVIS's own folders on disk: the brain's notes, journal, memory and topics; the needs boards; the package folders; the answers folder; received files once downloaded.
- Logs that only grow and are read whole are written as files and indexed as Content: a run's output stream (today `run_events`, 6.6 MB, up to 109 KB an event) as `data/runs/<id>.jsonl`, and voice samples and voice events (today 178,288 rows) as one file a day, `data/voice/<date>.jsonl`, deleted after 7 and 30 days.

How changes are found:

- **Watch.** One FSEvents stream over all watched folders, through `ctypes` on CoreServices (no new dependency). The engine records the last event ID it handled, so after a restart FSEvents replays what changed while it was away. An event that says changes were dropped or coalesced ("must scan subdirectories") triggers a scan of that subtree.
- **Backup scan.** A stat-only walk of every watched folder compares size, modified time and inode with each PathEntry: at startup, after the Mac wakes, and every 6 hours. Git-ignored paths are skipped. A changed file is read and hashed; a file gone is deleted from the engine with all its streams.
- **Pace.** Finding a change and writing its PathEntry is cheap and never waits. Reading text, splitting sections and embedding are the background lane and pause while Tim talks.

Today, by contrast, ingest has no watcher: it re-walks every folder every 10 minutes (`knowledge_runtime.py:INGEST_EVERY_SEC`), compares a ledger of size and modified time (`knowledge_ingest.py:Ledgered`), and reads only Markdown.

### JARVIS's records become documents, redesigned

JARVIS's records are Data. The 76 data tables (88 less 12 that only hold schema versions) become 18 document types. Three rules decide the collapse:

- **One record, one document.** A thing JARVIS tracks (a Work item, an agent, a message, a folder) is one document holding its current state and its small children inline (a Work item's pieces and decisions; an agent's keys and rights).
- **Events become history.** Tables that log each change of a record (`work_events`, `workflow_events`, `package_inbox_events`, `project_events`, `attention_events`, `agent_key_events`, `folder_location_events`, `language_document_revisions`) become that document's versions. Each version records who wrote it (agent, person, model), what kind of change it was and its summary, as the UDS design requires of every write.
- **Logs become Content.** Append-only streams read whole are files on disk (above).

| Document type | Path | Today's tables collapsed into it | Why |
|---|---|---|---|
| `work` | `/work/<id>.work` | `work_items`, `work_pieces`, `work_decisions`, `work_events` (history), `works`, `work_projects`, `work_folders` | A Work item is one record; its pieces (about 6 an item) and decisions belong to it, and every same-transaction rule becomes one document write. `works` is already being folded into Work. |
| `workflow` | `/workflows/<id>.workflow` | `workflows`, `workflow_items`, `workflow_open`, `workflow_events` (history) | Phases, tasks and open waits are parts of one workflow. |
| `package` | `/packages/<inbox id>.package` | `package_inbox`, `package_inbox_events` (history) | One package, its states as versions. |
| `run` | `/runs/<id>.run` | `runs`; `run_events` becomes the Content file `data/runs/<id>.jsonl` | The run's state is data; its output stream is a log. |
| `agent` | `/agents/<session id>.agent` | `agents`, `agent_keys` (agent keys), `agent_rights`, `agent_key_events` (history), `agent_author_proofs`, `steers` | An agent, its keys, rights, proofs and the keystrokes and commands sent to it are one record. |
| `message` | `/agents/<session id>/messages/<id>.message`, `/people/<github id>/messages/<id>.message` | `messages`, `session_messages`, `human_messages`, `human_drafts`, `human_approved`, `human_sends`, `human_preflight`, `human_owed`, `human_receipts`, `human_openability`, `human_review`, and the conversation log's `to_agent`, `from_agent`, `answer` and `message_in` items | One message from draft to approved, sent, delivered, answered and read; each step a version. Today the same message is written in up to five tables. |
| `thread` | `/people/<github id>/threads/<id>.thread` | `human_work`, `human_commands` | A thread with a person and its work state. |
| `turn` | `/conversation/<date>/<id>.turn` | the conversation log's `heard`, `typed`, `capture`, `jarvis` and `notice` items | What Tim and JARVIS said; messages are their own documents and the thread is a query over both. |
| `file` | `/people/<github id>/files/<id>.file` | `messaging_files`, `attachment_locations`, `attachment_leases` | A received or sent file and where it is. |
| `folder` | `/folders/<folder id>` | `folders`, `folder_locations`, `folder_location_events` (history), `folder_access_scopes`, `folder_registrations`, `folder_history_files` | The folder's own path holds its data; its files are the Content under it. |
| `project` | `/projects/<slug>.project` | `projects`, `project_aliases`, `project_folders`, `project_bindings`, `project_events` (history), `accomplishments`, `accomplishment_evidence` | A project, its names, its folders (as reference links) and its accomplishments. |
| `proposal` | `/proposals/<kind>/<id>.proposal` | `folder_candidates`, `folder_observations`, `project_candidates` | Anything waiting for Tim's yes, with its evidence. |
| `attention` | `/attention/<kind>/<source id>.attention` | `attention_items`, `attention_events` (history), `attention_presentations`, `human_announcements` | One update and every time it was shown. |
| `digest` | `/attention/digests/<id>.digest` | `attention_digests` | One spoken digest across many updates. |
| `hold` | `/keys/holds/<subject>.hold` | `agent_key_held`, `agent_key_errors` | What an unknown key tried to send, and the error package about it. |
| `langdoc` | `/language/<id>.langdoc` | `language_documents`, `language_document_revisions` (history), `language_publications` | One document, its revisions as versions. |
| `usage` | `/usage/<brain>/<date>.usage` | `usage_readings`, `usage_hourly` | One day's readings per brain; hourly figures are a query. |
| `settings` | `/settings/<area>.settings`, `/people/<user>/settings/<area>.settings` | `attention_settings`, `agent_key_settings`, `legacy_admissions`, `folder_discovery`, `human_sync`, `voice_input_settings` | Small sets of values, one document per area or person. |

Not a type: `voice_samples` and `voice_events` (Content files), `run_events` (Content file), `needs_board_writers` (the proven writer becomes a field on the needs board file's Document), `human_switch` (read by nothing), the 12 schema tables. Unchanged: `codex_sessions.py` still reads Codex's own SQLite file, which belongs to Codex.

Transactions across documents remain in one place: `wait_links.settle` ends waits on `work`, `workflow` and `package` documents in the same transaction as Tim's answer. Everything else that was a multi-table transaction is now a single document write.

### One PR, three rounds, three CI builds

One PR branch, `feature/uds-engine`, and one PR to main. Each round is a child branch, developed and tested locally with the tests it touches, merged into the PR branch and pushed only after the previous CI build passed. The next child branch starts while CI runs. A CI failure is fixed on the PR branch and reconciled with the child before the next push. Three pushes, three CI builds; a failure on the third build costs one more.

Because the whole move lands in one merge, the cut-over happens once: on the first start after the merge, JARVIS copies `jarvis.db` into the engine, verifies it, and runs on the engine. `jarvis.db` is kept untouched beside the engine's backups.

## Engine changes still needed

- **Always up.** The engine starts with JARVIS, before anything else; JARVIS waits for it before serving the page; the supervisor restarts it at once and never gives up; Diagnostics shows it down while it is. `JARVIS_KNOWLEDGE` then governs only ingest, embedding and the knowledge tools.
- **Two lanes.** Record reads and writes and change detection run at normal priority and are never paused. Text extraction, sections, embedding, compaction, the linker and cards are the background lane and pause for the conversation and the CPU. The process no longer runs at nice 10; the background threads are lowered on their own.
- **Documents of any shape at paths** (one path per item, open types from the suffix, reference links from any field holding a path, hierarchy links from the paths).
- **Transactions with conditions.** Several documents in one atomic write (built), plus conditions checked under the commit lock: a document's expected version, or a path not yet taken.
- **Versions.** Every write is a version of the item, tagged with who, what kind and a summary; `get(path, version)`, history, diff and `get_at(path, time)`; compaction keeps version chains. Versions of a document are stored as compressed blocks of similar documents; a Work item with 1,592 changes keeps 1,592 versions.
- **Queries records need.** Ordered scans by path prefix and by any field with a limit and a cursor, counts and grouped counts, and a change feed (every write since a log number) for anything that today reads "events after".
- **Content.** The FSEvents watcher and backup scan, sections for Markdown, text, PDF, Word, Excel, PowerPoint and HTML (through JARVIS's existing readers), whole-item and section vectors, exact phrase search.
- **Rights.** AccessRights on paths, data and content rights apart, hidden fields, the gate inside every call; JARVIS's brain and runs read as Tim.
- **Never dropped.** Rebuild rebuilds indexes, vectors and cards only. A snapshot is taken every day and before the cut-over.
- **Fast reads.** JARVIS keeps in memory what it reads on every request or second (agent keys and rights for the web gate, voice and attention settings).
- **Back to SQLite.** `python -m jarvis.knowledge export-sqlite` writes today's tables back from the engine through the same mapping, so the move can be undone; the round trip SQLite to engine to SQLite is a test.

## The three rounds

| Round | Delivers | Tested by | Agent hours | CI build |
|---|---|---|---|---|
| `engine-core` | The UDS interface and the ten acceptance checks as a suite; one path per item with PathEntry; documents of any shape; reference and hierarchy links with three origins; always up with two lanes; transactions with conditions; versions with history and `get_at`; ordered scans, counts and the change feed; snapshots and rebuild that never drops records; the in-process engine for tests. | The acceptance checks for paths, atomic writes, delete, history; transaction conditions under concurrent writers; restart and log recovery measured on a copy of today's data; the lanes (a held background lane never delays a record write). | 13 (`uds-interface` 1.5, `path-keys` 2, `open-documents` 1.5, `always-up` 2, `record-transactions` 1.5, `record-queries` 1.5, `item-history` 2, `test-engine` 1) | 1 |
| `content-and-search` | The FSEvents watcher and backup scan over registered folders and JARVIS's own folders; Content with extracted text and sections in every readable format, honouring index scope; whole-item and section vectors with one hit per item and counts once; exact phrase search; AccessRights and the gate; the top-level folders' document counts in the Folders view; the copy, verify and export-sqlite kit. | The acceptance checks for separate rights, one hit per item, exact phrase, gated links, hidden fields, AI rights, counted once; a watcher test on a temporary folder (create, change, rename, delete, dropped events) and a backup scan that finds a change made while the watcher was stopped. | 13 (`folder-watch` 2, `content-sections` 2, `item-vectors` 1.5, `phrase-search` 1.5, `rights-gate` 2, `folder-counts` 1, `move-kit` 3) | 2 |
| `records-as-documents` | The 18 document types; the 16 store modules rewritten over them, keeping their functions and callers; runs' output and voice samples written as Content files; the cut-over at first start; SQLite deleted (`run_store._connect`, `run_store.migrate`, `migrations/`); the test suite moved to the in-process engine. | Every store's existing tests against the engine; the round trip SQLite to engine to SQLite on a copy of today's database, equal table by table; the cut-over on that copy; the full suite and page tests on CI. | 20 (`work-workflow-package` 4, `agents-keys-settings` 2.5, `messages-threads-turns-files` 4, `runs` 1.5, `folders-projects-proposals` 2.5, `attention` 1.5, `language-usage-voice` 2, `retire-sqlite` 2) | 3 |
| **Total** | | | **46 agent hours** | **3 CI builds (about 2 hours of CI)** |

Start to merge: each round's phases run as parallel subagents on its child branch, so a round takes about 5 to 7 hours of wall time, and CI runs while the next round is built. About 20 hours from start to the PR ready for his merge vote, plus the time each round waits on the CI build before it.

Compared with the previous plan (51 agent hours, 30 CI builds, ten PRs): 5 agent hours fewer (one move kit and one cut-over instead of eight moves with write-back; no event sequences), 27 CI builds fewer, one PR.

## Risks

- **One merge moves everything.** The cut-over runs once for all 16 stores. It is rehearsed on a copy of today's database in round 3, and `jarvis.db` stays untouched with `export-sqlite` to go back.
- **One process holds everything.** While the engine restarts, nothing reads or writes Work, messages, runs or settings; log recovery time is measured in round 1. Voice settings and the attention policy are held in memory so listening and speaking never wait on the engine.
- **Read latency.** About 1 ms a call over loopback against SQLite's tens of microseconds; the Work tab and package views read one document each instead of joining tables.
- **Versions grow.** Keeping every version of busy documents (a Work item, an attention item) grows the store; compaction stores them in compressed blocks, and the growth is measured on today's data in round 1.
- **Watcher gaps.** FSEvents can drop or coalesce events and does not cover a folder on a disconnected drive; the backup scan and the replay from the last event ID cover both.
- **Power loss.** The log is fsynced at each commit and fully flushed every 10 seconds, the same exposure as SQLite on macOS; a commit carrying Tim's answer or approval is fully flushed before it is acknowledged.
- **Round 3 is the largest.** 20 agent hours in one round and one CI build; a failure there costs a fourth build.

## What happens to today's code

- **Kept:** the write-ahead log, segments, manifest, snapshots, the store's point-in-time reads, the inverted index and its predicates, the index advisor and his index pins, the vector files, Accelerate scan and stale-vector masks, the e5 model, its tokenizer and install, the embedding queue, BM25 scoring, the loopback server and token, topics, link proposals, seeds, the context pack, and every store module's functions and callers.
- **Reworked:** types, document checks, the graph, the query, transactions, compaction, the vector and full-text indexes, the supervisor and pace, ingest (watcher, sections, formats, scope), the API, routes and brain tools, the Knowledge view, cards, and the inside of all 16 store modules.
- **Dropped:** SQLite and every SQL statement; the 76 tables as tables; the `chunk` kind and the line packer; hash keys; the closed kind list and fixed link table; the 10-minute re-walk as the only way changes are found; invariants I8 and I1; the engine's restart limit and memory kill.

## The package "Document database gap and chunking fix"

Tim answered its question ("build the fix as proposed") with Change it on 2026-11-10 at 14:48, with no note. This plan replaces its fix. It can be set aside.

## Assumptions and open points, until his second message

- Content covers JARVIS's own folders (notes, needs boards, packages, answers, received files) as well as the registered folders.
- Logs read whole (run output, voice samples) become Content files rather than documents.
- Event tables become each record's history, not documents of their own; lists of recent activity across records use the change feed, not a history index.
- Collapsing every message into one `message` document carries the One Channel design ("every message to and from Tim in one record") into the engine; One Channel's own plan has to agree.
- The single PR covers all of it, so the cut-over happens at one merge, with `export-sqlite` as the way back instead of a write-back per stage.
- The backup scan runs at startup, after wake and every 6 hours.
- Files that start JARVIS (`.env`, tokens, the node ID, `team-connection.json`, `language.json`) stay files and are not indexed: the engine needs some of them before it starts, and secrets never go in it.
