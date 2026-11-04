# Open-source embedding models for JARVIS — research, 2026-10-30

Research only. No production code was changed. Nothing here is implemented:
today `jarvis_memory.search` ranks by counting query words
(`jarvis_memory._score`), and `history_read.search` is also keyword based.

## Decision (consolidated 2026-10-30)

**Do now:** nothing in production. Run a head-to-head on JARVIS data (below)
before choosing. Until it is run, no open model may be described as "as good
as Voyage".

**Shortlist for that test:** Qwen3-Embedding-0.6B (default candidate),
voyage-4-nano (new, same size class), harrier-oss-v1-0.6b, the current
keyword search as the baseline, and Voyage's hosted `voyage-4-lite` /
`voyage-4-large` as the reference. Add Qwen3-Embedding-8B and
harrier-oss-v1-27b only as a one-day rented-GPU check of whether the extra
size matters.

### Voyage AI (primary: docs.voyageai.com, Voyage blog, Hugging Face card)

| Model | Open weights | Context | Dims | API price per 1M tokens |
|---|---|---|---|---|
| voyage-4-large | No | 32K | 1024 default; 256/512/2048 | $0.12 |
| voyage-4 | No | 32K | same | $0.06 |
| voyage-4-lite | No | 32K | same | $0.02 |
| voyage-code-4 | No | 32K | same | $0.12 |
| voyage-4-nano | Yes, Apache-2.0 | 32K | 2048 default on the card; 1024/512/256 | not served by the API (secondary source) |

- 200 M free tokens per account (pricing page). Voyage-4-large claims to
  lead 29 RTEB datasets by NDCG@10: +3.87% over Gemini Embedding 001,
  +8.20% over Cohere Embed v4, +14.05% over OpenAI v3 Large (Voyage's own
  blog; it does not say whether the gaps are absolute or relative, and
  these are Voyage's numbers, not independent).
- The four voyage-4 models share one embedding space, so a cheap model can
  embed queries against documents embedded by a larger one. Docs say this
  includes voyage-4-nano. I have not tested it.
- **voyage-4-nano card:** 180M non-embedding plus 160M embedding
  parameters (about 0.3B, BF16), multilingual (languages not listed), query
  and document prompts, int8/binary options. It requires
  `trust_remote_code=True` and `transformers>=4.56`; loading runs code from
  the repo, so pin a reviewed revision. The card gives **no scores**, only
  "outperforms … `voyage-3.5-lite`" and a link to a spreadsheet I did not
  open.
- Cost is not the issue: a 10 M-token corpus (my assumption) costs about
  $1.20 on voyage-4-large, and the first 200 M tokens are free.
- **Privacy is the issue.** Voyage's FAQ: for its hosted API, data "may be
  stored and used for training by default". Opting out gives zero-day
  retention but needs a payment method and an organization admin, and the
  dashboard cannot undo it. The FAQ is silent on SOC 2 and on self-hosting.

### Closest open-weight models to Voyage-class retrieval

No published number compares them on one benchmark: Voyage reports RTEB,
the others report MTEB. So "closest" can only mean "highest on their own
cards", not parity.

| Model | Card score | Why it is on the list |
|---|---|---|
| harrier-oss-v1-27b (MIT) | 74.3 Multilingual MTEB v2 | Highest card score here; 54 GB of weights |
| Qwen3-Embedding-8B (Apache-2.0) | 70.58 multilingual; 75.22 English v2 | Established, 16 GB of weights |
| harrier-oss-v1-0.6b (MIT) | 69.0 Multilingual MTEB v2 | Best score at 0.6B |
| Qwen3-Embedding-0.6B (Apache-2.0) | 64.33 multilingual; 70.70 English v2 | Best-known small model |
| voyage-4-nano (Apache-2.0) | none published | Only open Voyage model; shares space with the API models |

Qwen3-8B vs Harrier 27B: 27B scores about 3.7 points higher on its card but
needs about 3.4× the memory (54 GB vs 16 GB, my arithmetic) and 5,376-dim
vectors (21.5 KB each vs 16 KB for 4096 dims). Qwen3-8B is the sensible
large model; take 27B only if the test shows 8B missing results.

### Hardware (from the tier table below)

| Model | Needs | Where |
|---|---|---|
| Qwen3-0.6B, harrier-0.6b, voyage-4-nano | 8–16 GB RAM, CPU is enough | The Mac (Intel i9, 64 GB) or Railway CPU |
| Qwen3-Embedding-8B | 24 GB GPU | External equipment (Tier 3) |
| harrier-oss-v1-27b | 80 GB GPU | External equipment (Tier 4) |

### Railway vs self-hosting

| | Railway | Own / external hardware |
|---|---|---|
| Models that fit | ≤0.6B on CPU; no GPU offered | Any, by tier |
| Cost | About $40–$80 a month for 1–2 vCPU (my estimate) | Hardware owner's cost; unknown |
| Operations | Already used for `team_server`; deploys, health check | You run drivers, updates, uptime |
| Availability | One replica if a volume is used; brief outage per deploy | As good as the owner makes it |
| Privacy | Text leaves the Mac to a third-party cloud | Stays on a machine you control if it is yours; not if it is Shane's or Matthew's |

Choose: the Mac for the 0.6B-class models (free, private). Railway only for a
team-shared index of content that may leave the Mac. External GPU
equipment only if the test shows 8B or 27B is worth it, and only after the
privacy question below is answered for that machine.

### Privacy

- Local Mac: nothing leaves it. This is the default for notes, transcripts
  and messages.
- Railway or someone else's GPU: the text being embedded, and every query,
  is readable by that host. `team_server` today holds no prompts or
  transcripts; keep it that way unless Tim says yes.
- Voyage API: stored and trainable by default; opt-out exists. Needs Tim's
  yes before any private text is sent. The eval can use non-private or
  synthetic text for the hosted reference.
- Cheaper compromise: embed on the Mac and send only vectors. Vectors can
  still leak meaning, so treat them as private too.

### Head-to-head evaluation (required before any parity claim)

1. Build a set of 50–100 real queries from how Tim uses `search_history`,
   `jarvis_memory.search` and session messages, each with the notes or
   transcript passages that should be found, labelled by hand.
2. Run each candidate over the same corpus with identical chunking, the
   documented query/document prompts, and 1024-dim vectors for fairness.
3. Report recall@5, NDCG@10 and query latency on this Mac, per model, with
   bootstrap confidence intervals, against the keyword baseline.
4. Treat two models as at parity only if their NDCG@10 intervals overlap
   and the gap is under a threshold Tim sets (my suggestion: 2 points).
5. Test the shared-space claim (voyage-4-nano queries against
   `voyage-4-large` documents) only if the API is allowed for that data.
6. Record the date, model revisions and corpus snapshot with the result.

Hold off on all adoption until step 3 exists. Each model needs a new
dependency, which needs Tim's approval.

## Initial recommendation (written before the Voyage and hosting work; superseded where it differs from the Decision above)

1. **Default: `Qwen/Qwen3-Embedding-0.6B`** (Apache-2.0, 1024 dims
   truncatable to 32, 32K context, 100+ languages, instruction-aware).
2. **Runner-up / lighter: `google/embeddinggemma-300m`** (768 dims, 2K
   context) if a smaller footprint matters more than quality.
3. **Not for JARVIS now:** the 8B and 27B models. JARVIS's corpus is a folder
   of Markdown files and transcripts; the extra points do not pay for a
   multi-GB model on a Mac that also runs speech recognition and a browser.
4. Re-check `harrier-oss-v1-0.6b` (MIT, newer, higher MTEB v2 score) before
   deciding; see "Open questions".

Fit with the project rules: no new dependency without approval. Any of these
needs one (`sentence-transformers` or `onnxruntime`/`llama.cpp`), so adopting
a model is a decision for Tim first. Run it as an optional re-ranker or
index builder behind the existing search, with keyword search as the
fallback, so nothing on a voice path waits for it.

## Comparison

Scores are from each model's own Hugging Face card (primary source), fetched
2026-10-30. "Mean (Task)". MTEB versions differ by column; do not compare
across columns.

| Model | Params | Dims | Context | License | MTEB Multilingual | MTEB Eng v2 | Code |
|---|---|---|---|---|---|---|---|
| Qwen3-Embedding-8B | 8B | 4096 (MRL 32–4096) | 32K | Apache-2.0 | 70.58 | 75.22 | n/r |
| Qwen3-Embedding-0.6B | 0.6B | 1024 (MRL 32–1024) | 32K | Apache-2.0 | 64.33 | 70.70 | n/r |
| harrier-oss-v1-27b | 27B | 5376 | 32K | MIT | 74.3 (v2) | n/r | n/r |
| harrier-oss-v1-0.6b | 0.6B | 1024 | 32K | MIT | 69.0 (v2) | n/r | n/r |
| harrier-oss-v1-270m | 270M | 640 | 32K | MIT | 66.5 (v2) | n/r | n/r |
| EmbeddingGemma 2 | 740M total, 270M text-only | 768 (MRL 512/256/128) | 8K | Apache-2.0 | 61.36 | 68.46 | 78.68 |
| EmbeddingGemma (1) 300m | 0.3B | 768 (MRL 512/256/128) | 2K | Gemma terms, gated | 61.15 | 69.67 | 68.76 |
| BGE-M3 | n/s | 1024 | 8K | MIT | n/r (images only) | n/r | n/r |

n/r = not reported on the card; n/s = not stated on the card.
The harrier card says "Multilingual MTEB v2" for all three sizes, so its
column is a v2 score, while the Qwen3 column is the older MTEB Multilingual
leaderboard. Those two are not directly comparable.

### Notes per candidate

- **Qwen3-Embedding** — Instruction-aware, MRL, code and 100+ languages.
  The 8B card says it ranked No. 1 on the multilingual leaderboard as of
  2025-06-05; that is stale. The 0.6B is the practical size.
- **harrier-oss-v1** — MIT. 94 languages. The 0.6B and 270M were distilled
  from larger embedding models. Queries *must* carry an instruction
  (`Instruct: {task}\nQuery: {query}`); the card's FAQ says omitting it
  degrades results. Documents take none. Last-token pooling. Release date is
  not on the card.
- **EmbeddingGemma 2** — Ungated and Apache-2.0 (the first version needed
  acceptance of Gemma terms). Multimodal (text, image, audio, video), but the
  text-only part is 270M. Card warns float16 is unsupported (use bf16/fp32).
  Its release date and the on-phone RAM figures (about 191 MB text-only, 567
  MB multimodal) come from secondary coverage, not the card.
- **BGE-M3** — Only model here that gives dense, sparse and multi-vector
  (ColBERT) retrieval from one model; MIT; 8K context. The card's benchmark
  numbers are images, so I could not quote them. Older and likely behind the
  others on quality.

## Hosting fit for this Mac

This machine (from `sysctl`): Intel Core i9-9980HK, 64 GB RAM. Intel, so no
Apple-silicon GPU/MPS; embedding runs on CPU. Rough weights size from
parameter counts (my arithmetic, not measured): 0.6B at bf16/fp16 is about
1.2 GB; 8B about 16 GB; 27B about 54 GB, which would crowd out everything
else. Only the ≤0.6B models are realistic. I did not benchmark speed; no
latency numbers in this document are measured.

JARVIS's corpus is small (Markdown memory, journal, transcripts), so
indexing once and re-embedding changed files is cheap even on CPU. Query
latency is one short embedding per search.

## Server-side deployment (added 2026-10-30)

"Server-side" here means a service next to `team_server/`, not the
per-Mac JARVIS. Nothing below is built or measured. Railway figures are from
Railway's docs, fetched 2026-10-30; sizes, costs and throughput labelled
"estimate" are my arithmetic.

### What Railway offers

| Fact | Value | Source |
|---|---|---|
| Per-service caps, Hobby | 48 GB RAM, 48 vCPU, 5 GB volume, 6 replicas | pricing/plans |
| Per-service caps, Pro | 1 TB RAM, 1,000 vCPU, volume up to 1 TB self-serve, 42 replicas | pricing/plans |
| Per replica, Pro | up to 24 vCPU and 24 GB RAM | reference/scaling |
| Volume default size | Hobby 5 GB, Pro 50 GB; resize up only | reference/volumes |
| Volumes | one per service; replicas cannot be used with volumes; a redeploy of a service with a volume has brief downtime | reference/volumes |
| Volume I/O | 3,000 read and 3,000 write IOPS at every size | reference/volumes |
| Price | RAM $10/GB-month, CPU $20/vCPU-month, volume $0.15/GB-month, egress $0.05/GB | pricing/plans |
| Subscription | Hobby $5 (includes $5 usage), Pro $20 (includes $20 usage) | pricing/plans |
| GPU | Not in Railway's pricing, scaling or volume docs. Forum posts say GPUs are not supported; those are not official, so treat as "no GPU available" unless Railway says otherwise | station.railway.com threads |

The plans page says caps "include replica multiplication", while the
scaling page gives 24 vCPU / 24 GB per replica on Pro. The two pages do not
say what a single replica may use on Hobby; I did not find it. Check before
sizing on Hobby.

### Estimates for a CPU embedding service on Railway

Billing is by use, and an always-on service pays for its allocation all
month (estimate, from the prices above):

| Allocation | Monthly cost | Fits |
|---|---|---|
| 1 vCPU, 2 GB | about $40 | EmbeddingGemma 2 text-only (270M) or harrier-270m, int8/bf16 |
| 2 vCPU, 4 GB | about $80 | Qwen3-Embedding-0.6B or harrier-0.6b (weights about 1.2 GB at bf16, plus runtime) |
| 8 vCPU, 16 GB | about $320 | headroom for batch indexing; 4B-class would barely fit and is slow |
| volume 5–50 GB | $0.75–$7.50 | model cache and vector index |

Reasoning:
- **CPU is the only option.** No GPU on Railway, so 0.6B is the practical
  ceiling; 8B (about 16 GB of weights) and 27B (about 54 GB) would need a
  very large, expensive CPU box and would be slow. Not recommended.
- **Persistent storage.** Put the model cache and the index on one volume. A
  volume forbids replicas, so the service is one instance with a short
  outage on every deploy. That matches the one replica `railway.json` already
  runs for the team server, but embedding queries then have no failover.
  Alternative: bake the model into the image (no volume, replicas allowed,
  larger image) and keep vectors in MongoDB.
- **Vector size.** A 1024-dim float32 vector is 4 KB, so 1M vectors is about
  4 GB (estimate); harrier-27b's 5,376 dims would be about 21.5 GB per
  million. MRL truncation (Qwen3: down to 32; EmbeddingGemma: 128) shrinks
  this at some quality cost, per the cards.
- **Cold start.** Loading a 1.2 GB model on each restart adds seconds to
  minutes; I did not measure it.
- **Privacy.** Embedding on Railway means message and note text leaves the
  Mac. JARVIS's rule is that nothing on a voice path waits for the team
  server, and `team_server` stores no prompts or transcripts. Do not move
  private text server-side without Tim's yes. Embed on the Mac and send only
  vectors, or run the service only for team-shared content.
- **Verdict.** Railway can host a small CPU embedding service (Qwen3-0.6B
  or smaller) for roughly $40–$80 a month. For a corpus this small, the Mac
  doing it locally costs nothing extra; Railway is justified only when the
  team needs a shared index.

### Tiers for externally hosted equipment (Shane or Matthew)

Their specifications are not given, so these are requirement tiers: pick
the highest tier the offered machine meets. Memory figures are weights at
bf16 (2 bytes per parameter) plus roughly 25% for activations and a cache,
my estimate. Where a card says bf16 or fp32 only (EmbeddingGemma), do not
run fp16.

| Tier | Hardware requirement | Model choice | Why |
|---|---|---|---|
| 0 — CPU only | 4+ cores, 8 GB RAM, 20 GB disk, no GPU | EmbeddingGemma 2 (270M text) or harrier-oss-v1-270m | Smallest; same class as Railway CPU |
| 1 — CPU, roomier | 8+ cores, 16 GB RAM | Qwen3-Embedding-0.6B (recommended default) or harrier-oss-v1-0.6b | Best quality that is practical without a GPU |
| 2 — Small GPU | 1 GPU with 8–12 GB VRAM, 16 GB RAM | Same 0.6B models, batched | GPU speeds indexing, not needed for single queries |
| 3 — Mid GPU | 1 GPU with 24 GB VRAM, 32 GB RAM | Qwen3-Embedding-8B (about 16 GB weights, about 20 GB with overhead) | Card: 75.22 English v2, 70.58 multilingual |
| 4 — Large GPU | 80 GB VRAM (or 2×48 GB), 128 GB RAM | harrier-oss-v1-27b (about 54 GB weights, about 68 GB with overhead) | Highest multilingual v2 score here (74.3); see caveat |

Notes for the tiers:
- Apple silicon counts unified memory like VRAM, but the speed will not
  match a data-centre GPU. Intel Macs (like Tim's) are Tier 0–1 only.
- Storage: model cache is 1–2 GB (Tier 0–2), about 16 GB (Tier 3), about
  54 GB (Tier 4), plus 4 KB per 1024-dim vector (21.5 KB at 5,376 dims).
- Tiers 3 and 4 buy a few MTEB points for 13–45× the memory of the 0.6B
  model. For JARVIS's corpus I would not pay that; they only make sense if
  the team hosts a large shared document set.
- Ask Shane or Matthew for: GPU model and VRAM, CPU cores, RAM, disk, OS,
  whether it is shared with other jobs, network path to the Mac, and who
  administers it. These decide the tier. Ask also whether text may leave the
  Mac for that machine (see Privacy above).
- Tier 4 depends on the unverified harrier quality claim; test Tier 1 first.

### Revised recommendation

Keep Qwen3-Embedding-0.6B as the default. Run it on the Mac first. If a
shared server index is wanted: Railway CPU (2 vCPU, 4 GB, one volume, about
$80 a month) for the 0.6B model, or Tier 1–2 external equipment. Use Tier 3
only if measured recall on real queries shows the 0.6B model missing.

## Licensing

All candidates are permissive: Apache-2.0 (Qwen3, EmbeddingGemma 2), MIT
(harrier, BGE-M3). Only original EmbeddingGemma carries Gemma terms and a
click-through gate, which is a reason to prefer version 2 or Qwen3.

## Multilingual

All shortlisted models claim 94–100+ languages. Tim's use is mostly
English; multilingual is a tiebreaker, not a driver.

## Caveats and open questions

- **Sources.** Model facts above are from the Hugging Face cards. Web
  roundups disagreed with each other on scores, ranks and dates and are not
  cited as evidence. I did not open the live MTEB leaderboard (it is a
  dynamic app); check it before committing.
- **Not verified:** harrier's release date and that "state of the art" claim
  today; EmbeddingGemma 2 release date (reported as 2026-10-06 by secondary
  sources only); any real speed or RAM on this Mac.
- **Not evaluated:** other families (Jina, Nomic, Snowflake Arctic, IBM
  Granite). Out of time; they could change the runner-up.
- **Decide with a local test, not leaderboards.** Build a small set of real
  JARVIS queries and the notes they should find, and compare Qwen3-0.6B,
  harrier-0.6b and the keyword baseline on recall@5 before adopting anything.

## Primary sources

- https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- https://huggingface.co/Qwen/Qwen3-Embedding-8B
- https://huggingface.co/microsoft/harrier-oss-v1-27b
- https://huggingface.co/microsoft/harrier-oss-v1-0.6b
- https://huggingface.co/google/embeddinggemma-300m
- https://huggingface.co/google/embeddinggemma-2
- https://huggingface.co/BAAI/bge-m3
- https://huggingface.co/voyageai/voyage-4-nano
- https://docs.voyageai.com/docs/embeddings
- https://docs.voyageai.com/docs/pricing
- https://docs.voyageai.com/docs/faq
- https://blog.voyageai.com/2026/01/15/voyage-4/
- https://docs.railway.com/reference/pricing/plans
- https://docs.railway.com/reference/volumes
- https://docs.railway.com/reference/scaling
- https://station.railway.com/feedback/gpu-support-56d19c42 (community, not official)
