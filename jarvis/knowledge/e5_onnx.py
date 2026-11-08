"""multilingual-e5-small through onnxruntime: the embedder behind
`embedder.RUNNER` (design section 7.11).

One space for English and Spanish, 384 dimensions. A passage is embedded
as "passage: <text>" and a question as "query: <text>" (the model's
training prefixes), mean-pooled over the attention mask and L2-normalised.
The tokenizer is `unigram.Unigram` over the model's own `tokenizer.json`,
so onnxruntime (with numpy, its own requirement) is the one library this
needs. The model folder is `embedding_models.model_dir(...)`, installed
and checked there; this class only reads it.

`THREADS` intra-op threads (the engine runs at nice 10, and a query waits
behind at most one small ingest batch), CPU only, and no memory arena, so
the process gives back what one long batch took.
"""
from __future__ import annotations

import os
import threading
from array import array
from pathlib import Path

MAX_TOKENS = 512
THREADS = max(1, min(4, (os.cpu_count() or 4) // 4))
PREFIX = {"query": "query: ", "passage": "passage: "}


class E5Onnx:
    """`Embedder` for one installed model folder (`model.onnx`,
    `tokenizer.json`)."""

    def __init__(self, folder: Path, model_id: str, dim: int = 384, threads: int = THREADS) -> None:
        import numpy as np
        import onnxruntime as ort
        from jarvis.knowledge.unigram import Unigram
        self._np = np
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 3
        # Weights stay as the file has them rather than repacked per core:
        # a little slower, a good deal smaller (phase5/results.md).
        opts.add_session_config_entry("session.disable_prepacking", "1")
        self.session = ort.InferenceSession(str(Path(folder) / "model.onnx"), opts,
                                            providers=["CPUExecutionProvider"])
        compact = Path(folder) / "tokenizer.bin"
        self.tokenizer = Unigram(compact if compact.is_file() else Path(folder) / "tokenizer.json")
        self.inputs = {i.name for i in self.session.get_inputs()}
        out_dim = self.session.get_outputs()[0].shape[-1]
        if isinstance(out_dim, int) and out_dim != dim:
            raise ValueError(f"the model gives {out_dim} dimensions, not {dim}")
        self.id = model_id
        self.dim = dim
        self._lock = threading.Lock()

    def embed(self, texts: list[str], purpose: str) -> list[array]:
        np = self._np
        prefix = PREFIX["query" if purpose == "query" else "passage"]
        encoded = [self.tokenizer.encode(prefix + t, MAX_TOKENS) for t in texts]
        width = max(len(e) for e in encoded)
        ids = np.full((len(encoded), width), self.tokenizer.pad, dtype=np.int64)
        mask = np.zeros((len(encoded), width), dtype=np.int64)
        for row, e in enumerate(encoded):
            ids[row, :len(e)] = e
            mask[row, :len(e)] = 1
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        with self._lock:
            hidden = self.session.run(None, feed)[0]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
        pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        return [array("f", row.astype(np.float32).tobytes()) for row in pooled]
