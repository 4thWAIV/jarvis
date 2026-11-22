"""The author's working set and its batch search (working_set.py,
role_search.py; construction-drafts.md "The author works from a prioritized
working set and calls back for more", Tim 2026-11-21): packed to the
model's window, never over it, the prioritized sentences first and the
lowest priority cut first, saying what was left out; and several searches
run at once."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

from jarvis import model_tiers, pages, role_mcp, role_search, roles, working_set


@dataclass
class Row:
    stored: str
    knowledge_state: str = "probably_does_not_know"
    file_type: str = "prose"
    type: str = "file"
    size: int = 0


def _large(files: int = 40, sentences: int = 400):
    """Evidence far past any window: `files` files of `sentences` marked
    sentences each, every one bearing on a question."""
    rows, texts, entries = [], {}, []
    for f in range(files):
        name = f"files/f{f}.md"
        parts, said, at = [], [], 0
        for n in range(sentences):
            words = f"Sentence {n} of file {f} says something worth reading about the plan. "
            parts.append({"id": f"s{n}", "segment": words.strip(), "kind": "sentence",
                          "position": {"start": at, "end": at + len(words) - 1},
                          "scores": {"background": 0.6}})
            said.append({"item": f"s{n}", "scores": {"q1": 0.5 + (n % 50) / 100}})
            at += len(words)
        text = f"# File {f}\n" + "".join(p["segment"] + " " for p in parts)
        texts[name] = text
        rows.append(Row(name, size=len(text)))
        entries.append({"file": name, "marks": {"parts": parts, "questions": said,
                                                 "facts": []}})
    return rows, texts, {"files": entries}


def test_the_working_set_never_exceeds_its_budget_on_a_large_evidence():
    rows, texts, marked = _large()
    whole = sum(pages.tokens(t) for t in texts.values())
    for engine, model in (("claude", "opus"), ("codex", "gpt-6.2-astra")):
        budget = pages.budget(model_tiers.Pick(engine, model, 1, "test", [model]), "author")
        room = working_set.room(budget)
        assert room <= budget.window // 2, "never over half the window"
        assert budget.fits(room, budget.answer_most)
        assert whole > room, "the evidence is past the window whole"
        looked = [{"tool": "knowledge_search", "query": "q", "hits": [
            {"path": f"/x/{n}.md", "score": 0.5, "text": "hit " * 400} for n in range(50)]}]
        ws = working_set.build(rows, texts, marked, ["q1"], looked, room, reads="its tools.")
        assert ws.tokens <= room and not ws.whole
        assert ws.cut and ws.given["sentences"] < ws.total["sentences"]
        assert ws.given["contents"] == len(rows), "every file named"
        assert "were left out" in ws.text and "its tools." in ws.text


def _small():
    rows = [Row("files/a.md"), Row("files/b.md", "knows")]
    texts = {"files/a.md": "# A plan\nLow one. High one. Not asked.\n" + "Unmarked words. " * 200,
             "files/b.md": "Known thing here. More."}
    marked = {"questions": [{"id": "q1", "asks": "Build it?"},
                            {"id": "background", "asks": "background"}], "files": [
        {"file": "files/a.md", "marks": {"parts": [
            {"id": "s1", "segment": "Low one.", "position": {"start": 9, "end": 17},
             "scores": {"evidence": 0.9}},
            {"id": "s2", "segment": "High one.", "position": {"start": 18, "end": 27},
             "scores": {"background": 0.7}},
            {"id": "s3", "segment": "Not asked.", "position": {"start": 28, "end": 38},
             "scores": {"filler": 0.9}}],
            "questions": [{"item": "s1", "scores": {"q1": 0.6}},
                          {"item": "s2", "scores": {"q1": 0.9, "background": 0.8}},
                          {"item": "s3", "scores": {"q1": 0.4, "background": 0.9}}],
            "facts": []}},
        {"file": "files/b.md", "marks": {"parts": [
            {"id": "s1", "segment": "Known thing here.", "position": {"start": 0, "end": 17},
             "scores": {"known-to-him": 0.8}}],
            "questions": [{"item": "s1", "scores": {"q1": 0.6}}], "facts": []}}]}
    return rows, texts, marked


def test_only_the_handoff_s_questions_rank_a_sentence():
    """Classification's background score is no question: a sentence that
    bears only on background is never prioritized (measured on
    package-870e45145b2d, 2,300 of 2,468 sentences got in through it)."""
    rows, texts, marked = _small()
    found = working_set.prioritized(marked, rows, ["q1"])
    assert [(s.file, s.id) for s in found] == [
        ("files/a.md", "s2"), ("files/a.md", "s1"), ("files/b.md", "s1")], \
        "by question score, then label score; under 0.5 never, background never"
    assert all("background" not in s.questions for s in found)


def test_the_files_come_whole_with_short_marks_in_place_when_they_fit():
    rows, texts, marked = _small()
    ws = working_set.build(rows, texts, marked, ["q1"], [], 10_000, reads="its tools.")
    assert ws.whole and not ws.cut
    assert "[s2 q1 .9 background .7] High one." in ws.text, "a short mark, in place"
    assert "[s3 filler .9] Not asked." in ws.text, "every marked sentence, whole files"
    assert "# A plan\n[s1 q1 .6 evidence .9] Low one." in ws.text
    assert ws.text.count('path="files/a.md"') == 2, "named once whole, once in the contents"
    assert "start=" not in ws.text and "end=" not in ws.text, "no position repeated"
    assert "q1: Build it?" in ws.text, "the legend names the questions"
    assert ws.text.index("<contents>") < ws.text.index("<document")
    assert "its tools." in ws.text
    # Every mark taken out gives each file back exactly.
    import re
    for path, text in texts.items():
        body = ws.text.split(f'<document path="{path}"')[1].split(">", 1)[1].split("</document>")[0]
        assert re.sub(r"\[s\d+ [^\]]*\] ", "", body).strip("\n") == text


def test_the_working_set_only_when_the_files_do_not_fit():
    rows, texts, marked = _small()
    whole = working_set.build(rows, texts, marked, ["q1"], [], 10_000, reads="x")
    ws = working_set.build(rows, texts, marked, ["q1"], [], whole.tokens - 1, reads="x")
    assert not ws.whole
    assert ws.text.index("<contents>") < ws.text.index("<prioritized")
    assert ws.text.count("High one.") == 1, "its words once, under the question it bears on most"
    assert "[s2 q1 .9 background .7] High one." in ws.text
    assert ws.text.count('<from file="files/a.md">') == 1, "a file named once a question"
    assert "Not asked." not in ws.text
    # Cut to a room that holds only the legend, contents and the first
    # sentence: the lowest go first.
    names = working_set.numbered(["q1"])
    head = working_set.legend(names, {"q1": "Build it?"})
    toc = sum(pages.tokens(line) + 1 for line in working_set.contents(rows, texts))
    found = working_set.prioritized(marked, rows, ["q1"])
    small = (pages.tokens(head) + 1 + toc + pages.tokens(found[0].line(names)) + 1
             + pages.tokens('<from file="files/a.md">\n\n</from>') + 1 + working_set.NOTE_TOKENS)
    cut = working_set.build(rows, texts, marked, ["q1"], [], small, reads="x")
    assert "High one." in cut.text and "Low one." not in cut.text
    assert "Known thing here." not in cut.text.split("<prioritized")[1]
    assert cut.given == {"contents": 2, "sentences": 1, "hits": 0}
    assert "1 of 3 prioritized sentences" in cut.text and "were left out" in cut.text


def test_a_file_s_one_line_is_its_first_heading_else_its_first_sentence():
    assert working_set.summary_line("intro\n## The plan ##\nmore") == "The plan"
    assert working_set.summary_line('{\n  "x": 1,\n  "plainly": "It moves. Then"}') \
        .startswith('"x": 1')
    assert working_set.summary_line("First one here. Second.") == "First one here."


def test_the_batch_search_runs_its_searches_at_once():
    began, lock, running = [], threading.Lock(), {"now": 0, "most": 0}

    def search(text, under, kinds):
        with lock:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
        time.sleep(0.2)
        with lock:
            running["now"] -= 1
        began.append((text, under))
        return {"hits": [{"key": f"{under or ''}/{text}.md", "source": "s", "score": 0.7,
                          "why": ["words"], "text": "word " * 200}]}
    t = time.monotonic()
    found = role_search.run([{"words": f"w{n}", "under": "/e" if n % 2 else ""}
                             for n in range(6)], search)
    assert time.monotonic() - t < 0.2 * 3, "at once, not one after another"
    assert running["most"] == 6
    assert [f["words"] for f in found] == [f"w{n}" for n in range(6)], "in the order given"
    assert found[1]["under"] == "/e" and "under" not in found[0]
    hit = found[0]["hits"][0]
    assert hit["path"] == "/w0.md" and len(hit["text"]) <= role_search.HIT_CHARS


def test_the_author_s_tool_server_offers_the_batch_search_and_logs_it(tmp_path, monkeypatch):
    assert "search_at_once" in roles.role("author").tools
    assert {s["name"] for s in role_mcp.tool_specs("author")} >= {
        "search_at_once", "knowledge_search", "read_package_file", "search_package_files"}
    assert "search_at_once" not in roles.role("editor").tools
    monkeypatch.setattr(role_search, "SEARCH", lambda: lambda text, under, kinds: {
        "hits": [{"key": "/ev/files/a.md", "score": 1.0, "text": "a"}]})
    monkeypatch.setattr(role_mcp, "ROLE", "author")
    monkeypatch.setattr(role_mcp, "LOG", tmp_path / "research-log.jsonl")
    ok, text, _ = role_mcp.call("search_at_once", {"searches": [{"words": "a"},
                                                                {"words": "b"}]})
    assert ok and len(json.loads(text)["searches"]) == 2
    logged = json.loads((tmp_path / "research-log.jsonl").read_text().splitlines()[0])
    assert logged["tool"] == "search_at_once" and logged["hits"] == ["/ev/files/a.md"]
    assert not role_mcp.call("search_at_once", {"searches": []})[0]
    monkeypatch.setattr(role_mcp, "ROLE", "editor")
    assert not role_mcp.call("search_at_once", {"searches": [{"words": "a"}]})[0]
