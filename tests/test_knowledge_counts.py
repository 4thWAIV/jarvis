"""Counts from the index alone (UDS round 3): the owner's count, grouped
count, `embedded` and `waiting` come from the general index's postings and
the resident key column, equal to reading each item, with no item decoded
when every condition is exact; and the general index's bound on long text:
a record field of 100 KB is keyed by a bounded prefix, folded only that
far, and `=`, `in` and `prefix` still answer exactly."""
from __future__ import annotations

import statistics
import time

import pytest

from jarvis import spoken_words
from jarvis.knowledge import encoding, lexical
from jarvis.knowledge.documents import FID
from jarvis.knowledge.uds import Caller, Transaction, in_process

TIM = Caller("tim")
LONG = "Ünïcode prefix " * 20                       # folds past the index's bound


@pytest.fixture
def opened(tmp_path):
    with in_process(tmp_path / "engine") as both:
        yield both


def _fill(uds) -> None:
    tx = Transaction()
    rows = [
        ("/w/a.work", {"state": "done", "n": 3, "tags": ["x", "y", "x"], "who": "Tim"}),
        ("/w/b.work", {"state": "done", "n": 3.0, "tags": ["y"], "who": "tim"}),
        ("/w/c.work", {"state": "ready", "n": 7, "who": "Café"}),
        ("/w/d.work", {"state": "ready", "n": -1.5, "who": "Cafe", "flag": True}),
        ("/w/sub/e.work", {"state": "abc", "n": None, "flag": False}),
        ("/w/sub/f.note", {"state": "ab", "text": LONG + "one"}),
        ("/w/sub/g.note", {"state": "abd", "text": LONG + "two"}),
        ("/p/a.project", {"state": "done", "text": LONG + "one"}),
        ("/p/b.project", {"state": "Done"}),
    ]
    for path, data in rows:
        tx.put(path, data)
    uds.transaction(TIM, tx)
    uds.put(TIM, "/w/h.work", data={"state": "done"}, content={"text": "words"})


CASES = [
    {},
    {"under": "/w"},
    {"under": "/w/sub"},
    {"parent": "/w"},
    {"types": ["note"]},
    {"under": "/w", "types": ["work", "note"]},
    {"where": [["state", "=", "done"]]},
    {"where": [["state", "!=", "done"]]},
    {"where": [["state", "in", ["ready", "ab"]]]},
    {"where": [["state", ">", "ab"]]},            # finds "abc" and "abd", not "ab"
    {"where": [["state", ">=", "ab"]]},
    {"where": [["state", "<", "abd"]]},
    {"where": [["state", "<=", "ab"]]},
    {"where": [["state", "prefix", "ab"]]},
    {"where": [["n", "=", 3]]},
    {"where": [["n", ">", 0]]},
    {"where": [["n", "<=", -1.5]]},
    {"where": [["n", "=", None]]},
    {"where": [["flag", "exists"]]},
    {"where": [["flag", "exists", False]]},
    {"where": [["flag", "=", True]]},
    {"where": [["flag", ">", False]]},
    {"where": [["who", "=", "cafe"]]},            # text compares folded: accents off
    {"where": [["tags[]", "=", "x"]]},
    {"where": [["text", "=", LONG + "one"]]},      # past the bound: checked against each
    {"where": [["text", "prefix", LONG]]},
    {"where": [["text", "prefix", "unicode pre"]]},
    {"where": [["text", "!=", LONG + "one"]]},
    {"where": [["nobody", "=", 1]]},
    {"where": [["nobody", "!=", 1]]},
    {"where": [["state", "=", "done"], ["n", ">", 2]], "under": "/w"},
    {"group_by": "state"},
    {"group_by": "n"},
    {"group_by": "who"},
    {"group_by": "tags[]"},
    {"group_by": "text"},
    {"group_by": "flag", "under": "/w"},
    {"group_by": "_ancestors[]"},
    {"group_by": "nobody"},
]


@pytest.mark.parametrize("body", CASES, ids=[str(c) for c in CASES])
def test_the_index_counts_what_reading_each_item_counts(opened, body):
    engine, uds = opened
    _fill(uds)
    items = engine.items
    full = {**body, "caller": {"person": "tim"}}
    preds, prefix, parent, types = items._preds(full)
    each = items._count_each(preds, prefix, parent, types, body.get("group_by"), "tim", engine.store.lsn)
    counted = items.count(full)
    for out in (each, counted):
        out.pop("lsn")
    assert counted == each


def test_the_long_text_answers_are_exact(opened):
    _, uds = opened
    _fill(uds)
    assert uds.count(TIM, where=[["text", "=", LONG + "one"]]).items == 2
    assert uds.count(TIM, where=[["text", "=", LONG + "on"]]).items == 0
    assert uds.count(TIM, where=[["text", "prefix", LONG + "tw"]]).items == 1
    assert [i.path for i in uds.list(TIM, where=[["text", "=", LONG + "two"]]).items] == ["/w/sub/g.note"]


def test_no_item_is_read_when_every_condition_is_exact(opened, monkeypatch):
    engine, uds = opened
    _fill(uds)

    def never(*a, **k):
        raise AssertionError("an item was decoded")
    monkeypatch.setattr(engine.docs, "by_ordinal", never)
    monkeypatch.setattr(engine.docs, "get", never)
    assert uds.count(TIM).items == 10
    assert uds.count(TIM, "/w", where=[["state", "=", "done"], ["n", ">", 2]]).items == 2
    assert uds.count(TIM, where=[["flag", "exists", False]]).items == 8
    assert dict(uds.count(TIM, "/w", group_by="n").groups) == {3: 2, 7: 1, -1.5: 1, None: 1}
    found = uds.count(TIM)
    assert (found.embedded, found.waiting) == (0, 2)     # /w/h.work's content and /p/a.project's words


def test_a_reader_with_rights_is_counted_item_by_item(opened):
    engine, uds = opened
    _fill(uds)
    assert uds.count(Caller("ana")).items == 0       # no grant: nothing, read each


# --- the index's bound on long text ---------------------------------------------------

def _record(i: int, long_fields: int = 3) -> dict:
    data = {f"field_{k}": f"value {i} {k}" for k in range(40)}
    data.update({"id": f"r-{i}", "state": "done", "n": i, "tags": ["a", "b"]})
    for k in range(long_fields):
        data[f"text_{k}"] = (f"Run {i} said: " + "the result went on and on. " * 4000)[:100_000]
    return data


def test_a_long_field_is_keyed_by_a_bounded_prefix_folded_only_that_far(opened, monkeypatch):
    engine, uds = opened
    longest = []
    real = spoken_words.fold

    def fold(text):
        longest.append(len(text or ""))
        return real(text)
    monkeypatch.setattr(spoken_words, "fold", fold)
    uds.put(TIM, "/runs/r-1.run", data=_record(1))
    # Nothing folds the 100 KB: the index a bounded prefix, the record's
    # words their own bound.
    assert max(longest) <= max(encoding.FOLD_FIRST * 4, lexical.RECORD_WORDS + 64)
    monkeypatch.setattr(spoken_words, "fold", real)
    longest_key = max(len(k) for k in engine.docs.mem_index)
    assert longest_key <= FID.size + 1 + encoding.STRING_MAX
    text = _record(1)["text_0"]
    assert uds.count(TIM, where=[["text_0", "=", text]]).items == 1
    assert uds.count(TIM, where=[["text_0", "=", text[:-1]]]).items == 0
    assert uds.count(TIM, where=[["text_0", "in", [text, "x"]]]).items == 1
    assert uds.count(TIM, where=[["text_0", "prefix", text[:5000]]]).items == 1
    assert uds.count(TIM, where=[["text_0", "prefix", text[:5000] + "!"]]).items == 0


def test_a_record_with_long_fields_writes_about_as_fast_as_a_short_one(opened):
    _, uds = opened

    def p50(make, n=15):
        times = []
        for i in range(n):
            data = make(i)
            t = time.perf_counter()
            uds.put(TIM, f"/bench/{len(times)}-{id(make)}.run", data=data)
            times.append(time.perf_counter() - t)
        return statistics.median(times)
    short = p50(lambda i: _record(i, long_fields=0))
    long = p50(lambda i: _record(i, long_fields=3))
    # Three 100 KB fields add their bytes to the log, the hash and the
    # document, never a fold of 300 KB to the index; generous for a busy CI.
    assert long < short + 0.05, (short, long)
