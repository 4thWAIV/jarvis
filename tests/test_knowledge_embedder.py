"""Vector search (phase 5): installing the embedding model, the pure-Python
tokenizer, the pluggable runner, attaching an embedder while the engine
runs, the graph's ranking and the weighted fusion, the ingest's `at` and
`agent`, and the page and brain sharing one call. The real model is run
only by the `knowledge_engine`-marked test at the end."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from jarvis import data_paths, embedding_models as em
from jarvis import knowledge_ingest as ki
from jarvis.knowledge import embedder as emb
from jarvis.knowledge import query as qy
from jarvis.knowledge import vecmath
from jarvis.knowledge.engine import Engine
from jarvis.knowledge.unigram import Unigram
from jarvis.model_files import InstallRefused, sha256_of


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    monkeypatch.setattr(Engine, "compact_in_child", False)
    monkeypatch.setattr(vecmath, "MATH", vecmath.Reference())
    emb._loaded.clear()
    yield
    emb._loaded.clear()


# --- installing the model -----------------------------------------------------

TINY_TOKENIZER = {"normalizer": None,
                  "pre_tokenizer": {"type": "Metaspace", "replacement": "\u2581", "add_prefix_space": True},
                  "model": {"type": "Unigram", "unk_id": 3,
                            "vocab": [["<s>", 0.0], ["<pad>", 0.0], ["</s>", 0.0], ["<unk>", 0.0], ["\u2581a", -1.0]]},
                  "added_tokens": [{"id": 0, "content": "<s>"}, {"id": 1, "content": "<pad>"},
                                   {"id": 2, "content": "</s>"}]}


def _tiny_model(tmp_path: Path) -> tuple[em.EmbeddingModel, Path]:
    src = tmp_path / "published"
    (src / "onnx").mkdir(parents=True)
    (src / "onnx" / "model_quantized.onnx").write_bytes(b"model bytes")
    (src / "tokenizer.json").write_text(json.dumps(TINY_TOKENIZER))
    files = tuple(em.ModelFile(remote, name, sha256_of(src / remote), (src / remote).stat().st_size)
                  for remote, name in (("onnx/model_quantized.onnx", "model.onnx"), ("tokenizer.json", "tokenizer.json")))
    model = em.EmbeddingModel("tiny", "tiny", "x/tiny", "0" * 40, files, 4, "fake", "MIT", 10)
    return model, src


def test_an_install_checks_every_file_and_renames_the_folder_into_place(tmp_path):
    model, src = _tiny_model(tmp_path)
    assert "not installed" in em.model_problem(model=model)
    where = em.install(model, files_from=src)
    assert where == data_paths.embedding_models_dir() / "tiny"
    assert sorted(p.name for p in where.iterdir()) == ["model.json", "model.onnx", "tokenizer.bin", "tokenizer.json"]
    assert Unigram(where / "tokenizer.bin").encode("a a") == Unigram(where / "tokenizer.json").encode("a a") == [0, 4, 4, 2]
    assert em.model_problem(model=model) is None
    assert json.loads((where / "model.json").read_text())["revision"] == "0" * 40
    assert not [p for p in where.parent.iterdir() if p.name.startswith(".embedding-model-")]


def test_a_file_that_is_not_the_published_one_is_refused_and_nothing_is_installed(tmp_path):
    model, src = _tiny_model(tmp_path)
    (src / "tokenizer.json").write_text('{"changed": true}')
    with pytest.raises(InstallRefused, match="SHA-256"):
        em.install(model, files_from=src)
    assert not em.model_dir(model).exists()


def test_a_download_is_the_published_url_at_the_pinned_revision(tmp_path, monkeypatch):
    model, src = _tiny_model(tmp_path)
    fetched = []

    def fake_download(url, dest, progress):
        fetched.append(url)
        remote = url.split(f"/resolve/{model.revision}/", 1)[1]
        dest.write_bytes((src / remote).read_bytes())
    monkeypatch.setattr(em, "download", fake_download)
    em.install(model)
    assert fetched == [f"https://huggingface.co/x/tiny/resolve/{'0' * 40}/onnx/model_quantized.onnx",
                       f"https://huggingface.co/x/tiny/resolve/{'0' * 40}/tokenizer.json"]


def test_the_shipped_model_names_the_trial_s_file():
    f = em.DEFAULT.files[0]
    assert f.sha256.startswith("f80102d3") and f.sha256.endswith("c193") and f.size == 118308185
    assert em.DEFAULT.revision.startswith("761b726d") and em.DEFAULT.dim == 384


# --- the runner -----------------------------------------------------------------

def test_the_runner_gives_the_installed_model_through_its_runtime(tmp_path, monkeypatch):
    model, src = _tiny_model(tmp_path)
    monkeypatch.setattr(em, "DEFAULT", model)
    assert emb.installed() is None                            # nothing installed: no vectors
    em.install(model, files_from=src)
    made = []
    monkeypatch.setitem(emb.RUNTIMES, "fake", lambda folder, m: made.append(folder) or emb.FakeEmbedder(4))
    first = emb.installed()
    assert first is not None and first.dim == 4 and emb.installed() is first and len(made) == 1


def test_a_runtime_that_cannot_load_turns_vectors_off_not_the_engine(tmp_path, monkeypatch):
    model, src = _tiny_model(tmp_path)
    monkeypatch.setattr(em, "DEFAULT", model)
    em.install(model, files_from=src)

    def broken(folder, m):
        raise RuntimeError("no runtime here")
    monkeypatch.setitem(emb.RUNTIMES, "fake", broken)
    assert emb.installed() is None
    e = Engine.open(tmp_path / "k")
    try:
        assert e.vectors.space_id is None
        assert qy.search(e, {"text": "anything"})["notes"] == ["near: no embedder, so vectors were not used"]
    finally:
        e.close()


def test_an_embedder_that_appears_later_is_attached_and_embeds_what_waits(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "RUNNER", lambda: None)
    e = Engine.open(tmp_path / "k")
    try:
        e.transaction({"puts": [{"_key": "doc:a", "_kind": "document", "_label": "a"},
                                {"_key": "chunk:a", "_kind": "chunk", "_label": "a", "document": "doc:a",
                                 "text": "the zebra roadmap"}]})
        assert e.vectors.space_id is None
        monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(16))
        e.maintain()
        assert e.vectors.space_id == "fake-16" and e.vectors.wait_idle(10)
        hits = qy.run(e, {"near": {"text": "zebra roadmap"}})["hits"]
        assert hits[0]["key"] == "chunk:a" and "near" in hits[0]["why"]
    finally:
        e.close()


# --- the tokenizer ---------------------------------------------------------------

def _tokenizer_file(tmp_path: Path, normalizers: list, vocab: list) -> Path:
    spec = {"normalizer": {"type": "Sequence", "normalizers": normalizers},
            "pre_tokenizer": {"type": "Metaspace", "replacement": "▁", "add_prefix_space": True},
            "model": {"type": "Unigram", "unk_id": 3, "vocab": vocab},
            "added_tokens": [{"id": 0, "content": "<s>"}, {"id": 1, "content": "<pad>"},
                             {"id": 2, "content": "</s>"}, {"id": 3, "content": "<unk>"}]}
    path = tmp_path / "tokenizer.json"
    path.write_text(json.dumps(spec))
    return path


def test_unigram_takes_the_most_probable_pieces_and_fuses_unknowns(tmp_path):
    vocab = [["<s>", 0.0], ["<pad>", 0.0], ["</s>", 0.0], ["<unk>", 0.0],
             ["▁the", -1.0], ["▁", -2.0], ["zeb", -3.0], ["ra", -3.0], ["zebra", -4.0],
             ["▁zebra", -9.0]]
    tok = Unigram(_tokenizer_file(tmp_path, [{"type": "Replace", "pattern": {"Regex": " {2,}"}, "content": " "}],
                                  vocab))
    # "▁zebra" alone costs -9; "▁" + "zebra" costs -6, better than "▁"+"zeb"+"ra" (-8).
    assert tok.encode("the  zebra") == [0, 4, 5, 8, 2]
    assert tok.encode("the ΩΩ") == [0, 4, 5, 3, 2]          # two unknown characters, one <unk>
    assert len(tok.encode("the " * 600, max_len=16)) == 16


def test_the_character_map_is_read_from_the_double_array_trie(tmp_path):
    # One entry: "A" (0x41) normalised to "a". Unit 0 is the root, offset 1;
    # unit 1 ^ 0x41 holds label 0x41, a leaf flag and offset 1 to its value.
    units = [0] * 0x50
    units[0] = 1 << 10                          # root: offset 1
    pos = 1 ^ 0x41
    units[pos] = 0x41 | (1 << 8) | (1 << 10)    # label A, has a leaf, offset 1
    units[pos ^ 1] = 0                           # value: offset 0 into the strings
    blob = len(units).to_bytes(4, "little") * 0 + (len(units) * 4).to_bytes(4, "little")
    blob += b"".join(u.to_bytes(4, "little") for u in units) + b"a\x00"
    vocab = [["<s>", 0.0], ["<pad>", 0.0], ["</s>", 0.0], ["<unk>", 0.0], ["▁a", -1.0], ["▁b", -1.0]]
    tok = Unigram(_tokenizer_file(tmp_path, [{"type": "Precompiled",
                                              "precompiled_charsmap": base64.b64encode(blob).decode()}], vocab))
    assert tok.normalize("A b") == "a b"
    assert tok.encode("A") == [0, 4, 2]


# --- the graph and the fusion ----------------------------------------------------

@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))
    e = Engine.open(tmp_path / "k")
    puts = [{"_key": "agent:hammer", "_kind": "agent", "_label": "hammer", "aliases": ["the hammer agent"]},
            {"_key": "agent:anvil", "_kind": "agent", "_label": "anvil"},
            {"_key": "doc:d", "_kind": "document", "_label": "d"}]
    for i, (agent, text, at) in enumerate([("agent:hammer", "the old report on the parser", 100.0),
                                           ("agent:hammer", "the newest report: tests pass", 300.0),
                                           ("agent:anvil", "the parser report from anvil", 200.0),
                                           (None, "notes about the parser and hammer", 50.0)]):
        chunk = {"_key": f"chunk:{i}", "_kind": "chunk", "_label": "d", "document": "doc:d", "text": text, "at": at}
        if agent:
            chunk["agent"] = agent
        puts.append(chunk)
    e.transaction({"puts": puts})
    assert e.vectors.wait_idle(20)
    yield e
    e.close()


def test_about_ranks_what_a_named_agent_said_newest_first(engine):
    hits = qy.run(engine, {"about": {"text": "what did hammer say last"}})["hits"]
    assert [h["key"] for h in hits][:2] == ["chunk:1", "chunk:0"]
    assert hits[0]["why"]["about"]["edge"] == "said_by" and hits[0]["why"]["about"]["entity"] == "agent:hammer"
    assert qy.run(engine, {"about": {"text": "what did the hammer agent say"}})["hits"][0]["key"] == "chunk:1"
    assert qy.run(engine, {"about": {"text": "nobody named here"}})["hits"] == []


def test_about_keeps_to_what_existed_before_a_moment(engine):
    hits = qy.run(engine, {"from": {"where": [["at", "<", 250.0]]}, "about": {"text": "hammer"}})["hits"]
    assert [h["key"] for h in hits] == ["chunk:0"]


def test_search_fuses_words_meaning_and_the_graph_by_their_weights(engine):
    both = qy.search(engine, {"text": "hammer parser report"})["hits"]
    assert {"match", "about"} <= set(both[0]["why"])
    graph_only = qy.run(engine, {"match": "parser report", "about": {"text": "hammer"},
                                 "weights": {"match": 0.0, "about": 1.0}})["hits"]
    assert graph_only[0]["key"] == "chunk:1"
    words_only = qy.run(engine, {"match": "parser report", "about": {"text": "hammer"},
                                 "weights": {"match": 1.0, "about": 0.0}})["hits"]
    assert words_only[0]["key"] in ("chunk:0", "chunk:2")
    with pytest.raises(Exception):
        qy.run(engine, {"match": "x", "weights": {"loudness": 2}})


# --- ingest ----------------------------------------------------------------------

def test_the_conversation_gives_each_chunk_its_time_and_an_agent_s_words_their_speaker(tmp_path, monkeypatch):
    from jarvis import conversation_log as cl, run_store
    from tests.test_knowledge_ingest import caller, only
    monkeypatch.setattr(emb, "RUNNER", lambda: None)
    run_store.init_db()
    e = Engine.open(tmp_path / "k")
    try:
        cl.append(cl.ConversationItem.new(cl.ItemKind.HEARD, "what did hammer say about the zebra"))
        cl.append(cl.ConversationItem.new(cl.ItemKind.ANSWER, "the zebra parser is done",
                                          agent_session_id="s1", agent_name="Hammer"))
        ki.run_pass(caller(e), adapters=only(ki.Conversation))
        chunks = [d for d in e.docs.find(kinds_=["chunk"], limit=100)]
        said = next(c for c in chunks if "parser" in c["text"])
        heard = next(c for c in chunks if "what did" in c["text"])
        assert said["at"] > 0 and heard["at"] > 0
        assert [x["to"] for x in e.graph.edges(said["_key"], "out", "said_by")] == ["agent:hammer"]
        assert not list(e.graph.edges(heard["_key"], "out", "said_by"))
        assert e.docs.get("agent:hammer")["_trust"] == "untrusted"
        assert qy.run(e, {"about": {"text": "what did hammer say"}})["hits"][0]["key"] == said["_key"]
    finally:
        e.close()


def test_a_watermark_from_before_the_schema_reads_its_source_again(tmp_path, monkeypatch):
    from jarvis import conversation_log as cl, run_store
    from tests.test_knowledge_ingest import caller, only
    monkeypatch.setattr(emb, "RUNNER", lambda: None)
    run_store.init_db()
    e = Engine.open(tmp_path / "k")
    try:
        item = cl.append(cl.ConversationItem.new(cl.ItemKind.HEARD, "an old line"))
        e.transaction({"watermarks": {"conversation": {"after": item.id}}})       # written before schema 2
        ki.run_pass(caller(e), adapters=only(ki.Conversation))
        assert any("old line" in d["text"] for d in e.docs.find(kinds_=["chunk"], limit=10))
        assert e.watermarks()["conversation"]["schema"] == ki.SCHEMA
    finally:
        e.close()


# --- the real model (needs it installed, or JARVIS_E5_MODEL_DIR) -----------------

def _real_folder() -> Path:
    given = os.environ.get("JARVIS_E5_MODEL_DIR")
    folder = Path(given) if given else em.model_dir()
    if em.model_problem(folder) is not None or em.runtime_problem() is not None:
        pytest.skip("the embedding model or onnxruntime is not here")
    return folder


@pytest.mark.knowledge_engine
def test_the_real_model_embeds_by_meaning_across_both_languages():
    from jarvis.knowledge.e5_onnx import E5Onnx
    model = E5Onnx(_real_folder(), em.DEFAULT.space)
    passages = model.embed(["The parser's tests all pass now.", "Mañana lloverá en la ciudad.",
                            "Tomorrow it will rain in the city."], "passage")
    assert all(len(v) == 384 for v in passages)
    assert all(abs(sum(x * x for x in v) - 1.0) < 1e-3 for v in passages)
    q = model.embed(["will it rain tomorrow?"], "query")[0]

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))
    assert cos(q, passages[2]) > cos(q, passages[0]) and cos(q, passages[1]) > cos(q, passages[0])


@pytest.mark.knowledge_engine
def test_the_tokenizer_gives_the_library_s_ids_when_the_library_is_here():
    tokenizers = pytest.importorskip("tokenizers")
    folder = _real_folder()
    lib = tokenizers.Tokenizer.from_file(str(folder / "tokenizer.json"))
    mine = Unigram(folder / "tokenizer.json")
    for text in ("passage: the zebra parser", "query: ¿qué dijo hammer?", "ＦＵＬＬ width and 👍🏽"):
        assert mine.encode(text) == lib.encode(text).ids
