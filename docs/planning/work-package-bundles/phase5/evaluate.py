"""Phase 5: vector search measured in the knowledge engine itself, on the
phase 0 question set (phase0/README.md says how the questions and chunks
were made; both stay outside the repository, being Tim's words).

    python -I evaluate.py install --data D --files F
        Installs the embedding model into the data folder D through
        `embedding_models.install`, from a folder F already holding the two
        published files (the same SHA-256 and size check as a download).

    python -I evaluate.py build --data D --chunks C.jsonl --gold G.json --out E
        Opens a knowledge engine at E and writes the chunks into it as the
        ingest does: a document per source, its chunks with `at` and, for an
        agent's message, `agent` (a `said_by` edge to that agent), and the
        agents, people and projects of the question set's entity table with
        their names. The engine embeds every chunk in the background with
        the installed model; this waits for it and prints the timing.

    python -I evaluate.py eval --data D --gold G.json --out E [--weights a,b,c ...]
        For each question, the engine's own query (query.run), restricted
        to what existed before the question was asked (`at` before it;
        notes have `at` 0): keyword, keyword + graph, vector, hybrid
        (keyword + vector) and hybrid + graph at each weighting given
        (match,near,about), and recall@10 and hit@10 by source document,
        overall and per class. Prints aggregates only, no words.

Run with the project's code on the path and onnxruntime importable (a venv
holding requirements.txt's packages).
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))

K = 10


def peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20, 1)


def _setup(data: Path) -> None:
    os.environ["JARVIS_DATA_DIR"] = str(data)
    from jarvis import data_paths
    import importlib
    importlib.reload(data_paths)


def install(data: Path, files: Path) -> None:
    _setup(data)
    from jarvis import embedding_models
    t = time.perf_counter()
    where = embedding_models.install(files_from=files)
    print(json.dumps({"installed": str(where), "seconds": round(time.perf_counter() - t, 1),
                      "problem": embedding_models.model_problem()}))


def _agent_key(name: str) -> str:
    from jarvis import spoken_words
    return "agent:" + "-".join(spoken_words.folded_words(name))


def build(data: Path, chunks_path: Path, gold_path: Path, out: Path) -> None:
    _setup(data)
    from jarvis.knowledge.engine import Engine
    gold = json.loads(gold_path.read_text())
    chunks = [json.loads(line) for line in chunks_path.open(encoding="utf-8")]
    t0 = time.perf_counter()
    engine = Engine.open(out)
    if engine.vectors.embedder is None:
        raise SystemExit("no embedding model is installed in that data folder")
    entities: dict[str, dict] = {}
    speaker: dict[str, str] = {}                 # any name's agent key -> the agent's key
    for key, names in (gold.get("entities") or {}).items():
        kind = key.split(":", 1)[0]
        ekey = _agent_key(names[0]) if kind == "agent" else key
        entities[ekey] = {"_key": ekey, "_kind": kind, "_label": names[0], "aliases": names[1:]}
        if kind == "agent":
            speaker.update({_agent_key(n): ekey for n in names})
    for c in chunks:
        if c.get("agent"):
            k = speaker.setdefault(_agent_key(c["agent"]), _agent_key(c["agent"]))
            entities.setdefault(k, {"_key": k, "_kind": "agent", "_label": c["agent"], "aliases": []})
    engine.transaction({"puts": list(entities.values())})
    batch, docs_seen = [], set()
    timed = lambda c: c["source"] not in ("memory", "journal", "projects", "topics")  # noqa: E731
    for c in sorted(chunks, key=lambda c: (c["at"] if timed(c) else 0, c["id"])):
        if c["doc"] not in docs_seen:
            docs_seen.add(c["doc"])
            batch.append({"_key": "doc:" + c["doc"], "_kind": "document", "_label": c["doc"],
                          "_source": c["doc"], "_trust": c["trust"]})
        chunk = {"_key": "chunk:" + c["id"], "_kind": "chunk", "_label": c["doc"], "_source": c["doc"],
                 "_trust": c["trust"], "text": c["text"], "document": "doc:" + c["doc"],
                 "at": float(c["at"]) if timed(c) else 0.0}
        if c.get("agent"):
            chunk["agent"] = speaker[_agent_key(c["agent"])]
        batch.append(chunk)
        if len(batch) >= 400:
            engine.transaction({"puts": batch})
            batch = []
    if batch:
        engine.transaction({"puts": batch})
    loaded_s = time.perf_counter() - t0
    t1, c1 = time.perf_counter(), time.process_time()
    while not engine.vectors.wait_idle(timeout=30.0):
        print(f"waiting: {engine.vectors.pending()} left", file=sys.stderr, flush=True)
    embed_s, cpu_s = time.perf_counter() - t1, time.process_time() - c1
    health = engine.vectors.health()
    engine.close()
    print(json.dumps({"chunks": len(chunks), "entities": len(entities), "load_s": round(loaded_s, 1),
                      "embed_s": round(embed_s, 1), "embed_cpu_s": round(cpu_s, 1),
                      "ms_per_chunk": round(1000 * embed_s / max(1, len(chunks)), 1),
                      "embedded": health["embedded"], "spaces": health["spaces"],
                      "peak_rss_mb": peak_rss_mb()}, indent=2))


def evaluate(data: Path, gold_path: Path, out: Path, weightings: list[str]) -> dict:
    _setup(data)
    from jarvis.knowledge import query
    from jarvis.knowledge.engine import Engine
    gold = json.loads(gold_path.read_text())
    questions = gold["questions"] if isinstance(gold, dict) else gold
    engine = Engine.open(out)
    # As the running engine does on its own timer: the rows written to
    # files, so the scan is Accelerate's over a mapped file, not Python's
    # over the memtable.
    engine.store.flush()
    # What is found must not depend on how busy the machine is: the "before
    # then" filter reads every earlier chunk, so the quality runs get all
    # the time they need, and a search's real cost is timed apart below.
    query.BUDGET_MS_MAX = 60_000
    methods = {"keyword": {"match": True}, "keyword+graph": {"match": True, "about": True},
               "vector": {"near": True}, "hybrid": {"match": True, "near": True}}
    for w in weightings:
        m, n, a = (float(x) for x in w.split(","))
        methods[f"hybrid+graph[{w}]"] = {"match": True, "near": True, "about": True,
                                         "weights": {"match": m, "near": n, "about": a}}
    rows: dict[str, list[dict]] = defaultdict(list)
    partial: dict[str, int] = defaultdict(int)
    took: dict[str, list[float]] = defaultdict(list)
    embed_ms: list[float] = []
    cpu: dict[str, list[float]] = defaultdict(list)
    for q in questions:
        text, rel = q["question"], set(q["relevant"])
        engine.vectors.embed_query(text, timeout=5.0)          # warm, so the timings are a search's
        t = time.perf_counter()
        engine.vectors.embed_query(text, timeout=5.0)
        embed_ms.append((time.perf_counter() - t) * 1000)
        for name, spec in methods.items():
            body = {"from": {"where": [["at", "<", float(q["at"])]]},
                    "return": {"properties": ["_source"], "limit": 60}, "budget_ms": 60_000}
            if spec.get("match"):
                body["match"] = text
            if spec.get("near"):
                body["near"] = {"text": text, "k": 200}
            if spec.get("about"):
                body["about"] = {"text": text}
            if spec.get("weights"):
                body["weights"] = spec["weights"]
            t, c = time.perf_counter(), time.process_time()
            answer = query.run(engine, body)
            partial[name] += bool(answer.get("partial"))
            took[name].append((time.perf_counter() - t) * 1000)
            cpu[name].append((time.process_time() - c) * 1000)
            top: list[str] = []
            for h in answer["hits"]:
                src = h.get("source")
                if src and src not in top:
                    top.append(src)
                if len(top) == K:
                    break
            found = rel & set(top)
            rows[name].append({"class": q.get("class", ""), "recall": len(found) / len(rel), "hit": bool(found)})
    engine.close()

    def pct(v: list[float], p: float) -> float:
        s = sorted(v)
        return round(s[min(len(s) - 1, int(p * len(s)))], 1)

    out_doc = {"questions": len(questions), "methods": {}}
    for name, r in rows.items():
        entry = {"recall@10": round(sum(x["recall"] for x in r) / len(r), 3),
                 "hit@10": round(sum(x["hit"] for x in r) / len(r), 3),
                 "missed": round(1 - sum(x["hit"] for x in r) / len(r), 3),
                 "search_ms_p50": pct(took[name], 0.5), "search_ms_p95": pct(took[name], 0.95),
                 "cpu_ms_p50": pct(cpu[name], 0.5), "partial": partial[name]}
        for cls in sorted({x["class"] for x in r}):
            sub = [x for x in r if x["class"] == cls]
            entry[f"hit[{cls}]"] = f"{sum(x['hit'] for x in sub)}/{len(sub)}"
        out_doc["methods"][name] = entry
    out_doc["query_embed_ms_p50"] = pct(embed_ms, 0.5)
    # A search as the brain's knowledge_search makes it, with no "before
    # then" filter and the engine's own budget: what a search costs in use.
    query.BUDGET_MS_MAX = 500
    engine = Engine.open(out)
    live_ms, live_cpu = [], []
    for q in questions:
        t, c = time.perf_counter(), time.process_time()
        query.search(engine, {"text": q["question"], "limit": 10})
        live_ms.append((time.perf_counter() - t) * 1000)
        live_cpu.append((time.process_time() - c) * 1000)
    engine.close()
    out_doc["hybrid_search_ms_p50"] = pct(live_ms, 0.5)
    out_doc["hybrid_search_ms_p95"] = pct(live_ms, 0.95)
    out_doc["hybrid_search_cpu_ms_p50"] = pct(live_cpu, 0.5)
    out_doc["query_embed_ms_p95"] = pct(embed_ms, 0.95)
    out_doc["peak_rss_mb"] = peak_rss_mb()
    return out_doc


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install")
    i.add_argument("--data", type=Path, required=True)
    i.add_argument("--files", type=Path, required=True)
    b = sub.add_parser("build")
    b.add_argument("--data", type=Path, required=True)
    b.add_argument("--chunks", type=Path, required=True)
    b.add_argument("--gold", type=Path, required=True)
    b.add_argument("--out", type=Path, required=True)
    e = sub.add_parser("eval")
    e.add_argument("--data", type=Path, required=True)
    e.add_argument("--gold", type=Path, required=True)
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--weights", action="append", default=[])
    args = ap.parse_args()
    if args.cmd == "install":
        install(args.data, args.files)
    elif args.cmd == "build":
        build(args.data, args.chunks, args.gold, args.out)
    else:
        print(json.dumps(evaluate(args.data, args.gold, args.out, args.weights or ["1,1,1"]), indent=2))


if __name__ == "__main__":
    main()
