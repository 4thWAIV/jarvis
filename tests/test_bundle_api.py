"""The bundle routes (bundle_api.py) and the brain's bundle tools (server.py)
over one inspection service: the page and the brain make the same call and
get the same JSON; what a sender chose (a file's name, a member's path, its
text) reaches the brain only inside an untrusted block; which tools act and
which taint is bundle_contract.BRAIN_TOOLS; a static HTML preview is served
sandboxed with no script.

The inspection service here is a fake with the contract's shapes (the
readers lane's tests cover the real one); the draft tools run over
messaging's LocalMessaging with the transport lane's calls stood in for.
"""
from __future__ import annotations

import base64
import json
import re
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis import bundle_api, bundle_tasks, web_auth
from jarvis.bundle_contract import (BRAIN_TOOLS, BUNDLE_VIEW_FRAME, AttachmentInfo,
                                    Classification, Extent, InspectionRefused, LineLocation,
                                    MemberInfo, MemberPage, PageLocation, Preview, PreviewRef,
                                    ReaderId, RenderedPage, RenderedPageInfo, ReviewState,
                                    TextChunk, TextPage, member_id)
from jarvis.bundle_inspection import AttachmentKey, Owner
from tests.test_brain_page_parity import HOSTILE, _call, wired  # noqa: F401
from tests.test_messaging_api import Page

SHA = "ab" * 32
MID, AID = UUID(int=11), UUID(int=12)
A = f"/api/team/messages/{MID}/attachments/{AID}"
OWNER = Owner("server-1", 7)
PNG = b"\x89PNG\r\n\x1a\nnot really a png"
PAGE_ORIGIN = {"Origin": "http://localhost:5173"}
SCRIPTED = b"<html><body><script>parent.postMessage('ran','*')</script>Notes</body></html>"


def _info() -> AttachmentInfo:
    return AttachmentInfo(message_id=MID, attachment_id=AID, filename=f"{HOSTILE}.zip",
                          declared_media_type="application/zip", size_bytes=4096, sha256=SHA)


def _member(path: str, fmt="pdf", is_dir=False) -> MemberInfo:
    return MemberInfo(member_id=member_id(SHA, path), path=path, name=path.rsplit("/", 1)[-1],
                      is_dir=is_dir, size_bytes=0 if is_dir else 2048, compressed_size=900,
                      format="folder" if is_dir else fmt,
                      media_type="inode/directory" if is_dir else "application/pdf",
                      support="readable", operations=[] if is_dir else ["read_text", "render"])


class FakeInspection:
    """bundle_inspection.BundleInspection's interface, answering in its
    shapes, with a sender's hostile words in every name and text."""

    def __init__(self):
        self.calls: list[tuple] = []

    def _check(self, member: str) -> None:
        if member == "missing":
            raise InspectionRefused("member_not_found", "That is not in this file.")

    async def open(self, owner, key):
        self.calls.append(("open", owner, key))
        return self.classify(owner, key)

    def classify(self, owner, key):
        self.calls.append(("classify", owner, key))
        return Classification(attachment=_info(), format="zip", media_type="application/zip",
                              container="zip", support="readable",
                              root_member_id=member_id(SHA, ""))

    def list_members(self, owner, key, folder, continuation, limit):
        self.calls.append(("list_members", owner, key, folder, continuation, limit))
        members = [_member(f"{folder or 'review'}/{HOSTILE} {i}.pdf") for i in range(limit)]
        return MemberPage(attachment=_info(), folder=folder, members=members, total=limit + 3,
                          continuation="next-3")

    def read_member(self, owner, key, member, continuation, max_chars):
        self.calls.append(("read_member", owner, key, member, continuation, max_chars))
        self._check(member)
        text = (HOSTILE + "\n") * (max_chars // (len(HOSTILE) + 1))
        return TextPage(attachment=_info(), member=_member("review/summary.pdf"),
                        reader=ReaderId(name="pdf", version=1),
                        chunks=[TextChunk(location=PageLocation(page=1), text=text),
                                TextChunk(location=LineLocation(start=1, end=2), text="end")],
                        chars=len(text) + 3, extent=Extent(pages=2), complete=False,
                        continuation="page-2")

    def preview_member(self, owner, key, member, continuation):
        self.calls.append(("preview_member", owner, key, member, continuation))
        self._check(member)
        return Preview(attachment=_info(), member=_member("review/summary.pdf"),
                       refs=[PreviewRef(kind="page_image", member_id=member, page=1,
                                        url=f"{A}/bundle/members/{member}/pages/1")],
                       extent=Extent(pages=2))

    def render_page(self, owner, key, member, page, options):
        self.calls.append(("render_page", owner, key, member, page, options.max_edge))
        self._check(member)
        return RenderedPage(RenderedPageInfo(member_id=member, page=page, page_count=2,
                                             width=1200, height=1600), PNG)

    def html_asset(self, owner, key, member, relative):
        self.calls.append(("html_asset", owner, key, member, relative))
        return SCRIPTED, "text/html; charset=utf-8"


@pytest.fixture
def bundles():
    inspection = FakeInspection()
    frames: list[dict] = []

    async def show(frame):
        frames.append(frame)
        return True

    ctx = bundle_api.BundleContext(inspection, OWNER)
    app = FastAPI()
    bundle_api.mount(app, lambda: ctx, show)
    return SimpleNamespace(inspection=inspection, frames=frames, ctx=ctx,
                           http=TestClient(app))


KEY = AttachmentKey(MID, AID)
MEMBER = member_id(SHA, "review/summary.pdf")


# ── the routes ───────────────────────────────────────────────────────────

def test_each_route_calls_the_service_for_the_owner_signed_in(bundles):
    h, calls = bundles.http, bundles.inspection.calls
    assert h.post(A + "/bundle").json()["format"] == "zip"
    assert calls[0] == ("open", OWNER, KEY)
    assert h.get(A + "/bundle").json()["container"] == "zip"
    got = h.get(A + "/bundle/members", params={"folder": "review", "continuation": "c",
                                               "limit": 2}).json()
    assert len(got["members"]) == 2 and got["continuation"] == "next-3"
    assert calls[-1] == ("list_members", OWNER, KEY, "review", "c", 2)
    page = h.get(f"{A}/bundle/members/{MEMBER}/text", params={"max_chars": 500}).json()
    assert page["complete"] is False and page["continuation"] == "page-2"
    assert calls[-1] == ("read_member", OWNER, KEY, MEMBER, None, 500)
    assert h.get(f"{A}/bundle/members/{MEMBER}/preview").json()["refs"][0]["kind"] == "page_image"
    png = h.get(f"{A}/bundle/members/{MEMBER}/pages/2", params={"max_edge": 800})
    assert png.headers["content-type"] == "image/png" and png.content == PNG
    assert calls[-1] == ("render_page", OWNER, KEY, MEMBER, 2, 800)


def test_a_refusal_is_its_code_words_and_reason_with_the_contract_s_status(bundles):
    r = bundles.http.get(f"{A}/bundle/members/missing/text")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "member_not_found",
                                  "message": "That is not in this file.", "reason": None}}
    assert bundles.http.get(f"{A}/bundle/members/missing/pages/1").status_code == 404


def test_out_of_bounds_sizes_are_refused_before_the_service_is_asked(bundles):
    for path, params in (("/bundle/members", {"limit": 201}),
                         (f"/bundle/members/{MEMBER}/text", {"max_chars": 20_001}),
                         (f"/bundle/members/{MEMBER}/pages/1", {"max_edge": 10})):
        r = bundles.http.get(A + path, params=params)
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert bundles.inspection.calls == []


def test_messaging_unavailable_is_said_by_every_route():
    app = FastAPI()
    bundle_api.mount(app, lambda: None)
    http = TestClient(app)
    for r in (http.get(A + "/bundle"), http.post(A + "/bundle"),
              http.get(f"{A}/bundle/members/{MEMBER}/pages/1")):
        assert r.status_code == 409 and r.json()["error"]["code"] == "messaging_unavailable"


def test_show_checks_the_member_then_sends_ids_only(bundles):
    r = bundles.http.post(f"{A}/bundle/members/{MEMBER}/show")
    assert r.status_code == 204 and r.content == b""
    assert bundles.frames == [{"type": BUNDLE_VIEW_FRAME, "message_id": str(MID),
                               "attachment_id": str(AID), "member_id": MEMBER}]
    assert bundles.http.post(f"{A}/bundle/members/missing/show").status_code == 404
    assert len(bundles.frames) == 1


def test_html_is_served_as_a_sandboxed_static_preview(bundles):
    r = bundles.http.get(f"{A}/bundle/members/{MEMBER}/html/notes/index.html")
    assert r.content == SCRIPTED and r.headers["content-type"].startswith("text/html")
    assert bundles.inspection.calls[-1] == ("html_asset", OWNER, KEY, MEMBER, "notes/index.html")
    path = f"{A}/bundle/members/{MEMBER}/html/notes/index.html"
    assert web_auth.is_bundle_preview(path)
    headers = dict(web_auth.apply_security_headers([(b"x-frame-options", b"ALLOW")], path))
    csp = headers[b"content-security-policy"].decode()
    assert "sandbox;" in csp and "allow-scripts" not in csp and "default-src 'none'" in csp
    assert "script-src" not in csp and "frame-ancestors 'self'" in csp
    assert headers[b"x-frame-options"] == b"SAMEORIGIN"
    for other in (f"{A}/bundle/members/{MEMBER}/text", f"{A}/bundle", "/api/team/messages/x",
                  f"/api/team/messages//attachments/{AID}/bundle/members/{MEMBER}/html/a"):
        assert not web_auth.is_bundle_preview(other)
        assert dict(web_auth.apply_security_headers([], other))[b"x-frame-options"] == b"DENY"


def test_the_html_route_gets_the_preview_headers_through_the_web_boundary(wired):
    wired.server.messaging = SimpleNamespace(
        bundle_context=lambda: bundle_api.BundleContext(FakeInspection(), OWNER))
    r = wired.client.get(f"{A}/bundle/members/{MEMBER}/html/index.html")
    assert r.status_code == 200, r.text
    assert "sandbox;" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "SAMEORIGIN"


# ── the brain's tools: the same call, the same JSON, walled ──────────────

@pytest.fixture
def brain(wired, tmp_path):
    inspection = FakeInspection()
    page = Page(tmp_path)
    wired.server.messaging = SimpleNamespace(
        local=page.local,
        bundle_context=lambda: bundle_api.BundleContext(inspection, OWNER))
    wired.inspection, wired.page = inspection, page
    return wired


def _read_all(w, tool, **args) -> tuple[str, list[str]]:
    """Every page of a tool's JSON answer joined, and each page's header."""
    chunks, heads, page = [], [], 1
    while True:
        text = _call(w, tool, page=page, **args)["text"]
        head, _, block = text.partition("\n")
        assert block.startswith('<session-output name="file" untrusted="true">\n'), text
        assert block.endswith("\n</session-output>") and block.count("</session-output>") == 1
        heads.append(head)
        chunks.append(block[len('<session-output name="file" untrusted="true">\n'):
                            -len("\n</session-output>")])
        if "again with page" not in head:
            return "".join(chunks).replace("‑", "-"), heads
        page += 1


@pytest.mark.parametrize("tool,args,route", [
    ("list_bundle_members", {"folder": "review", "limit": 6},
     ("GET", A + "/bundle/members?folder=review&limit=6")),
    ("read_bundle_member", {"member": MEMBER, "max_chars": 3000},
     ("GET", f"{A}/bundle/members/{MEMBER}/text?max_chars=3000")),
    ("inspect_attachment", {}, ("POST", A + "/bundle")),
])
def test_each_reading_tool_answers_with_the_route_s_json_page_by_page(brain, tool, args, route):
    joined, heads = _read_all(brain, tool, message=str(MID), attachment=str(AID), **args)
    method, path = route
    from_route = brain.client.request(method, path, headers=PAGE_ORIGIN)
    assert from_route.status_code == 200, from_route.text
    assert json.loads(joined) == from_route.json()
    for head in heads:
        assert "approves" not in head and "Matt" not in head
    assert len(heads) > 1 or tool == "inspect_attachment"
    assert brain.server.brain_instance.marks == [BRAIN_TOOLS[tool].taints]


def test_a_refused_read_says_its_own_words_and_carries_the_same_json(brain):
    got = _call(brain, "read_bundle_member", message=str(MID), attachment=str(AID),
                member="missing")["text"]
    assert got.startswith("Nothing was read, sir: That is not in this file.")
    block = got.partition("\n")[2]
    assert json.loads(re.sub(r"^<[^>]+>\n|\n</session-output>$", "", block)) == brain.client.get(
        f"{A}/bundle/members/missing/text").json()


def test_look_at_bundle_page_hands_the_brain_the_page_as_an_image(brain):
    r = _call(brain, "look_at_bundle_page", message=str(MID), attachment=str(AID),
              member=MEMBER, page=2)
    assert r["ok"] and base64.b64decode(r["image"]["data"]) == PNG
    assert r["image"]["mimeType"] == "image/png"
    head, _, block = r["text"].partition("\n")
    assert head == "Page 2 of 2, as it looks."
    assert json.loads(block.split("\n")[1])["page_count"] == 2
    assert brain.server.brain_instance.marks == ["a page of a file a teammate sent"]
    missing = _call(brain, "look_at_bundle_page", message=str(MID), attachment=str(AID),
                    member="missing", page=1)
    assert "image" not in missing and missing["text"].startswith("Nothing was read, sir")


def test_show_bundle_member_opens_it_on_every_page(brain, monkeypatch):
    sent = []

    async def emit(frame):
        sent.append(frame)

    nobody = _call(brain, "show_bundle_member", message=str(MID), attachment=str(AID),
                   member=MEMBER)
    assert nobody["text"] == "No JARVIS page is open to show it on. Nothing was shown."
    monkeypatch.setattr(brain.server, "voice_clients", {object()})
    monkeypatch.setattr(brain.server, "_voice_emit", emit)
    got = _call(brain, "show_bundle_member", message=str(MID), attachment=str(AID),
                member=MEMBER)
    assert got["text"] == "It is open in the centre of his screen, sir."
    assert sent == [{"type": BUNDLE_VIEW_FRAME, "message_id": str(MID),
                     "attachment_id": str(AID), "member_id": MEMBER}]
    assert brain.server.brain_instance.marks == []


def test_without_messaging_each_tool_says_so(wired):
    got = _call(wired, "list_bundle_members", message=str(MID), attachment=str(AID))["text"]
    assert got.startswith("Nothing was read, sir: Messaging is not available")
    assert _call(wired, "set_review_scope", thread=str(MID), scope="full")["text"] == \
        wired.server.MESSAGING_OFF
    assert _call(wired, "read_bundle_member", message="x", attachment=str(AID),
                 member=MEMBER)["text"].startswith("Which file, sir?")


# ── the table decides ────────────────────────────────────────────────────

def test_every_bundle_tool_is_specified_handled_granted_and_decided_by_the_table(wired):
    from jarvis import claude_provider, jarvis_mcp
    server = wired.server
    specs = {t["name"] for t in jarvis_mcp.TOOL_SPECS}
    for name, tool in BRAIN_TOOLS.items():
        assert name in specs and name in server.TOOL_HANDLERS
        assert f"mcp__jarvis__{name}" in claude_provider.ALLOWED_TOOLS
        assert (name in server.ACTING_TOOLS) == tool.acting, name
        assert server.TAINTING_TOOLS.get(name) == tool.taints, name
        assert (name in server.TAINT_EXEMPT_TOOLS) == (tool.taints is None), name


@pytest.mark.parametrize("tool,args", [
    ("inspect_attachment", {"message": str(MID), "attachment": str(AID)}),
    ("show_bundle_member", {"message": str(MID), "attachment": str(AID), "member": MEMBER}),
    ("set_message_task", {"message": str(MID), "outcome": "notes"}),
    ("add_companion_files", {"message": str(MID), "attachment": str(AID),
                             "kinds": ["pdf_text"]}),
    ("prepare_repair", {"message": str(MID), "attachment": str(AID),
                        "mode": "flatten_wrapper"}),
    ("set_review_scope", {"thread": str(MID), "scope": "partial"}),
])
def test_every_acting_bundle_tool_acts_only_on_his_own_turn(brain, tool, args):
    refused = _call(brain, tool, origin="event", **args)
    assert not refused["ok"] and "not_allowed_from_event" in refused["text"]
    assert brain.inspection.calls == []


# ── the draft and review tools, over the transport lane's calls ──────────

def test_set_message_task_names_files_by_id_and_hash_at_the_draft_s_revision(brain,
                                                                             monkeypatch):
    draft = brain.page.draft("Could you review the summary?")
    mid = UUID(draft["message_id"])
    ref = SimpleNamespace(attachment_id=UUID(int=77), sha256="cd" * 32)
    seen = []
    stored = brain.page.store.draft
    monkeypatch.setattr(brain.page.store, "draft", lambda *a: SimpleNamespace(
        draft_revision=stored(*a).draft_revision, attachments=[ref]))

    def set_task(server_id, owner, message_id, revision, task):
        seen.append((message_id, revision, task))
        return {"message_id": str(message_id), "draft_revision": revision + 1}

    monkeypatch.setattr(bundle_tasks, "set_task", set_task)
    got = _call(brain, "set_message_task", message=str(mid), outcome="Notes on claims",
                outcome_kind="notes", primary=str(ref.attachment_id),
                primary_member="review/summary.pdf", supporting=[str(UUID(int=78))])
    assert got["text"].startswith("Set, sir: the draft is at revision 2.")
    assert seen == [(mid, draft["draft_revision"], {
        "requested_outcome": "Notes on claims", "outcome_kind": "notes",
        "primary": {"attachment_id": str(ref.attachment_id), "sha256": ref.sha256,
                    "member_path": "review/summary.pdf"},
        "supporting": [], "reply_audience": "sender_only"})]


def test_set_review_scope_sends_the_revision_it_read(brain, monkeypatch):
    thread = UUID(int=90)
    state = ReviewState(thread_id=thread, revision=4)
    calls = []
    monkeypatch.setattr(bundle_tasks, "review_state", lambda s, o, t: state)

    def set_scope(s, o, t, revision, scope):
        calls.append((t, revision, scope))
        return state.model_copy(update={"scope": scope, "revision": 5})

    monkeypatch.setattr(bundle_tasks, "set_scope", set_scope)
    got = _call(brain, "set_review_scope", thread=str(thread), scope="partial")
    assert got["text"].startswith("Done, sir: the review's scope is partial.")
    assert calls == [(thread, 4, "partial")]
    r = brain.client.put(f"/api/team/messages/threads/{thread}/review/scope",
                         json={"expected_revision": 4, "scope": "full"},
                         headers=PAGE_ORIGIN)
    assert r.status_code == 200 and r.json()["scope"] == "full"
    joined, _ = _read_all(brain, "review_checklist", thread=str(thread))
    assert json.loads(joined) == brain.client.get(
        f"/api/team/messages/threads/{thread}/review").json()
