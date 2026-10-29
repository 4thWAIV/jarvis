"""The human-messaging contract's rules (teamserver/messaging_models.py),
tested where they are defined. No database: these are the pure rules the
messaging routes and JARVIS both apply. JARVIS's copy of this module is
checked here too, because a change to it runs this suite and not JARVIS's."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from teamserver import messaging_models as mm
from teamserver import messaging_ports

REPO = Path(__file__).resolve().parents[2]
SERVER = "00000000-0000-4000-8000-0000000000aa"
TIM, MATT = 863470, 1001
T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _id(n: int) -> UUID:
    return UUID(int=n)


def _content(**kw) -> mm.MessageContent:
    base = dict(message_id=_id(1), thread_id=_id(1), recipient_github_id=MATT,
                content_revision=1, subject="Deploy", text="Friday?")
    return mm.MessageContent(**{**base, **kw})


def _accepted(sender, recipient, n, minutes, thread=None, parent=None) -> mm.AcceptedMessage:
    content = _content(message_id=_id(n), thread_id=thread or _id(n),
                       in_reply_to_message_id=parent, recipient_github_id=recipient)
    return mm.AcceptedMessage(
        **content.model_dump(), server_id=SERVER, sender_github_id=sender,
        source_node_id=_id(900), idempotency_key=_id(800 + n),
        content_digest=mm.content_digest(content, server_id=SERVER, sender_github_id=sender),
        accepted_at=T0 + timedelta(minutes=minutes))


# -- JARVIS's copy -------------------------------------------------------------------

def test_jarvis_carries_this_module_exactly():
    source = (REPO / "team_server" / "teamserver" / "messaging_models.py").read_text()
    copy = (REPO / "jarvis" / "messaging_wire.py").read_text()
    assert copy.endswith(source) and copy[:-len(source)].startswith("# GENERATED"), \
        "run scripts/generate_messaging_contract.py"


def test_the_fixtures_were_made_from_this_revision():
    fixtures = json.loads((REPO / "tests" / "fixtures" / "messaging_contract.json").read_text())
    assert fixtures["interface_revision"] == mm.INTERFACE_REVISION
    for vector in fixtures["digest_vectors"]:
        content = mm.MessageContent.model_validate(vector["content"])
        assert mm.content_digest(content, server_id=vector["server_id"],
                                 sender_github_id=vector["sender_github_id"]) == vector["digest"]


# -- capability ------------------------------------------------------------------------

def test_messaging_is_offered_only_by_its_capability():
    assert mm.messaging_offered(["github-login", "network", "messaging.v1"])
    assert not mm.messaging_offered(["github-login", "membership", "device-pairing",
                                     "status", "network"])
    assert messaging_ports.MESSAGING_FLAG == "TEAM_SERVER_MESSAGING"


# -- normalization -------------------------------------------------------------------

def test_text_is_normalized_to_lf_and_nfc_and_nothing_else():
    assert mm.normalize_text("a\r\nb\rc\n\n  d ") == "a\nb\nc\n\n  d "
    assert mm.normalize_text("é") == "é"
    assert mm.text_problem(mm.normalize_text("tab\there")) is None
    assert mm.text_problem("a\r\nb") == "content_not_normalized"
    assert mm.text_problem("bell\u0007") == "invalid_request"
    assert mm.text_problem("rtl‮hidden") == "invalid_request"


def test_a_subject_is_one_trimmed_line():
    assert mm.normalize_subject("  Deploy \n window\t ") == "Deploy window"
    assert mm.subject_problem("Deploy window") is None
    assert mm.subject_problem(" Deploy") == "content_not_normalized"


@pytest.mark.parametrize("name, problem", [
    ("plan.pdf", None), ("Q3 report (final).xlsx", None), ("../x", "invalid_request"),
    ("a/b", "invalid_request"), ("a\\b", "invalid_request"), (".", "invalid_request"),
    (" x", "content_not_normalized"), ("x\n", "content_not_normalized"),
    ("é.txt", "content_not_normalized"), ("x" * 256, "invalid_request")])
def test_filenames_are_safe_display_names(name, problem):
    assert mm.filename_problem(name) == problem


@pytest.mark.parametrize("chosen", [
    "plan.pdf", "/Users/tim/Desktop/plan.pdf", "C:\\Users\\tim\\plan.pdf", "  spaced  .txt ",
    "bell\u0007.txt", "e\u0301.txt", "..", "/", "", "x" * 300 + ".pdf", "é" * 200,
    "x" * 250 + "." + "y" * 40])
def test_a_chosen_files_name_always_normalizes_to_a_safe_display_name(chosen):
    name = mm.normalize_filename(chosen)
    assert mm.filename_problem(name) is None
    assert mm.normalize_filename(name) == name, "normalizing twice changes nothing"


def test_normalized_names_keep_what_they_can():
    assert mm.normalize_filename("/Users/tim/Desktop/plan.pdf") == "plan.pdf"
    assert mm.normalize_filename("..") == "file"
    long = mm.normalize_filename("x" * 300 + ".pdf")
    assert long.endswith(".pdf") and len(long.encode()) <= mm.FILENAME_MAX_BYTES


@pytest.mark.parametrize("given, kept", [
    ("text/plain; charset=utf-8", "text/plain"), ("Application/PDF", "application/pdf"),
    ("", "application/octet-stream"), (None, "application/octet-stream"),
    ("pdf", "application/octet-stream"), ("image/svg+xml", "image/svg+xml")])
def test_a_media_type_is_a_plain_lowercase_hint(given, kept):
    assert mm.normalize_media_type(given) == kept


def test_a_file_only_message_is_valid_and_an_empty_one_is_not():
    pdf = mm.AttachmentRef(attachment_id=_id(31), filename="plan.pdf",
                           media_type="application/pdf", size_bytes=1, sha256="a" * 64)
    assert _content(text="", subject="", attachments=[pdf]).text == ""
    with pytest.raises(ValidationError):
        _content(text="", attachments=[])


def test_the_package_limit_is_the_sum_of_its_files():
    files = [mm.AttachmentRef(attachment_id=_id(40 + n), filename=f"f{n}",
                              media_type="application/octet-stream",
                              size_bytes=mm.FILE_MAX_BYTES, sha256="b" * 64) for n in range(5)]
    with pytest.raises(ValidationError):
        _content(attachments=files)
    assert len(_content(attachments=files[:4]).attachments) == 4


def test_a_newer_schema_version_and_unknown_fields_are_refused():
    with pytest.raises(ValidationError):
        mm.MessageContent.model_validate({**_content().model_dump(mode="json"),
                                          "schema_version": 2})
    with pytest.raises(ValidationError):
        mm.MessageContent.model_validate({**_content().model_dump(mode="json"), "cc": [1]})


# -- the digest ------------------------------------------------------------------------

def _digest(content=None, server=SERVER, sender=TIM):
    return mm.content_digest(content or _content(), server_id=server, sender_github_id=sender)


@pytest.mark.parametrize("change", [
    {"recipient_github_id": 1002}, {"subject": "Deploy!"}, {"text": "Saturday?"},
    {"content_revision": 2}, {"related_message_ids": [_id(7)]},
    {"message_id": _id(2), "thread_id": _id(2)}])
def test_every_field_the_owner_approves_changes_the_digest(change):
    assert _digest(_content(**change)) != _digest()


def test_the_session_context_is_in_the_digest():
    assert _digest(server="other") != _digest()
    assert _digest(sender=MATT) != _digest()


def test_file_hashes_sizes_names_and_order_are_in_the_digest():
    a = mm.AttachmentRef(attachment_id=_id(31), filename="a.txt", media_type="text/plain",
                         size_bytes=3, sha256="a" * 64)
    b = mm.AttachmentRef(attachment_id=_id(32), filename="b.txt", media_type="text/plain",
                         size_bytes=4, sha256="c" * 64)
    base = _digest(_content(attachments=[a, b]))
    assert _digest(_content(attachments=[b, a])) != base
    assert _digest(_content(attachments=[a.model_copy(update={"sha256": "d" * 64}), b])) != base
    assert _digest(_content(attachments=[a.model_copy(update={"size_bytes": 9}), b])) != base
    assert _digest(_content(attachments=[a.model_copy(update={"filename": "z.txt"}), b])) != base


def test_related_ids_are_a_set_in_the_digest():
    assert _digest(_content(related_message_ids=[_id(7), _id(8)])) == \
        _digest(_content(related_message_ids=[_id(8), _id(7)]))


def test_the_node_and_the_key_are_not_content():
    content = _content()
    a = mm.SendRequest(**content.model_dump(), source_node_id=_id(1), idempotency_key=_id(2),
                       content_digest=_digest(content))
    b = a.model_copy(update={"source_node_id": _id(3), "idempotency_key": _id(4)})
    assert _digest(mm.content_of(a)) == _digest(mm.content_of(b))


def test_canonical_bytes_are_sorted_compact_utf8():
    assert mm.canonical_bytes({"b": "é", "a": [1, None]}) == '{"a":[1,null],"b":"é"}'.encode()


def test_a_thread_root_and_a_reply_are_shaped_as_such():
    with pytest.raises(ValidationError):
        _content(in_reply_to_message_id=_id(9))
    with pytest.raises(ValidationError):
        _content(message_id=_id(2), thread_id=_id(1))
    reply = _content(message_id=_id(2), thread_id=_id(1), in_reply_to_message_id=_id(1))
    assert reply.in_reply_to_message_id == _id(1)
    with pytest.raises(ValidationError):
        _content(related_message_ids=[_id(1)])


# -- receipts ----------------------------------------------------------------------------

def test_receipts_keep_their_first_times_and_repeat_harmlessly():
    facts = mm.ReceiptFacts(message_id=_id(1), recipient_github_id=MATT, revision=1)
    got = mm.merge_receipt(facts, "received", _id(5), T0)
    assert (got.received_at, got.revision) == (T0, 2)
    assert mm.merge_receipt(got, "received", _id(5), T0 + timedelta(hours=1)) == got
    second_node = mm.merge_receipt(got, "received", _id(6), T0 + timedelta(hours=1))
    assert second_node.received_at == T0 and len(second_node.nodes) == 2
    read = mm.merge_receipt(second_node, "read", _id(6), T0 + timedelta(hours=2))
    assert mm.merge_receipt(read, "read", _id(5), T0 + timedelta(hours=3)).read_at == \
        T0 + timedelta(hours=2)


def test_a_read_before_received_is_kept_and_received_is_not_invented():
    facts = mm.ReceiptFacts(message_id=_id(1), recipient_github_id=MATT, revision=1)
    read = mm.merge_receipt(facts, "read", _id(5), T0)
    assert (read.read_at, read.received_at) == (T0, None)
    both = mm.merge_receipt(read, "received", _id(5), T0 + timedelta(seconds=3))
    assert (both.read_at, both.received_at) == (T0, T0 + timedelta(seconds=3))


# -- work state -------------------------------------------------------------------------

def test_a_new_incoming_message_waits_in_the_queue_on_the_owners_side():
    m = _accepted(MATT, TIM, 1, 0)
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM, message=m)
    assert (item.placement, item.action_owner_github_id, item.correspondent_github_id,
            item.unread_reminder, item.arrival_at, item.revision) == \
        ("queued", TIM, MATT, True, m.accepted_at, 1)


def test_a_new_outgoing_message_is_active_and_their_turn():
    m = _accepted(TIM, MATT, 1, 0)
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM, message=m)
    assert (item.placement, item.action_owner_github_id, item.unread_reminder) == \
        ("active", MATT, False)


def test_a_message_to_oneself_is_always_your_turn():
    m = _accepted(TIM, TIM, 1, 0)
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM, message=m)
    assert (item.placement, item.action_owner_github_id, item.correspondent_github_id) == \
        ("queued", TIM, TIM)
    turned = mm.apply_work_command(item, mm.SetActionOwner(
        expected_revision=1, action_owner_github_id=TIM), T0)
    assert turned.action_owner_github_id == TIM
    reply = _accepted(TIM, TIM, 2, 5, thread=_id(1), parent=_id(1))
    assert mm.on_message(item, reply).action_owner_github_id == TIM


def test_an_incoming_reply_returns_the_turn_moves_up_and_brings_back_a_dismissed_item():
    root = _accepted(TIM, MATT, 1, 0)
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM, message=root)
    dismissed = mm.apply_work_command(item, mm.Dismiss(expected_revision=1), T0)
    reply = _accepted(MATT, TIM, 2, 30, thread=_id(1), parent=_id(1))
    back = mm.on_message(dismissed, reply)
    assert (back.placement, back.dismissed_at, back.action_owner_github_id,
            back.unread_reminder, back.arrival_at) == \
        ("queued", None, TIM, True, reply.accepted_at)
    assert back.revision == dismissed.revision + 1


def test_an_outgoing_reply_keeps_the_item_active_and_gives_the_turn_away():
    root = _accepted(MATT, TIM, 1, 0)
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM, message=root)
    opened = mm.apply_work_command(item, mm.OpenItem(expected_revision=1), T0)
    sent = mm.on_message(opened, _accepted(TIM, MATT, 2, 3, thread=_id(1), parent=_id(1)))
    assert (sent.placement, sent.action_owner_github_id, sent.arrival_at) == \
        ("active", MATT, root.accepted_at), "sending is not a new arrival"


def test_open_put_back_and_restore_never_move_the_arrival():
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM,
                             message=_accepted(MATT, TIM, 1, 0))
    for change in (mm.OpenItem, mm.PutBack, mm.Dismiss, mm.Restore, mm.OpenItem):
        item = mm.apply_work_command(item, change(expected_revision=item.revision),
                                     T0 + timedelta(hours=1))
        assert item.arrival_at == T0
    assert item.placement == "active"


def test_addressed_keeps_its_first_time_and_is_not_a_reply_or_a_read():
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM,
                             message=_accepted(MATT, TIM, 1, 0))
    a = mm.apply_work_command(item, mm.SetAddressed(expected_revision=1, addressed=True), T0)
    b = mm.apply_work_command(a, mm.SetAddressed(expected_revision=a.revision, addressed=True),
                              T0 + timedelta(hours=1))
    assert b == a and a.addressed_at == T0 and a.unread_reminder is True
    c = mm.apply_work_command(b, mm.SetAddressed(expected_revision=b.revision, addressed=False),
                              T0)
    assert c.addressed_at is None


def test_the_whose_turn_cue_is_the_owners_and_only_between_the_two():
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM,
                             message=_accepted(MATT, TIM, 1, 0))
    waiting = mm.apply_work_command(item, mm.SetActionOwner(
        expected_revision=1, action_owner_github_id=MATT), T0)
    assert waiting.action_owner_github_id == MATT
    with pytest.raises(mm.ContractError) as e:
        mm.apply_work_command(waiting, mm.SetActionOwner(
            expected_revision=waiting.revision, action_owner_github_id=4242), T0)
    assert e.value.code == "invalid_request"


def test_a_stale_revision_is_a_conflict_with_the_latest_and_a_repeat_is_harmless():
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM,
                             message=_accepted(MATT, TIM, 1, 0))
    opened = mm.apply_work_command(item, mm.OpenItem(expected_revision=1), T0)
    with pytest.raises(mm.ContractError) as e:
        mm.apply_work_command(opened, mm.Dismiss(expected_revision=1), T0)
    assert e.value.code == "revision_conflict" and e.value.latest == opened
    assert mm.apply_work_command(opened, mm.OpenItem(expected_revision=opened.revision),
                                 T0) == opened


def test_dismiss_keeps_its_first_time_and_restore_of_a_live_item_changes_nothing():
    item = mm.new_work_state(server_id=SERVER, owner_github_id=TIM,
                             message=_accepted(MATT, TIM, 1, 0))
    assert mm.apply_work_command(item, mm.Restore(expected_revision=1), T0) == item
    gone = mm.apply_work_command(item, mm.Dismiss(expected_revision=1), T0)
    assert mm.apply_work_command(gone, mm.Dismiss(expected_revision=gone.revision),
                                 T0 + timedelta(days=1)) == gone


def test_the_queue_is_newest_first_with_the_thread_breaking_ties():
    items = [mm.new_work_state(server_id=SERVER, owner_github_id=TIM,
                               message=_accepted(MATT, TIM, n, minutes))
             for n, minutes in ((1, 0), (2, 5), (3, 5), (4, 1))]
    order = sorted(items, key=mm.item_order_key, reverse=True)
    assert [i.thread_id for i in order] == [_id(3), _id(2), _id(4), _id(1)]


def test_work_commands_are_a_closed_set_by_their_name():
    parsed = mm.WorkCommandRequest.model_validate(
        {"change": {"command": "set_unread_reminder", "expected_revision": 3, "unread": False}})
    assert isinstance(parsed.change, mm.SetUnreadReminder)
    with pytest.raises(ValidationError):
        mm.WorkCommandRequest.model_validate({"change": {"command": "archive",
                                                         "expected_revision": 1}})


# -- errors, retries and faults -------------------------------------------------------

def test_every_error_has_a_status_and_only_transient_ones_retry():
    assert {c for c, s in mm.ERRORS.items() if s.retryable} == \
        {"sync_restart", "rate_limited", "dependency_unavailable", "not_ready", "internal"}
    for code, spec in mm.ERRORS.items():
        assert 400 <= spec.status < 600 and spec.shown and code == code.lower()
    # The team server's existing codes keep their meaning.
    assert mm.ERRORS["invalid_request"].status == 422
    assert mm.ERRORS["too_large"].status == 413
    assert mm.ERRORS["session_invalid"].status == 401


def test_the_team_servers_own_session_and_startup_codes_are_known():
    for code in ("session_missing", "session_expired", "session_revoked", "session_invalid"):
        assert mm.ERRORS[code].status == 401
    for code in ("membership_suspended", "device_required"):
        assert mm.ERRORS[code].status == 403
    assert mm.ERRORS["not_ready"].status == 503 and mm.ERRORS["internal"].status == 500


def test_an_unknown_code_is_judged_by_its_status():
    assert mm.error_spec("brand_new", 503).retryable
    assert not mm.error_spec("brand_new", 409).retryable
    assert mm.session_ended(401) and not mm.session_ended(403)
    assert mm.error_spec("digest_mismatch", 500) == mm.ERRORS["digest_mismatch"]


def test_retry_backoff_is_bounded_full_jitter():
    assert mm.retry_delay(1, 0.0) == 0.0
    assert mm.retry_delay(1, 0.999) < mm.RETRY_BASE_SEC
    assert mm.retry_delay(50, 0.999) < mm.RETRY_MAX_SEC


def test_production_faults_never_fire_and_armed_ones_name_their_point():
    for point in mm.FAULT_POINTS:
        mm.NO_FAULTS.check(point)
    faults = mm.ArmedFaults()
    faults.arm("server_after_pin_before_insert", times=2)
    for _ in range(2):
        with pytest.raises(mm.InjectedFault) as e:
            faults.check("server_after_pin_before_insert")
        assert e.value.point == "server_after_pin_before_insert"
    faults.check("server_after_pin_before_insert")
    with pytest.raises(ValueError):
        faults.arm("nowhere")
    assert not hasattr(mm.NO_FAULTS, "arm")


def test_the_services_interfaces_are_checkable():
    for proto in (messaging_ports.MessageRepository, messaging_ports.AttachmentStorage,
                  messaging_ports.IdSource):
        assert getattr(proto, "_is_runtime_protocol", False)


def test_file_settings_default_to_the_contract_and_name_the_team_server():
    settings = messaging_ports.ATTACHMENT_SETTINGS
    assert all(name.startswith("TEAM_SERVER_") for name in settings)
    assert settings["TEAM_SERVER_FILE_MAX_BYTES"] == mm.FILE_MAX_BYTES
    assert mm.ORPHAN_PIN_AFTER_SEC > mm.RETRY_MAX_ATTEMPTS * mm.RETRY_MAX_SEC, \
        "recovery never robs a submission still inside its automatic retries"


def test_the_insert_and_projection_gap_has_a_fault_point():
    assert "server_after_insert_before_projection" in mm.FAULT_POINTS


# -- the announcement lease --------------------------------------------------------------

LAPTOP, DESK = _id(101), _id(103)


def _announce(lease, node, step, token=None, at=T0, new=_id(301)):
    return mm.apply_announcement(lease, mm.AnnouncementRequest(
        node_id=node, step=step, lease_token=token), message_id=_id(2),
        owner_github_id=TIM, now=at, new_token=new, lease_sec=30)


def test_one_node_holds_the_announcement_and_a_repeat_changes_nothing():
    lease = _announce(None, LAPTOP, "claim")
    assert (lease.state, lease.node_id, lease.lease_token, lease.revision) == \
        ("claimed", LAPTOP, _id(301), 1)
    assert _announce(lease, LAPTOP, "claim", new=_id(302)) == lease
    with pytest.raises(mm.ContractError) as e:
        _announce(lease, DESK, "claim", new=_id(302))
    assert e.value.code == "announcement_claimed" and e.value.latest.lease_token is None
    tried = _announce(lease, LAPTOP, "attempted", token=_id(301))
    assert (tried.state, tried.revision) == ("attempted", 2)
    assert _announce(tried, LAPTOP, "attempted", token=_id(301)) == tried
    done = _announce(tried, LAPTOP, "completed", token=_id(301))
    assert _announce(done, LAPTOP, "attempted", token=_id(301)) == done, "never backwards"


def test_an_expired_lease_passes_on_and_a_completed_one_never_does():
    lease = _announce(None, LAPTOP, "claim")
    later = T0 + timedelta(seconds=31)
    taken = _announce(lease, DESK, "claim", at=later, new=_id(302))
    assert (taken.node_id, taken.lease_token, taken.revision) == (DESK, _id(302), 2)
    with pytest.raises(mm.ContractError):
        _announce(taken, LAPTOP, "completed", token=_id(301), at=later)
    done = _announce(taken, DESK, "completed", token=_id(302), at=later)
    with pytest.raises(mm.ContractError):
        _announce(done, LAPTOP, "claim", at=later + timedelta(days=1), new=_id(303))


def test_a_step_without_a_claim_is_not_found():
    with pytest.raises(mm.ContractError) as e:
        _announce(None, LAPTOP, "completed", token=_id(301))
    assert e.value.code == "message_not_found"
