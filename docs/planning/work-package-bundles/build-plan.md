# The knowledge engine: build plan

Status: the owner chose to build the knowledge engine in-house. This plan
says how it is built, in what order, what each phase delivers and how each
is tested. It follows the design in `design.md` (branch
`docs/work-package-bundles`), above all section 7 (the engine), section 7.17
(the document store) and section 19 (phases), and the phase 0 measurements
(branch `docs/knowledge-phase0`, commits 5a16a2f and 633d408).

Branch `feature/knowledge-engine`, cut from `feature/voice-capture-cues`.

## What is decided

Every sub-decision of the design's decision tree was decided as recommended:
build the engine, in standard-library Python with Accelerate through
`ctypes`, every part built (no adopted library), loopback TCP with its own
token, records of truth in SQLite, asserted links in Markdown front matter,
the node ID backed up, the proposed indexing scope, topics as a fourth note
folder, run context off until evaluated, and the package decisions as
recommended.

One question is still open with the owner: whether to add onnxruntime and
multilingual-e5-small as the embedder. Until it is answered the embedder is
built only as an interface (`jarvis/knowledge/embedder.py`) with a
deterministic fake for tests, and no dependency is added. The vector part
stays in the design and is built; it has nothing to search until an
embedder is chosen.

Phase 0 changed two things this plan relies on. Lexical search plus the
graph is the best retrieval measured (hit@10 0.525), so the keyword index
and the graph are built before anything that depends on vectors, and
search works without them. The Accelerate exact scan meets its budget warm
(16 ms p50 at 100,000 rows) and misses it cold (489 ms), so the deadline
and the partial-result fallback are part of the vector work, not an
afterthought.

## The invariants, and where each is held

- I1, no unique truth: the engine stores only derived documents; every test
  of recovery accepts losing a tail because ingest sends it again.
  `python -m jarvis.knowledge rebuild` drops the directory.
- I2, files only through JARVIS's policy: the engine opens nothing outside
  its own directory. Ingest (phase 4) runs in JARVIS's code. A test lists
  the engine process's open files.
- I3, deadlines: every client call has one; the query takes `budget_ms` and
  answers partial past it.
- I4, never compete with hearing: the engine runs at `nice 10`; compaction
  runs in a child at background priority, stopped with `SIGSTOP` on
  `/v1/pace hold` and continued on `free`.
- I5, other people's words walled: every document carries `_trust`; every
  hit returns it.
- I6, nothing leaves the Mac: the engine listens on 127.0.0.1 only.
- I7, write before hint: the engine acknowledges a transaction only after
  its log write; hints come from JARVIS, not the engine.
- I8, refer by ID: kinds declare their properties; nothing else is stored.
- I9 and I10 belong to JARVIS's side (proposals, resolving references) and
  arrive with phases 1, 2 and 4.

## The phases

The design numbers its phases 0 to 9. Phases 1 and 2 (projects, references,
context from records) do not depend on the engine and can be built beside
it. This branch builds phase 3 and the parts of phases 4 and 5 that do not
depend on the embedder answer, in the order the owner asked for.

### Step 1: the process and its loopback API

Delivers `jarvis/knowledge/` with `python -m jarvis.knowledge serve`: a
`ThreadingHTTPServer` on 127.0.0.1 on a port it chooses, the address record
`knowledge-address.json` (scheme, host, port, pid, boot ID; 0600), its own
token from `data_paths.ensure_knowledge_token()`, one gate
(`jarvis/knowledge/auth.py`: bearer token, loopback `Host` on its own port,
any `Origin` refused, no CORS), the writer lock `knowledge/LOCK` held with
`flock`, exit when its stdin pipe closes, and `nice 10`. On JARVIS's side,
`jarvis/knowledge_client.py`: the transport, a deadline on every call, the
check that the address file's pid is the child it started before the token
is sent, and the supervisor (start with a pipe, health every 5 s, restart
after 1, 5 and 30 s, left down after three restarts in 10 minutes, the
physical footprint read with `proc_pid_rusage`).

Tests: the gate refuses no token, a wrong token, an `Origin`, a foreign
`Host` and a wrong port; a second engine on one directory refuses to start;
closing stdin ends the engine and frees the lock; the client refuses to send
its token to an address whose pid is not its child; the engine imports
nothing from `jarvis.server`.

### Step 2: the log, segments, manifest, snapshots, compaction and forget

Delivers the storage core: the write-ahead log (`u32 length | u32 crc32 |
u64 lsn | u8 type | payload`, 64 MiB files, group commit within 5 ms,
`F_FULLFSYNC` barriers every 10 s with `mark` records), the memtable of
versioned values, segments (header `KSEG`, blocks of at most 64 KiB with
their own CRC, compressed when that saves a quarter, a footer with a sparse
index, a bloom filter and the LSN range), the manifest with every file's
SHA-256 and `CURRENT` swapped by write, `F_FULLFSYNC`, rename and folder
fsync, recovery (only committed transactions, a bad record past the durable
mark is tail, one at or below it is corruption), reads at a snapshot LSN,
snapshots as hard links, size-tiered compaction over immutable inputs, and
forget (tombstone, flush, rotate the log and delete what lies below the
checkpoint, compact every segment holding it, delete snapshots holding it).

Tests, in process on temporary folders: every acknowledged LSN survives a
reopen; an uncommitted transaction never appears; the log truncated at
every byte of its last record recovers; a bad record past the durable mark
is cut as tail even with good records after it; one at or below the mark
reports corruption; a corrupt manifest falls back to the previous one; a
read at an old LSN sees the old version; compaction keeps every live
version and drops shadowed ones below the oldest pin; forget leaves no byte
of the content in any segment, log file or snapshot.

### Step 3: the document store and its general index

Delivers documents (section 7.17.1): a key, a kind, system properties with
the reserved `_` prefix, an ordinal given on first write and logged, and the
kinds' schemas in `jarvis/knowledge/kinds.py`. The general index (section
7.17.2): a field dictionary, every property path indexed (nested paths,
array elements under `name[]`), values encoded so byte order is value order
(type tag, order-preserving floats, dates as epoch milliseconds, strings
folded and cut to 128 bytes), posting lists in fixed-width blocks of 128
ordinals, structural indexes (key, kind, owner and namespace, trust,
source), per-kind opt-out (chunks index only their system properties,
`language` and `heading_path[]`), and probe-and-intersect planning from the
smallest posting list. A reference property is written as a graph edge, not
kept as a property.

Tests: equality, range and nested and array predicates answer the same as a
scan of every document; a changed or deleted document drops out of the
index at once and after a flush and a compaction; the planner starts from
the smallest list.

### Step 4: the typed graph

Delivers edges (section 7.4): every edge kind with its allowed source and
target kinds, enforced on write; origin, weight, source digest and LSN on
each; the forward and reverse files; and the bounded walk (a start key, at
most four steps, each an edge kind, a direction, node kinds, property
filters and a limit, with `max_nodes` and `budget_ms`). Vectors refer to
documents by ordinal; a document reaches its vectors through a per-space
map rebuilt at start.

Tests: an edge between kinds the table does not allow is refused; a walk
follows both directions and respects every bound; a two-hop walk on the
synthetic corpus meets 10 ms p95 (marked `knowledge_engine`).

### Step 5: the keyword index

Delivers BM25 (k1 1.2, b 0.75) over `spoken_words.folded_words`, with
`jarvis_memory`'s stop words and a Spanish list, postings in fixed-width
blocks of 128 ordinals and 128 term frequencies with a skip entry per block
(last ordinal, largest frequency), and top 200 candidates with a WAND-style
upper bound per term.

Tests: the ranking equals a brute-force BM25 over the same chunks; accented
and unaccented words match; a changed chunk's old terms stop matching.

### Step 6: the exact vector scan, and the embedder interface

Delivers vector spaces per embedder ID (`.f32` rows and `.ids` with ordinal,
property and LSN), the exact scan through Accelerate (`cblas_sgemv`, the
mask by `vDSP_vadd` of a penalty vector, exact top-k from `vDSP_maxvi`
block maxima), a pure-Python reference behind the same seam, the vector
deadline with partial results, `madvise(MADV_WILLNEED)` warm-up, and the
`Embedder` interface with its `RUNNER` seam and a fake that hashes words
into deterministic vectors. No real embedder is wired.

Tests: the Accelerate scan and the reference return the same top-k, with
and without a mask; a deleted or replaced document's row never returns;
the fake gives the same vector for the same text; with no embedder a query
returns lexical and graph results and says vectors were not used. One test
marked `accelerate` runs the real framework.

### Step 7: the cross-engine query

Delivers `POST /v1/query` (section 7.17.5): `where`, `walk`, `match` and
`near`, each a set of ordinals, the smallest run first and applied to the
rest, reciprocal rank fusion, every hit with its key, source, trust,
namespace and `why`, read at one snapshot LSN, and partial past the budget.

Tests: one request crossing all three parts returns the same as the
clauses intersected by hand; a filter never lets a refused document through
the vector mask.

### Later phases, unchanged from the design

- Phase 4's remainder: ingest adapters in a child, change feeds and
  watermarks, chunking, the linker, cards, importance, the cache tiers,
  `HotCards`, the query shape log and the index advisor, the brain's tools,
  KnowledgeView, and wiring the supervisor into the server.
- Phase 5's remainder: a real embedder, spaces with canaries, hybrid
  fusion with `query_en`, re-embedding on a model change. **This waits on
  the embedder answer.**
- Phases 6 to 9: local packages, prepare to send, receive and resolve,
  hardening (IVF at scale, recall alarm, rebuild timings).

## What waits on the embedder answer

- Any real embedding: model files, their install step, the canary, the
  spaces' IDs, ingest's embedding queue in production.
- Hybrid ranking with vectors in it, and the gold-set bar for it.
- The IVF build and `nprobe` tuning on real vectors (they need real rows to
  measure recall).
- Ingest and rebuild time budgets, which are bound by embedding cost.

Nothing in steps 1 to 7 waits on it: the vector scan, its files, its mask
and its deadline work the same whatever produces the rows.

## Routine choices made here

- Unit tests of the formats run in process on temporary folders in a bare
  `pytest`; tests that start the engine process, the crash and the budget
  tests carry the marker `knowledge_engine` and are deselected by default.
  Reason: CI checks the format logic, and a bare run never starts a process.
- The compaction child writes new segments and a result file; only the
  engine changes the manifest. Reason: one writer of `CURRENT` means a
  flush and a compaction can never race.
- The knowledge token lives at `data_dir()/knowledge-token`, beside the
  address file. Reason: it is held only by the server, never in the brain's
  home.
- `JARVIS_KNOWLEDGE` stays off by default, and the server does not start
  the engine until ingest exists. Reason: an engine with nothing in it costs
  memory and gives nothing.
- Each segment family (documents, index, forward edges, reverse edges,
  postings) is its own file under one generation number, written together
  under one manifest change. Reason: the design names one file per family,
  and one generation keeps them consistent.
- The engine keeps one resident column, `doc_src`, giving the generation
  that holds each ordinal's newest version. An index or posting entry counts
  only when it comes from that generation. Reads at an older LSN check each
  candidate against the document itself. Reason: stale entries in older
  segments are never trusted, without logging index entries.
- Ordinals are given by the log writer and written in the `doc_put` record.
  Reason: recovery replays the same ordinals.
- The vector maths has two implementations behind one seam, Accelerate and
  a pure-Python reference; tests use the reference unless marked
  `accelerate`. Reason: the result must not depend on which ran.
- Until an embedder is chosen the engine has none; a `near` clause is
  skipped and the answer says so. Reason: no dependency without the owner's
  yes.
- The Spanish stop words live in the keyword index module beside
  `jarvis_memory`'s English ones. Reason: they are used nowhere else.
- Only open reads pin versions in the live segment set; a snapshot keeps
  its versions in its own hard-linked files. Reason: no snapshot can hold
  compaction back, and nothing a snapshot holds is lost.
- One LSN names one transaction, and every record in it carries that LSN.
  Reason: a read at an LSN then sees whole transactions without a second
  number.
- The supervisor decides whether an earlier engine is still there by the
  writer lock, not by a health call. Reason: the token is never sent to a
  process this server did not start.
- Forget overrides pins: every version of a forgotten key goes, even one a
  read has pinned. Reason: content that must go cannot wait for a reader.
- The structural indexes (kind, owner, namespace, trust, source) are the
  general index's entries on the system properties, which every kind
  indexes even when it opts out of the rest. Reason: one posting format and
  one planner serve both.
- String values fold accents but keep case, as `spoken_words.fold` does.
  Reason: keys, paths and names differ by case; accents are what speech
  loses.
- The planner decodes only the smallest posting list and checks every other
  condition on the documents it names. Reason: a large list is never
  decoded, and every answer is checked against the document anyway.
- A read at an older LSN checks every document as it was rather than using
  the index, which describes the newest documents. Reason: such reads are
  rare (exports) and must be exact.
- The compaction plan names the parts it builds, and the child builds
  exactly those. Reason: the child must make the same files the engine
  would.
- An edge to a document that does not exist is refused, and a reference
  property is checked against the edge table like any edge. Reason: the
  graph never holds a dangling edge, so a walk never meets one; ingest
  writes a parent before or with its children.
- `possibly_same` may be written only with origin `extracted`. Reason: it
  is a proposal, never an asserted link.
- Until the cache tiers arrive (phase 4), a walk reads edges and documents
  through the block pool; the two-hop walk measured 6.2 ms p95 on the
  synthetic corpus at a load of about 11. Reason: it already meets its
  10 ms budget, so resident adjacency can wait for importance scores.
- A term's document frequency is read from its posting counts, which
  count a superseded chunk until compaction merges it away. Reason: an
  exact count at start would mean reading every chunk's text; the ranking is
  exact after a compaction and over-counts only by unmerged updates.
- Keyword search reads the newest state, not an older LSN. Reason: its
  postings describe the newest chunks; an export reads documents, not
  rankings.
- Only chunk text is in the keyword index. Reason: text bodies belong to
  chunks only (I8); labels and properties are in the general index.
- Vector rows are logged like any write, and each generation's rows are
  two files the vector part writes itself (`.f32` and `.ids`), not
  key-value segments; the store treats such a family as external. Reason:
  the rows are stored once, in the form the scan maps.
- A document whose embedded text changes has its rows deleted in the same
  transaction and is embedded again in the background. Reason: a vector
  never answers for text it was not made from.
- A kind embeds at most one property. Reason: the per-space map then holds
  one row per ordinal, 8 bytes, as the design's columnar rule asks.
- A filter larger than `GATHER_MAX` (4,096) documents is applied as a
  per-row mask built in Python; a smaller one gathers its rows and scores
  only those. Reason: both are exact; the mask's cost is measured once real
  vectors exist.
- A query with `at` may use only `from` and `walk`; `match` and `near`
  read the newest state. Reason: rankings are built over the newest
  postings and rows, and an older read must be exact.
- A `where` clause naming more than 20,000 documents is checked on the
  ranked candidates instead of being turned into a filter. Reason: building
  a filter that large costs more than checking the 200 candidates.
- The open-files test allows the code, system libraries, mapped
  libraries and a library's own anonymous temporary files beside the
  engine's directory. Reason: those are what loading Python and Accelerate
  opens; none of them is the user's data.
- The runtime holds the engine while the conversation floor is anything but
  idle, JARVIS's own speech included, and when the floor cannot be read.
  Reason: hearing comes first, and a doubtful reading is treated as busy.
- The server-side runtime lives in `knowledge_runtime.py`; `server.py` only
  starts and stops it and passes its health to Diagnostics. Reason: the
  rule is tested without the server.
- The Knowledge row is shown while the engine is switched off, as idle and
  "Switched off". Reason: Diagnostics lists every part of JARVIS, and the
  design names this row's idle state.
- Ingest watermarks are kept in the engine as their own logged family and
  written in the same transaction as the documents they describe, rather
  than in the manifest. Reason: a crash then keeps both or neither, and the
  manifest changes only at a flush.
- Messages to and from sessions are read from the conversation log, which
  already holds them, not a second time from their tables. Reason: one
  passage per message.
- Each conversation item is its own chunk of a per-day document, not one
  chunk per exchange. Reason: his turn and JARVIS's may differ in trust.
- Until projects with an index scope exist (phase 1), every registered
  folder read under the FILES reader is ingested by the design's Markdown
  rule. Reason: the engine is off by default, and the rule already keeps
  source code and history folders out.
- Passes run every ten minutes and five seconds after one that left more;
  hints only bring the next pass forward. Reason: the floor decides when,
  and a timer is enough until the brain's tools ask for freshness.
- A pass left behind by a server that went away is killed at the next
  start only when `ps` shows it is an ingest pass. Reason: a recorded pid
  may have been reused.
- The linker runs after each ingest pass over every topic, finding its
  candidate chunks through the keyword index, rather than on each chunk as
  it is written. Reason: an alias change then relinks everything at once,
  and the linker never reads every chunk.
- Proposals are of two kinds, `about` (a document to a topic) and
  `same_as` (two topics sharing a name or alias); an ambiguous alias alone
  is evidence for an `about` proposal, not a proposal of its own. Accepting
  writes `about_sources` or `same_as` in the topic's front matter; turning
  one down writes `not_linked`. Reason: every accepted link is then one he
  can read and edit, and chunk-level links would be noise in his files.
- Proposals are attention items that are shown and never spoken, and are
  resolved when they go away. Reason: they wait for his yes and are not
  news.
- Until projects exist (phase 1), topics link only to topics and documents.
  Reason: there is no project record to name.
- Recency in the importance score is measured in transactions, not days:
  how far a document's last version is behind the newest LSN, halved every
  5,000. Reason: the engine keeps no clock of its own, and LSNs order every
  change across a restart.
- Use counts live in the engine's memory only and start again after a
  restart. Reason: they are a hint, not truth (I1).
- The resident tier holds cards, at most 2,000 and every pinned topic; a
  write drops the cards it touches and they are made again when asked.
  Reason: the two-hop walk already meets its budget through the block pool,
  so resident adjacency waits for a measured need.
- `ensure_tool_token` and `ensure_knowledge_token` share one function,
  `data_paths._ensure_token`. Reason: one safe way to make a secret file.

## Order and commits

Each step is committed once its tests pass, in the order above. Only the
targeted tests are run locally; CI runs the suite.

## Progress

Phase 4's second step is built: ingest in its own child, one pass at a
time, paced with the floor, from the notes, the conversation log, finished
runs, workflow events and registered folders' Markdown, with watermarks kept
in the engine and the pass shown on the Knowledge row.

Phase 4's first step is built too: the server starts the engine after
startup settles (only with `JARVIS_KNOWLEDGE` on), paces it by the floor and
the CPU, and Diagnostics has its Knowledge row.

Steps 1 to 7 are built on this branch, each in its own commit, with the
tests named in each step (`tests/test_knowledge_*.py`; the process, crash,
child-process and budget tests carry `knowledge_engine`). What comes next is
the rest of phase 4: the linker (alias edges and proposals), cards and
importance with the cache tiers, `HotCards`, the query shape log and the
index advisor, and the brain's knowledge tools with their page.
