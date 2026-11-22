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
