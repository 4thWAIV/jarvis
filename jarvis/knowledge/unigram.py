"""A SentencePiece Unigram tokenizer read from a `tokenizer.json`, in plain
Python, so an embedder needs no tokenizer library (design section 7.11).

It does what the Hugging Face `tokenizers` library does for the XLM-R
family's file (multilingual-e5): the `Precompiled` normalizer (the
SentencePiece character map: a Darts double-array trie over UTF-8 bytes,
then the replacement strings), `Replace` of a regex, the `Metaspace`
pre-tokenizer (spaces become U+2581 and a word starts at each), Unigram
segmentation by Viterbi over the pieces' log probabilities, unknown
characters fused into one `<unk>`, and the template's `<s>` and `</s>`.
`tests/test_knowledge_embedder.py` holds it to the library's ids.

The file is data: it is parsed as JSON and nothing in it is run.
"""
from __future__ import annotations

import base64
import gc
import json
import re
import struct
import unicodedata
from array import array
from pathlib import Path
from typing import Optional

SPACE = "▁"
MAGIC = b"JUG1"
COMPACT = "tokenizer.bin"


class TokenizerError(ValueError):
    """A tokenizer file this reader does not understand."""


class _Charsmap:
    """SentencePiece's precompiled normalisation map."""

    def __init__(self, blob: bytes) -> None:
        if len(blob) < 4:
            raise TokenizerError("the character map is empty")
        size = struct.unpack_from("<I", blob, 0)[0]
        if size % 4 or 4 + size > len(blob):
            raise TokenizerError("the character map's trie does not fit")
        self.units = struct.unpack_from(f"<{size // 4}I", blob, 4)
        self.normalized = blob[4 + size:]

    @staticmethod
    def _offset(unit: int) -> int:
        return (unit >> 10) << ((unit & (1 << 9)) >> 6)

    def _prefixes(self, key: bytes) -> list[int]:
        """Darts common-prefix search: the values of every prefix of `key`
        that is in the trie, shortest first."""
        units = self.units
        out: list[int] = []
        pos = 0
        unit = units[pos]
        pos ^= self._offset(unit)
        for c in key:
            if c == 0:
                break
            pos ^= c
            if pos >= len(units):
                return out
            unit = units[pos]
            if unit & ((1 << 31) | 0xFF) != c:
                return out
            pos ^= self._offset(unit)
            if pos >= len(units):
                return out
            if (unit >> 8) & 1:
                out.append(units[pos] & ((1 << 31) - 1))
        return out

    def transform(self, chunk: str) -> Optional[str]:
        found = self._prefixes(chunk.encode("utf-8"))
        if not found:
            return None
        start = found[0]
        end = self.normalized.find(b"\x00", start)
        return self.normalized[start:end if end >= 0 else len(self.normalized)].decode("utf-8", "replace")

    def normalize(self, text: str) -> str:
        out: list[str] = []
        for cluster in _graphemes(text):
            if len(cluster.encode("utf-8")) < 6:
                replaced = self.transform(cluster)
                if replaced is not None:
                    out.append(replaced)
                    continue
            for ch in cluster:
                replaced = self.transform(ch)
                out.append(ch if replaced is None else replaced)
        return "".join(out)


def _graphemes(text: str) -> list[str]:
    """Clusters close enough to Unicode's for this map: a character and the
    combining marks, variation selectors and joiners after it."""
    out: list[str] = []
    for ch in text:
        if out and (unicodedata.category(ch) in ("Mn", "Me", "Mc") or ch in "‍️︎"
                    or (out[-1].endswith("‍"))):
            out[-1] += ch
        else:
            out.append(ch)
    return out


class Unigram:
    """`encode(text) -> ids`, `<s>` first and `</s>` last, at most `max_len`
    ids in all."""

    def __init__(self, path: Path) -> None:
        """From the model's `tokenizer.json`, or from the compact file
        `compile` writes beside it (`COMPACT`), which loads in a fraction of
        the memory: parsing a quarter of a million pieces from JSON leaves
        the process some 200 MB larger for good."""
        path = Path(path)
        if path.suffix == ".bin":
            self._load(path)
        else:
            self._from_spec(json.loads(path.read_text(encoding="utf-8")))
            gc.collect()

    def _from_spec(self, spec: dict) -> None:
        model = spec.get("model") or {}
        if model.get("type") != "Unigram":
            raise TokenizerError("only a Unigram model is read")
        vocab = model["vocab"]
        self.pieces: dict[str, int] = {}
        self.scores = array("d", (float(score) for _, score in vocab))
        for i, (piece, _score) in enumerate(vocab):
            self.pieces.setdefault(piece, i)
        self._words = [piece for piece, _ in vocab]
        self.unk_id = int(model.get("unk_id") if model.get("unk_id") is not None else 3)
        self.charsmap_blob = b""
        self.replace_specs: list[tuple[str, str]] = []
        for n in self._normalizers(spec.get("normalizer")):
            if n["type"] == "Precompiled":
                self.charsmap_blob = base64.b64decode(n["precompiled_charsmap"])
            elif n["type"] == "Replace":
                pattern = n["pattern"]
                regex = pattern.get("Regex")
                self.replace_specs.append((regex if regex is not None else re.escape(pattern["String"]),
                                           n["content"]))
            else:
                raise TokenizerError(f"normalizer {n['type']} is not read")
        pre = spec.get("pre_tokenizer") or {}
        if pre.get("type") != "Metaspace":
            raise TokenizerError("only the Metaspace pre-tokenizer is read")
        self.replacement = pre.get("replacement", SPACE)
        self.prefix = bool(pre.get("add_prefix_space", True) or pre.get("prepend_scheme") in ("always", "first"))
        added = {t["content"]: t["id"] for t in spec.get("added_tokens") or []}
        self.bos, self.eos, self.pad = added.get("<s>", 0), added.get("</s>", 2), added.get("<pad>", 1)
        self._ready()

    def _ready(self) -> None:
        self.unk_score = min(self.scores) - 10.0
        self.longest = max(len(p) for p in self.pieces)
        self.charsmap = _Charsmap(self.charsmap_blob) if self.charsmap_blob else None
        self.replaces = [(re.compile(r), c) for r, c in self.replace_specs]

    def save(self, path: Path) -> None:
        """The compact file: a header, the character map, the scores and the
        pieces, NUL-separated."""
        if any("\x00" in w for w in self._words):
            raise TokenizerError("a piece holds a NUL")
        header = json.dumps({"unk_id": self.unk_id, "replaces": self.replace_specs,
                             "replacement": self.replacement, "prefix": self.prefix,
                             "bos": self.bos, "eos": self.eos, "pad": self.pad,
                             "count": len(self._words)}).encode()
        words = "\x00".join(self._words).encode("utf-8")
        with open(path, "wb") as out:
            out.write(MAGIC + struct.pack("<I", len(header)) + header)
            out.write(struct.pack("<I", len(self.charsmap_blob)) + self.charsmap_blob)
            out.write(self.scores.tobytes())
            out.write(words)

    def _load(self, path: Path) -> None:
        blob = path.read_bytes()
        if not blob.startswith(MAGIC):
            raise TokenizerError("not a compact tokenizer file")
        at = len(MAGIC)
        (n,) = struct.unpack_from("<I", blob, at)
        header = json.loads(blob[at + 4:at + 4 + n])
        at += 4 + n
        (m,) = struct.unpack_from("<I", blob, at)
        self.charsmap_blob = blob[at + 4:at + 4 + m]
        at += 4 + m
        count = int(header["count"])
        self.scores = array("d")
        self.scores.frombytes(blob[at:at + 8 * count])
        at += 8 * count
        words = blob[at:].decode("utf-8").split("\x00")
        if len(words) != count:
            raise TokenizerError("the compact tokenizer file is cut short")
        self.pieces = {}
        for i, w in enumerate(words):
            self.pieces.setdefault(w, i)
        self._words = words
        self.unk_id = int(header["unk_id"])
        self.replace_specs = [tuple(x) for x in header["replaces"]]
        self.replacement, self.prefix = header["replacement"], bool(header["prefix"])
        self.bos, self.eos, self.pad = int(header["bos"]), int(header["eos"]), int(header["pad"])
        self._ready()

    @staticmethod
    def _normalizers(spec) -> list[dict]:
        if not spec:
            return []
        return list(spec["normalizers"]) if spec.get("type") == "Sequence" else [spec]

    def normalize(self, text: str) -> str:
        if self.charsmap is not None:
            text = self.charsmap.normalize(text)
        for pattern, content in self.replaces:
            text = pattern.sub(content, text)
        return text

    def words(self, text: str) -> list[str]:
        text = text.replace(" ", self.replacement)
        if self.prefix and not text.startswith(self.replacement):
            text = self.replacement + text
        out: list[str] = []
        for i, ch in enumerate(text):
            if ch == self.replacement and out and i:
                out.append(ch)
            elif out:
                out[-1] += ch
            else:
                out.append(ch)
        return out

    def _viterbi(self, word: str) -> list[int]:
        """The most probable segmentation; a character no piece starts with
        is `<unk>`, and unknowns next to each other are one."""
        n = len(word)
        neg = float("-inf")
        best = [neg] * (n + 1)
        back: list[tuple[int, int]] = [(0, -1)] * (n + 1)
        best[0] = 0.0
        pieces, scores, longest = self.pieces, self.scores, self.longest
        for start in range(n):
            here = best[start]
            if here == neg:
                continue
            single = False
            for end in range(start + 1, min(n, start + longest) + 1):
                found = pieces.get(word[start:end])
                if found is None:
                    continue
                if end == start + 1:
                    single = True
                score = here + scores[found]
                if score > best[end]:
                    best[end], back[end] = score, (start, found)
            if not single:
                score = here + self.unk_score
                if score > best[start + 1]:
                    best[start + 1], back[start + 1] = score, (start, self.unk_id)
        ids: list[int] = []
        end = n
        while end > 0:
            start, piece = back[end]
            if not (piece == self.unk_id and ids and ids[-1] == self.unk_id):
                ids.append(piece)
            end = start
        return ids[::-1]

    def encode(self, text: str, max_len: int = 512) -> list[int]:
        ids: list[int] = []
        for word in self.words(self.normalize(text)):
            ids.extend(self._viterbi(word))
        return [self.bos] + ids[:max_len - 2] + [self.eos]


def compile_file(source: Path, target: Path) -> None:
    """`tokenizer.json` read once and written compact beside it."""
    Unigram(source).save(target)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4 or sys.argv[1] != "compile":
        raise SystemExit("python -m jarvis.knowledge.unigram compile tokenizer.json tokenizer.bin")
    compile_file(Path(sys.argv[2]), Path(sys.argv[3]))
