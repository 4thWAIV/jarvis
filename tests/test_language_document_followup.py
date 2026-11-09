"""Canonical source discussion through the pre-existing capture/approval sender.

The provider and next-word approval are test doubles, not actual user approval
or external-agent delivery.
"""
import json
import os
from pathlib import Path

import pytest

from jarvis import language_projection, transmission
from tests.test_agent_transmission_pins import server, wire, _capture, _his_yes, SID
from tests.test_language_documents import draft, installed_kit


@pytest.mark.asyncio
async def test_architecture_question_correction_and_reviewed_followup(server, wire, draft, monkeypatch):
    await exercise_followup(server, wire, draft, monkeypatch)


async def exercise_followup(server, wire, draft, monkeypatch, *, original=None, published=None):
    server.run_store.init_db()
    service = server.language_documents.documents
    original = original or service.validate(**draft)
    reference = {key: original[key] for key in ("document_id", "revision", "source_revision")}
    published = published or language_projection.publish(service, **reference)
    grounded = await server.language_documents.ground(**reference, package_id=published["package_id"], declaration="DocumentStore")
    assert "Owns an immutable document revision." in grounded["source"]["text"]
    assert grounded["understanding"]["text"] == draft["readable_markdown"]
    correction = service.validate(**{**draft,
        "source": draft["source"].replace("Owns an immutable document revision.", "Owns a reviewed immutable document revision."),
        "readable_markdown": draft["readable_markdown"].replace("owns an immutable document revision", "owns a reviewed immutable document revision")},
        document_id=original["document_id"], parent_revision=original["revision"])
    assert correction["check"]["ok"] and correction["source_revision"] != original["source_revision"]
    assert service.retrieve(document_id=original["document_id"], revision=original["revision"])["source_revision"] == original["source_revision"]
    text = (f"Please review the proposed correction to DocumentStore in canonical revision {correction['revision']}. "
            f"Proposed YAML SHA256: {correction['source_revision']}.\n\n" + grounded["followup_reference"])
    capture_id = _capture(server, text)
    capture = server._captures[capture_id]
    assert capture["to_session"] == SID and not transmission.accepted(capture)
    assert original["source_revision"] in text and original["revision"] in text
    assert (await server.tool_read_capture({"capture": capture_id})).startswith("Read back in full")
    assert _his_yes(server) == [capture_id]
    staged = await server.tool_steer_session({"name": "guard", "capture": capture_id})
    assert "staged as message" in staged and wire.sent == []
    await server._perform_staged_steers()
    assert wire.sent == [(SID, server.STEER_SIGNATURE + text, False)]
    assert wire.speech.windows == 1
    assert all(message.status == "sent" for message in server.run_store.list_messages())
    output = os.getenv("JARVIS_LANGUAGE_REVIEW_DIR")
    if output:
        path = Path(output)
        path.mkdir(parents=True, exist_ok=True)
        (path / "correction-followup.json").write_text(json.dumps({
            "kind": "isolated simulation using existing capture and provider test double",
            "question": "What does DocumentStore own?", "source": grounded,
            "proposed_revision": correction["revision"], "proposed_source_revision": correction["source_revision"],
            "recipient": SID, "read_back_text": text, "provider_handoff": wire.sent,
            "approval": "Simulated next-word yes after full read-back; no human product approval is claimed."}, indent=2))
