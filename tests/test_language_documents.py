import json
from pathlib import Path

import pytest

from jarvis import language_documents as documents, language_kit as kit, work_store
from tests.test_language_kit import kit as installed_kit


@pytest.fixture
def draft(installed_kit, tmp_path):
    kit.activate(installed_kit)
    work = work_store.create("Architecture review", "folder", str(tmp_path))
    # The upstream architecture-language definition exercises the actual kit;
    # this smaller architecture describes one declaration using that language.
    source = '''scope: [review]
language: arch
import: [[metalang]]
title: A proposed document store
declare:
- constant:
    id: DocumentStore
    is: element
    value:
      form: record
      does: Owns an immutable document revision.
      reach: internal
      exactness: loose
      groups: none
      members: none
      sits: {package: jarvis, assembly: none, namespace: none}
      relates: {implements: none, answers: none, holds: none}
'''
    return dict(source=source, readable_markdown="""# A proposed document store

This proposed architecture has one declaration, DocumentStore. It is an internal record in the jarvis package. It owns an immutable document revision and uses loose exactness. No members, groups, assembly, namespace, implementation, answer or holding relationship are declared. This is a proposed language model, not a code correspondence or approval claim.

## Package materials
source.yaml is the canonical architecture above. index.html is its unchanged upstream rendered view. check.json records language conformance and source/view hashes; it does not establish code correspondence or approval. canonical.json binds document identity, revision, Work and author provenance. UNDERSTANDING.md is this author-supplied explanation. package.json names the entry, this brief and the material-to-understanding associations. No package file establishes human approval.
""", work_id=work.id, scope_revision="review-contract-v1", author="review-agent",
                coverage={"constructs": ["forms", "relations"], "declarations": ["DocumentStore"],
                          "unresolved": [], "outside_language": ["No code correspondence claim is made."]})


def test_immutable_revisions_work_linkage_and_restart(draft):
    service = documents.Documents()
    original = service.validate(**draft)
    assert original["check"]["ok"] and original["metadata"]["status"] == "proposed"
    updated = service.validate(**{**draft, "source": draft["source"].replace("record", "class"), "readable_markdown": draft["readable_markdown"].replace("record", "class")},
                               document_id=original["document_id"], parent_revision=original["revision"])
    assert original["source_revision"] != updated["source_revision"]
    assert updated["parent_revision"] == original["revision"]
    recovered = documents.Documents().retrieve(document_id=original["document_id"], revision=original["revision"])
    assert recovered == original
    assert recovered["work_id"] == draft["work_id"]
    assert Path(recovered["references"]["source"]).read_text() == draft["source"]


def test_stale_render_and_stale_source_edit_are_rejected(draft):
    service = documents.Documents()
    original = service.validate(**draft)
    with pytest.raises(documents.DocumentError, match="different source"):
        service.render(document_id=original["document_id"], revision=original["revision"], source_revision="stale")
    with pytest.raises(documents.DocumentError, match="stale"):
        service.validate(**draft, document_id=original["document_id"], parent_revision="stale")


@pytest.mark.parametrize("replacement, diagnostic", [("spaceship", "not a member"), ("pending", "pending value")])
def test_draft_keeps_upstream_diagnostics(draft, replacement, diagnostic):
    service = documents.Documents()
    source = (draft["source"].replace("members: none", "members: pending") if replacement == "pending"
              else draft["source"].replace("form: record", "form: " + replacement))
    result = service.validate(**{**draft, "source": source})
    assert not result["check"]["ok"] and diagnostic in result["check"]["diagnostics"]
    assert result["references"]["view"]
    with pytest.raises(documents.DocumentError, match="no validated render"):
        service.render(document_id=result["document_id"], revision=result["revision"], source_revision=result["source_revision"])


def test_unknown_language_and_coverage_omissions_are_visible(draft):
    service = documents.Documents()
    result = service.validate(**{**draft, "source": draft["source"].replace("language: arch", "language: evolang")})
    assert not result["check"]["ok"] and "names no file" in result["check"]["diagnostics"]
    coverage = {**draft["coverage"], "declarations": [], "unresolved": ["Member ownership needs an author."]}
    result = service.validate(**{**draft, "coverage": coverage})
    assert not result["check"]["ok"]
    assert any("every declaration" in p for p in result["check"]["problems"])
    assert any("Member ownership" in p for p in result["check"]["problems"])


def test_changed_source_or_render_cannot_claim_original_revision(draft):
    result = documents.Documents().validate(**draft)
    Path(result["references"]["view"]).write_text("different view")
    with pytest.raises(documents.DocumentError, match="view.html"):
        documents.Documents().retrieve(document_id=result["document_id"], revision=result["revision"])


def test_language_binding_uses_the_upstream_parsed_document(draft):
    result = documents.Documents().validate(**{**draft, "source": draft["source"].replace("language: arch", "language: propose")})
    assert result["language"] == "propose" and not result["check"]["ok"]
    assert any("requires language arch" in p for p in result["check"]["problems"])


def test_page_data_contract_fails_closed():
    with pytest.raises(documents.DocumentError, match="exactly one"):
        documents.page_data(b"<html>No upstream data</html>")


def test_brain_and_internal_agents_share_the_authoring_contract(tmp_path):
    from jarvis import brain, session_skill
    from tests.test_brain import _config
    contract = documents.authoring_contract()
    assert contract in brain.Brain(_config(tmp_path)).launch_prompt()
    assert contract in session_skill.render()


def test_readable_views_are_revision_bound_and_tampering_is_refused(draft):
    service = documents.Documents()
    original = service.validate(**draft)
    assert original["metadata"]["readable"]["origin"] == "author-supplied"
    assert Path(original["references"]["readable_markdown"]).read_text() == draft["readable_markdown"]
    revised = service.validate(**{**draft, "readable_markdown": draft["readable_markdown"] + "\nAuthor clarification."},
        document_id=original["document_id"], parent_revision=original["revision"])
    assert revised["revision"] != original["revision"] and revised["source_revision"] == original["source_revision"]
    Path(original["references"]["readable_markdown"]).write_bytes(b"different authored text")
    with pytest.raises(documents.DocumentError, match="UNDERSTANDING.md"):
        service.retrieve(document_id=original["document_id"], revision=original["revision"])


@pytest.mark.parametrize("value", ["", " ", 12, "x" * (kit.MAX_SOURCE_BYTES + 1)], ids=["empty", "blank", "wrong-type", "too-large"])
def test_invalid_authored_readable_view_is_refused(draft, value):
    with pytest.raises(documents.DocumentError, match="author-supplied Markdown"):
        documents.Documents().validate(**{**draft, "readable_markdown": value})
