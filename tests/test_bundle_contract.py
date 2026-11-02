"""The collaboration-bundles contract (jarvis/bundle_contract.py): member ids,
continuations, location shapes, and the brain tool table every lane binds."""
from __future__ import annotations

import pytest

from jarvis import bundle_contract as b

SHA = "a" * 64


def test_a_member_id_is_stable_and_changes_with_the_bytes_or_the_path():
    assert b.member_id(SHA, "brief.md") == b.member_id(SHA, "brief.md")
    assert b.member_id(SHA, "brief.md") != b.member_id("b" * 64, "brief.md")
    assert b.member_id(SHA, "brief.md") != b.member_id(SHA, "notes/brief.md")
    assert b.member_id(SHA, "").startswith("m") and len(b.member_id(SHA, "")) == 25


def test_a_continuation_round_trips_only_for_its_member_and_reader_version():
    token = b.encode_continuation("m1", 2, {"page": 3})
    assert b.decode_continuation(token, "m1", 2) == {"page": 3}
    for member, version in (("m2", 2), ("m1", 3)):
        with pytest.raises(ValueError):
            b.decode_continuation(token, member, version)
    for bad in ("", "!!!", "x" * (b.CONTINUATION_MAX_CHARS + 1)):
        with pytest.raises(ValueError):
            b.decode_continuation(bad, "m1", 2)


def test_a_text_chunk_takes_each_location_kind():
    for location in ({"kind": "page", "page": 2}, {"kind": "lines", "start": 1, "end": 40},
                     {"kind": "sheet", "sheet": "Data", "start_row": 1, "end_row": 9},
                     {"kind": "slide", "slide": 4}):
        chunk = b.TextChunk.model_validate({"location": location, "text": "x"})
        assert chunk.model_dump(mode="json")["location"] == location


def test_every_brain_tool_names_its_route_and_every_reader_of_content_taints():
    for name, tool in b.BRAIN_TOOLS.items():
        assert tool.route.split(" ", 1)[0] in {"GET", "POST", "PUT"}, name
        if tool.route.startswith("GET"):
            assert tool.taints, f"{name} reads a file someone sent"


def test_every_reason_has_its_words():
    from typing import get_args
    assert set(get_args(b.Reason)) == set(b.REASONS_SHOWN)
    assert set(get_args(b.RefusalCode)) == set(b.REFUSAL_STATUS)
