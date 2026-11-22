"""A role's material in pages (pages.py; construction-drafts.md "Mark
rebuilt for speed", Tim 2026-11-21): the budget from the model's own
window, the files grouped so similar ones share pages, packing, and the
pages asked in parallel, a page that does not check asked again once and
then halved."""
import asyncio
import json

import pytest

from jarvis import data_paths, model_tiers, pages


def test_tokens_err_high():
    prose = "The work driver and the GitHub watcher come first, since the others use them."
    assert pages.tokens(prose) >= len(prose) / 4, "English runs about four characters a token"
    assert pages.tokens("señor") == 2, "counted in UTF-8 bytes"


def test_the_window_comes_from_codex_s_own_list_else_the_table():
    cache = data_paths.codex_home() / "models_cache.json"
    cache.write_text(json.dumps({"models": [
        {"slug": "gpt-6.1-terra", "context_window": 500_000,
         "effective_context_window_percent": 95}]}))
    assert model_tiers.context_window("codex", "gpt-6.1-terra") == 475_000
    assert model_tiers.context_window("codex", "gpt-6.2-sol") == model_tiers.WINDOW["codex"]["sol"]
    assert model_tiers.context_window("claude", "sonnet") == model_tiers.WINDOW["claude"]["sonnet"]
    pick = model_tiers.Pick("codex", "gpt-6.1-terra", 3, "t", ["gpt-6.1-terra", "gpt-6.2-sol"])
    assert pages.budget(pick).window == model_tiers.WINDOW["codex"]["sol"], \
        "the smallest window of any model the sweep may take"


def test_a_budget_keeps_half_the_window_and_a_margin_free():
    room = pages.Budget(window=1_000, answer_most=300)
    assert room.fits(500, 250) and not room.fits(501, 0), "the page and instructions: half"
    assert not room.fits(500, 251), "with the answer and a quarter's margin inside the window"
    assert not room.fits(100, 301), "never more than the model answers in one turn"


def _doc(key, sizes, brks=None, context=0):
    brks = brks or [0] * len(sizes)
    return pages.Doc(key, [pages.Atom([f"{key}{n}"], s, s, b)
                           for n, (s, b) in enumerate(zip(sizes, brks))], context)


def test_files_share_pages_and_a_long_one_is_split_at_its_sections():
    room = pages.Budget(window=1_000, answer_most=200)       # page: given 500, answer 200
    docs = [_doc("a", [40, 40]), _doc("b", [30]),
            _doc("long", [50, 50, 50, 50, 50, 50], [0, 0, 0, 0, 2, 0], context=40),
            _doc("c", [20])]
    packed = pages.pack(docs, fixed=100, room=room)
    for page in packed:
        assert room.fits(100 + page.given, page.answer)
    first = packed[0]
    assert [(e.doc.key, e.whole) for e in first.entries][:2] == [("a", True), ("b", True)]
    long_pieces = [e for p in packed for e in p.entries if e.doc.key == "long"]
    assert len(long_pieces) > 1 and not any(e.whole for e in long_pieces)
    assert [i for e in long_pieces for a in e.atoms for i in a.items] == \
        [f"long{n}" for n in range(6)], "every piece in order, nothing lost"
    assert [e.atoms[0].items[0] for e in long_pieces] == ["long0", "long1", "long4"], \
        "the second piece could hold long1 to long4; it ends before the section break"
    assert long_pieces[2].atoms[0].brk == pages.SECTION
    assert all(e.context for e in long_pieces), "each piece given its whole file"
    assert [i for p in packed for i in p.items] == \
        ["a0", "a1", "b0"] + [f"long{n}" for n in range(6)] + ["c0"]


def test_material_that_cannot_fit_is_refused():
    room = pages.Budget(window=1_000, answer_most=200)
    with pytest.raises(pages.TooLarge):
        pages.pack([_doc("a", [10])], fixed=501, room=room)
    with pytest.raises(pages.TooLarge):
        pages.pack([_doc("a", [450])], fixed=100, room=room)


def test_halves_keep_order_and_give_a_cut_file_its_context():
    page = pages.Page([pages.Entry(_doc("a", [1, 1, 1], context=5),
                                   list(_doc("a", [1, 1, 1]).atoms), True, True)])
    one, two = page.halves()
    assert [i for p in (one, two) for i in p.items] == ["a0", "a1", "a2"]
    assert not one.entries[0].whole and one.entries[0].context
    assert pages.Page([pages.Entry(_doc("x", [1]), _doc("x", [1]).atoms, True)]).halves() is None


def test_files_are_grouped_by_type_then_folder_then_meaning():
    files = [("prose", "files", "a.md", "cats"), ("prose", "files", "b.md", "dogs"),
             ("prose", "files", "c.md", "cats again"), ("data", "files", "d.json", "x"),
             ("prose", "jarvis", "e.md", "cats")]
    vectors = {"cats": [1.0, 0.0], "dogs": [0.0, 1.0], "cats again": [0.9, 0.1], "x": [0.5, 0.5]}
    order = pages.grouped(files, kind=lambda f: f[0], folder=lambda f: f[1],
                          text=lambda f: f[3], name=lambda f: f[2],
                          embed=lambda texts: [vectors[t] for t in texts])
    assert [f[2] for f in order] == ["d.json", "a.md", "c.md", "b.md", "e.md"]
    plain = pages.grouped(files, kind=lambda f: f[0], folder=lambda f: f[1],
                          text=lambda f: f[3], name=lambda f: f[2], embed=lambda texts: None)
    assert [f[2] for f in plain] == ["d.json", "a.md", "b.md", "c.md", "e.md"], \
        "by name where there are no embeddings"


def test_run_asks_again_once_then_halves_and_raises_what_cannot_check():
    calls = []

    class S:
        async def open(self):
            pass

        async def fresh(self):
            pass

        async def close(self):
            pass

    async def ask(session, page):
        calls.append(len(page.items))
        if len(page.items) > 1:
            raise pages.NotChecked("too many")
        return page.items

    page = pages.Page([pages.Entry(_doc("a", [1, 1]), _doc("a", [1, 1]).atoms, True)])
    report = pages.Report()
    got = asyncio.run(pages.run([page], first=S(), make=lambda: None, parallel=3, ask=ask,
                                failed=RuntimeError, report=report))
    assert sorted(i for r in got for i in r) == ["a0", "a1"] and calls[:2] == [2, 2]
    assert (report.calls, report.again, report.halved) == (4, 1, 1)

    async def never(session, page):
        raise pages.NotChecked("no")
    with pytest.raises(RuntimeError, match="no"):
        asyncio.run(pages.run([pages.Page([pages.Entry(_doc("b", [1]), _doc("b", [1]).atoms,
                                                       True)])],
                              first=S(), make=lambda: None, parallel=1, ask=never,
                              failed=RuntimeError))


def test_the_reserve_is_the_expected_answer_never_more_than_the_most_it_can_take():
    # Pages filled by a sparse-answer reserve (2026-11-21): the measured
    # output per input token times the safety factor, capped by the most
    # the page's answer can take and by the model's turn.
    room = pages.Budget(window=1_000, answer_most=300, expected=0.5)
    assert room.reserve(400, 1_000) == 200, "half of what is given"
    assert room.reserve(400, 50) == 50, "never more than the most it can take"
    assert room.fits(500, 1_000) and not room.fits(501, 0)
    assert not pages.Budget(window=1_000, answer_most=300).fits(400, 400), \
        "with no expected answer the whole most is reserved"
    pick = model_tiers.Pick("codex", "gpt-6.1-terra", 3, "t", ["gpt-6.1-terra"])
    assert pages.budget(pick, "marking").expected == pytest.approx(
        pages.EXPECTED_ANSWER["marking"] * pages.SAFETY) == pytest.approx(0.44)
    assert pages.budget(pick, "classification").expected == pytest.approx(0.6), \
        "the smart passes' measured answer, the smaller one"
    assert pages.budget(pick, "retrieval").expected is None
    assert model_tiers.PARALLEL == {"claude": 3, "codex": 10}


def test_pages_fill_toward_half_the_window_with_a_small_answer():
    room = pages.Budget(window=10_000, answer_most=10_000, expected=0.5)
    # Each atom given 10 tokens, its answer at most 40 (more than it gives).
    docs = [pages.Doc(f"d{n}", [pages.Atom([f"d{n}"], 10, 40)]) for n in range(1_000)]
    filled = pages._greedy(docs, 100, room, None)
    assert 100 + filled[0].given > 0.45 * room.window, "a page fills toward half the window"
    packed = pages.pack(docs, fixed=100, room=room)
    assert len(packed) == len(filled), "the fewest pages the budget allows, evened"
    assert all(room.fits(100 + p.given, p.answer) for p in packed)
    assert max(p.given for p in packed) - min(p.given for p in packed) <= 40


def test_a_rate_limited_call_pauses_every_worker_and_is_asked_again():
    import time as _time
    hits = []

    class S:
        async def open(self):
            pass

        async def fresh(self):
            pass

        async def close(self):
            pass

    async def ask(session, page):
        if not hits:
            hits.append(_time.monotonic())
            raise pages.RateLimited("slow down", resets_at=_time.time() + 0.2)
        hits.append(_time.monotonic())
        return page.items

    one = [pages.Page([pages.Entry(_doc(k, [1]), _doc(k, [1]).atoms, True)]) for k in "ab"]
    report = pages.Report()
    got = asyncio.run(pages.run(one, first=S(), make=lambda: _made(S), parallel=2, ask=ask,
                                failed=RuntimeError, report=report, wait_most=5))
    assert sorted(i for r in got for i in r) == ["a0", "b0"]
    assert report.rate_limited == 1 and report.waited > 0
    assert report.summary()["rate_limited"] == 1
    with pytest.raises(pages.RateLimited):
        async def always(session, page):
            raise pages.RateLimited("hours", resets_at=_time.time() + 3_600)
        asyncio.run(pages.run(one[:1], first=S(), make=lambda: _made(S), parallel=1,
                              ask=always, failed=RuntimeError, wait_most=5))


async def _made(cls):
    return cls()


def test_the_answers_are_shared_evenly_across_the_workers():
    room = pages.Budget(window=1_000_000, answer_most=1_000_000, expected=0.5)
    docs = [pages.Doc(f"d{n}", [pages.Atom([f"d{n}"], 10, 20)]) for n in range(10_000)]
    packed = pages.pack(docs, fixed=100, room=room, spread=10)
    assert len(packed) == 10, "200,000 tokens of answer over ten workers"
    assert all(p.answer <= 20_000 for p in packed)
    small = [pages.Doc(f"s{n}", [pages.Atom([f"s{n}"], 10, 20)]) for n in range(50)]
    assert len(pages.pack(small, fixed=100, room=room, spread=10)) == 10, \
        "small material is spread across the workers too: no floor of its own"
    assert len(pages.pack(small, fixed=100, room=room, spread=1)) == 1
    assert pages.share([pages.Atom([], 1, 5_000), pages.Atom([], 1, 10)], 10) == 5_000, \
        "never less than one atom's answer"


def test_the_budget_still_bounds_a_page_when_the_share_is_larger():
    room = pages.Budget(window=4_000, answer_most=4_000, expected=0.5)
    docs = [pages.Doc(f"d{n}", [pages.Atom([f"d{n}"], 100, 10)]) for n in range(100)]
    packed = pages.pack(docs, fixed=100, room=room, spread=2)
    assert len(packed) > 2 and all(100 + p.given <= 2_000 for p in packed), \
        "every page at most half the window, whatever the share"


def test_never_more_pages_than_workers_while_the_budget_lets_it():
    # package-870e45145b2d: packed greedily to a share, 11 pages on 10
    # workers, so one page waited for a second wave. Uneven files, each to
    # go whole where it can.
    room = pages.Budget(window=1_000_000, answer_most=1_000_000, expected=0.5)
    sizes = [900, 40, 1_300, 75, 600, 20, 2_100, 310, 55, 870, 1_500, 30, 260, 700, 95]
    docs = [_doc(f"d{n}", [s // 5] * 5) for n, s in enumerate(sizes)]
    packed = pages.pack(docs, fixed=100, room=room, spread=10)
    assert len(packed) == 10, "exactly one page a worker"
    loads = [p.answer for p in packed]
    assert max(loads) <= 1.5 * sum(loads) / 10, "the answers shared evenly"
    assert [i for p in packed for i in p.items] == [i for d in docs for a in d.atoms
                                                    for i in a.items], "in order, none lost"
    few = [_doc("a", [5, 5]), _doc("b", [5])]
    assert len(pages.pack(few, fixed=100, room=room, spread=10)) == 3, "no more pages than atoms"
    one = [_doc("big", [10] * 20)]
    assert len(pages.pack(one, fixed=100, room=room, spread=4)) == 4, \
        "a doc that would go whole is cut so every worker has a page"


def test_material_the_budget_cannot_fit_in_the_workers_pages_takes_the_fewest_it_allows():
    room = pages.Budget(window=4_000, answer_most=4_000, expected=0.5)
    docs = [pages.Doc(f"d{n}", [pages.Atom([f"d{n}"], 100, 10)]) for n in range(100)]
    fewest = len(pages.pack(docs, fixed=100, room=room, spread=1))
    packed = pages.pack(docs, fixed=100, room=room, spread=2)
    assert len(packed) == fewest > 2
    assert all(room.fits(100 + p.given, p.answer) for p in packed)
    assert pages.pages_for(fewest=3, atoms=100, spread=10) == 10
    assert pages.pages_for(fewest=12, atoms=100, spread=10) == 12
    assert pages.pages_for(fewest=1, atoms=4, spread=10) == 4


def test_pages_fed_while_the_workers_ask_are_asked_as_they_come():
    # The mark step's pipeline: a page classified as soon as it is marked,
    # its sentences arriving while the rest are still marked.
    class S:
        async def open(self):
            pass

        async def fresh(self):
            pass

        async def close(self):
            pass

    order = []

    async def ask(session, page):
        await asyncio.sleep(0.01)
        order.append(page.items[0])
        return page.items

    def page_of(key):
        return pages.Page([pages.Entry(_doc(key, [1]), _doc(key, [1]).atoms, True)])

    async def go():
        feed = pages.Feed()
        answered = []
        run = asyncio.ensure_future(pages.run(
            [page_of("a")], first=S(), make=lambda: _made(S), parallel=3, ask=ask,
            failed=RuntimeError, feed=feed, on_answer=lambda p, got: answered.append(got)))
        await asyncio.sleep(0.05)
        assert answered == [["a0"]] and not run.done(), "it waits for more until closed"
        assert feed.free() == 3, "every worker free for the next page"
        feed.put(page_of("b"))
        feed.put(page_of("c"))
        await asyncio.sleep(0.05)
        feed.close()
        return await run, answered
    got, answered = asyncio.run(go())
    assert sorted(i for r in got for i in r) == ["a0", "b0", "c0"]
    assert len(answered) == 3 and order[0] == "a0"
