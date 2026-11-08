# Phase 5: vector search, measured

Measured on 2026-11-07 on the Intel Mac (16 threads, 64 GB), load average
10 to 24 from other agents' work, so timings are upper bounds. What is
found does not depend on load: the quality runs are given all the time they
need (`evaluate.py`), and a search's real cost is timed apart.

The question set is phase 0's: 40 questions Tim asked JARVIS (25
**knowledge**: a decision, a finding, a cause, what was built; 15
**status**: what a named agent last said or is working on), each with the
material that answers it, over the same 8,604 chunks. Each question sees
only what existed before it was asked. The questions are still marked
`confirmed: false` until Tim has read them, and the fusion weights were
chosen on these same 40 questions, so the figures are a good guide, not a
held-out test. The questions and chunks stay outside the repository (his
words); only these aggregates are committed.

Every figure below is the knowledge engine itself (`query.run`), not a
model of it: its keyword index, its graph (`about`: the agents, people and
projects a question names, and what the records say they said, newest
first) and its vector index over multilingual-e5-small, installed through
`embedding_models.install` and embedded by the engine's own background
worker.

## What is found

hit@10: at least one answering document in the first ten. Missed is
1 - hit@10.

| method | recall@10 | hit@10 | missed | knowledge | status |
|---|---|---|---|---|---|
| keyword | 0.308 | 0.400 | 60.0% | 15/25 | 1/15 |
| vector | 0.254 | 0.350 | 65.0% | 14/25 | 0/15 |
| keyword + vector (hybrid) | 0.362 | 0.475 | 52.5% | 18/25 | 1/15 |
| keyword + graph (no vectors) | 0.608 | 0.700 | 30.0% | 14/25 | 14/15 |
| **keyword + vector + graph, weights 1, 1, 2 (shipped)** | **0.704** | **0.800** | **20.0%** | **18/25** | **14/15** |
| keyword + vector + graph, 1, 1, 1 | 0.454 | 0.550 | 45.0% | 18/25 | 4/15 |
| keyword + vector + graph, 1, 1, 3 | 0.696 | 0.800 | 20.0% | 18/25 | 14/15 |
| keyword + vector + graph, 1, 1, 4 | 0.688 | 0.800 | 20.0% | 18/25 | 14/15 |
| keyword + vector + graph, 1, 2, 3 | 0.654 | 0.750 | 25.0% | 17/25 | 13/15 |
| keyword + vector + graph, 1, 0.5, 2 | 0.662 | 0.775 | 22.5% | 17/25 | 14/15 |
| keyword + vector + graph, 1, 0.5, 3 | 0.654 | 0.775 | 22.5% | 17/25 | 14/15 |
| keyword + vector + graph, 0.5, 1, 3 | 0.654 | 0.750 | 25.0% | 16/25 | 14/15 |

The weights are match, near and about in the engine's weighted reciprocal
rank fusion (`query.DEFAULT_WEIGHTS`).

**Where vectors help: knowledge questions.** Vectors alone find 14 of 25,
nearly what keywords find (15), and they find different ones: fused with
keywords and the graph they lift knowledge questions from 14 to 18 of 25.
Overall, search goes from missing 30 percent (keyword + graph, what the
engine did before) to missing 20 percent.

**Where they do not: status questions.** "What did X last say" is a
recency question about a named agent, not a similarity one: vectors alone
find none of the 15. They are answered by the graph, which finds 14 of 15
(the agent's own messages, newest first).

**Why the trial's hybrid was worse than no vectors.** In the phase 0
harness, hybrid + graph with e5 missed 52.5 percent against 47.5 for
keyword + graph, because equal-weight fusion pushed the graph's results
for status questions out (7/15 to 3/15). The same happens here at equal
weights (1, 1, 1: 4/15 status). Giving the graph twice the weight of each
other list keeps status at 14/15 while vectors add their four knowledge
questions; 2, 3 and 4 for the graph give the same 80 percent, so the
choice is on a plateau, not a knife edge. The engine's graph is also
stronger than the harness's (keyword + graph 70 percent against 52.5): it
resolves agents through their names and aliases and reads their messages
through `said_by` edges.

**The fixes tried.**
- e5's prefixes: passages are embedded as "passage: <text>" and questions
  as "query: <text>", the model's training form (kept; the trial used them
  too).
- Rank fusion: weighted reciprocal rank fusion, the table above.
- Chunking: the phase 0 chunks were used as they are, so the figures stay
  comparable with the trial; the ingest's own chunker (1,000 characters
  with a sentence of overlap) is not measured here.
- The bar phase 0 set, under 20 percent missed, is met at exactly 20.0
  percent, not under it: 8 of 40 questions are still missed.

## What it costs

| measure | figure |
|---|---|
| model | multilingual-e5-small, int8 ONNX, Xenova's export at commit `761b726d`, MIT licence, 384 dimensions, one space for English and Spanish |
| model download (Mac and Windows alike) | 135.4 MB: `model.onnx` 118,308,185 bytes (SHA-256 `f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193`) and `tokenizer.json` 17,082,730 bytes (SHA-256 `0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39`) |
| runtime download | onnxruntime 1.23.2: 18.3 MiB wheel on an Intel Mac, 16.4 MiB on Apple silicon, 12.8 MiB on Windows; with its requirements (numpy, sympy, protobuf, flatbuffers, coloredlogs, packaging) about 190 MB installed |
| no tokenizer library | the model's SentencePiece tokenizer is read by `knowledge/unigram.py`, which gives the `tokenizers` library's ids on all 8,604 chunks and 5 edge cases (0 differ), 2.7 ms a text |
| memory the engine gains | about 560 MB resident: onnxruntime and numpy 23, the tokenizer 60 (from a compact file made at install; parsing its JSON instead costs some 230 MB), the model session 275, activations up to 200 for a batch of four 512-token texts. The engine's budget grows by 600 MB while the model is installed |
| one search (hybrid, the brain's knowledge_search) | 52 ms p50, 162 ms p95; 128 ms of CPU p50 across threads |
| embedding a question | 10 ms p50, 29 ms p95; about 35 ms of CPU |
| embedding everything (8,604 chunks, 4.07 M characters) | 749 s (12.5 min), 87 ms a chunk, 2,605 s of CPU (about 3.5 cores, 4 threads at nice 10), under the pace: it stops while he speaks and goes on after |
| model load | 2.3 s |

## Re-running it

```bash
S=<a folder outside the repository>
V=<a venv holding requirements.txt's packages>
P5=docs/planning/work-package-bundles/phase5
$V/bin/python -I $P5/evaluate.py install --data $S/data --files <folder with the two files>
$V/bin/python -I $P5/evaluate.py build --data $S/data --chunks chunks.jsonl --gold gold.json --out $S/engine
$V/bin/python -I $P5/evaluate.py eval --data $S/data --gold gold.json --out $S/engine \
    --weights 1,1,1 --weights 1,1,2 --weights 1,1,3
```
