"""Write everything derived from the human-messaging contract, or check it.

    .venv/bin/python scripts/generate_messaging_contract.py           # write
    .venv/bin/python scripts/generate_messaging_contract.py --check   # exit 1 if stale

The source is `team_server/teamserver/messaging_models.py` (the wire) and
`jarvis/messaging_ports.py` (JARVIS's local shapes). From them:

    jarvis/messaging_wire.py              the wire module, copied for JARVIS
    tests/fixtures/messaging_contract.json  valid and invalid examples of every
                                          wire shape, digest test vectors, and
                                          valid examples of the page's shapes
    frontend/src/messagingContract.ts     the page's types and constants

Both test suites run `--check`'s comparison, so a stale output fails CI.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "team_server" / "teamserver" / "messaging_models.py"
MIRROR = ROOT / "jarvis" / "messaging_wire.py"
FIXTURES = ROOT / "tests" / "fixtures" / "messaging_contract.json"
TYPES = ROOT / "frontend" / "src" / "messagingContract.ts"

MIRROR_HEADER = (
    "# GENERATED from team_server/teamserver/messaging_models.py by\n"
    "# scripts/generate_messaging_contract.py. Do not edit: edit that file and\n"
    "# regenerate. Both test suites check this copy is exact.\n"
)


def mirror_text() -> str:
    return MIRROR_HEADER + SOURCE.read_text()


def _load():
    """The wire module (JARVIS's copy, which is the source byte for byte once
    written) and JARVIS's local shapes, which are built on that copy."""
    sys.path.insert(0, str(ROOT))
    from jarvis import messaging_ports, messaging_wire
    return messaging_wire, messaging_ports


# -- fixtures ---------------------------------------------------------------------

SERVER_ID = "00000000-0000-4000-8000-0000000000aa"
TIM, MATT = 863470, 1001
T0 = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _id(n: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{n:012x}")


def _examples(w) -> dict:
    """{model name: [(case, value)]}: deterministic, one per shape worth
    showing a consumer."""
    pdf = w.AttachmentRef(attachment_id=_id(31), filename="plan.pdf",
                          media_type="application/pdf", size_bytes=48213, sha256="a" * 64)
    notes = w.AttachmentRef(attachment_id=_id(32), filename="notes.txt",
                            media_type="text/plain", size_bytes=0, sha256="e3b0c44298fc1c149a"
                            "fbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    root = w.MessageContent(message_id=_id(1), thread_id=_id(1), recipient_github_id=MATT,
                            content_revision=1, subject="Deploy window",
                            text="Can we move the deploy to Friday?\nThe staging run is green.")
    reply = w.MessageContent(message_id=_id(2), thread_id=_id(1), in_reply_to_message_id=_id(1),
                             recipient_github_id=TIM, content_revision=2, subject="Deploy window",
                             text="Friday works.", related_message_ids=[_id(7)])
    files_only = w.MessageContent(message_id=_id(3), thread_id=_id(3), recipient_github_id=TIM,
                                  content_revision=1, attachments=[pdf])
    two_files = w.MessageContent(message_id=_id(4), thread_id=_id(4), recipient_github_id=MATT,
                                 content_revision=3, subject="Files", text="Both attached.",
                                 attachments=[notes, pdf])
    def request(content, sender):
        digest = w.content_digest(content, server_id=SERVER_ID, sender_github_id=sender)
        return w.SendRequest(**content.model_dump(), source_node_id=_id(101),
                             idempotency_key=_id(201 + content.message_id.int % 100),
                             content_digest=digest)
    requests = {"new_thread": request(root, TIM), "reply_with_related": request(reply, MATT),
                "self_files_only": request(files_only, TIM),
                "two_files_in_order": request(two_files, TIM)}

    def accepted(req, sender, minutes):
        return w.AcceptedMessage(**req.model_dump(), server_id=SERVER_ID, sender_github_id=sender,
                                 accepted_at=T0 + timedelta(minutes=minutes))
    messages = {"new_thread": accepted(requests["new_thread"], TIM, 0),
                "reply_with_related": accepted(requests["reply_with_related"], MATT, 5),
                "self_files_only": accepted(requests["self_files_only"], TIM, 9)}
    receipts = {
        "none_yet": w.ReceiptFacts(message_id=_id(1), recipient_github_id=MATT, revision=1),
        "received_and_read": w.ReceiptFacts(
            message_id=_id(1), recipient_github_id=MATT, received_at=T0 + timedelta(minutes=1),
            read_at=T0 + timedelta(minutes=3),
            nodes=[w.NodeReceipt(node_id=_id(102), received_at=T0 + timedelta(minutes=1))],
            revision=3),
        "read_before_received": w.ReceiptFacts(message_id=_id(2), recipient_github_id=TIM,
                                               read_at=T0 + timedelta(minutes=6), revision=2),
    }
    tim_item = w.new_work_state(server_id=SERVER_ID, owner_github_id=TIM,
                                message=messages["new_thread"])
    replied = w.on_message(tim_item, messages["reply_with_related"])
    self_item = w.new_work_state(server_id=SERVER_ID, owner_github_id=TIM,
                                 message=messages["self_files_only"])
    work = {"outbox_their_turn": tim_item, "reply_arrived_your_turn": replied,
            "self_message": self_item}
    leases = {
        "claimed": w.AnnouncementLease(message_id=_id(2), owner_github_id=TIM, node_id=_id(101),
                                       state="claimed", lease_token=_id(301),
                                       expires_at=T0 + timedelta(minutes=6), revision=1),
        "held_elsewhere": w.AnnouncementLease(message_id=_id(2), owner_github_id=TIM,
                                              node_id=_id(103), state="attempted",
                                              expires_at=T0 + timedelta(minutes=6), revision=2),
    }
    staging = {
        "uploading": w.AttachmentStatus(attachment_id=_id(31), message_id=_id(3),
                                        state="uploading", size_bytes=48213, sha256="a" * 64,
                                        received_bytes=16384,
                                        expires_at=T0 + timedelta(days=1)),
        "verified": w.AttachmentStatus(attachment_id=_id(31), message_id=_id(3),
                                       state="verified", size_bytes=48213, sha256="a" * 64,
                                       received_bytes=48213, expires_at=T0 + timedelta(days=1)),
        "pinned": w.AttachmentStatus(attachment_id=_id(31), message_id=_id(3), state="pinned",
                                     size_bytes=48213, sha256="a" * 64, received_bytes=48213),
    }
    commands = {name: w.WorkCommandRequest.model_validate({"change": change}) for name, change in (
        ("open", {"command": "open", "expected_revision": 2}),
        ("put_back", {"command": "put_back", "expected_revision": 3}),
        ("set_addressed", {"command": "set_addressed", "expected_revision": 3, "addressed": True}),
        ("set_unread_reminder", {"command": "set_unread_reminder", "expected_revision": 3,
                                 "unread": False}),
        ("set_action_owner", {"command": "set_action_owner", "expected_revision": 3,
                              "action_owner_github_id": MATT}),
        ("dismiss", {"command": "dismiss", "expected_revision": 4}),
        ("restore", {"command": "restore", "expected_revision": 5}))}
    page = w.SyncPage(epoch="e1", server_time=T0 + timedelta(minutes=10), scan_complete=False,
                      continuation="opaque-continuation",
                      entries=[w.SyncEntry(message=messages["reply_with_related"],
                                           receipts=receipts["read_before_received"],
                                           work_state=replied)])
    return {
        "AttachmentRef": [("pdf", pdf)],
        "SendRequest": list(requests.items()),
        "AcceptedMessage": list(messages.items()),
        "SendResult": [("first_send", w.SendResult(message=messages["new_thread"],
                                                   receipts=receipts["none_yet"],
                                                   work_state=tim_item, duplicate=False)),
                       ("identical_retry", w.SendResult(message=messages["new_thread"],
                                                        receipts=receipts["received_and_read"],
                                                        work_state=tim_item, duplicate=True))],
        "ReceiptBatch": [("received_and_read", w.ReceiptBatch(node_id=_id(102), receipts=[
            w.ReceiptItem(message_id=_id(1), kind="received"),
            w.ReceiptItem(message_id=_id(1), kind="read")]))],
        "ReceiptBatchResult": [("one_settled_one_not_found", w.ReceiptBatchResult(
            facts=[receipts["received_and_read"]], not_found=[_id(9)]))],
        "ReceiptFacts": list(receipts.items()),
        "RecipientList": [("roster_with_me", w.RecipientList(members=[
            w.TeamMember(github_id=MATT, login="matt", name="Matt"),
            w.TeamMember(github_id=TIM, login="tim", name=None)]))],
        "AnnouncementRequest": [
            ("claim", w.AnnouncementRequest(node_id=_id(101), step="claim")),
            ("completed", w.AnnouncementRequest(node_id=_id(101), step="completed",
                                                lease_token=_id(301)))],
        "AnnouncementLease": list(leases.items()),
        "AttachmentCreate": [("pdf", w.AttachmentCreate(attachment_id=_id(31), message_id=_id(3),
                                                        size_bytes=48213, sha256="a" * 64))],
        "AttachmentStatus": list(staging.items()),
        "WorkState": list(work.items()),
        "WorkCommandRequest": list(commands.items()),
        "SyncPage": [("one_entry", page),
                     ("scan_complete", w.SyncPage(epoch="e1", entries=[], scan_complete=True,
                                                  server_time=T0 + timedelta(minutes=11)))],
    }


def _invalid(valid: dict) -> list[dict]:
    """Each a valid example with one thing wrong, and the model that must
    refuse it."""
    req = valid["SendRequest"]["new_thread"]
    reply = valid["SendRequest"]["reply_with_related"]
    att = valid["AttachmentRef"]["pdf"]
    cases = [
        ("SendRequest", "unknown_field", {**req, "sender_github_id": 1}),
        ("SendRequest", "newer_schema_version", {**req, "schema_version": 2}),
        ("SendRequest", "neither_text_nor_files", {**req, "text": ""}),
        ("SendRequest", "text_not_normalized_crlf", {**req, "text": "a\r\nb"}),
        ("SendRequest", "text_with_control_character", {**req, "text": "a\u0007b"}),
        ("SendRequest", "subject_two_lines", {**req, "subject": "a\nb"}),
        ("SendRequest", "subject_too_long", {**req, "subject": "s" * 201}),
        ("SendRequest", "text_too_long", {**req, "text": "t" * 20_001}),
        ("SendRequest", "root_with_parent", {**req, "in_reply_to_message_id": str(_id(9))}),
        ("SendRequest", "reply_without_parent", {**reply, "in_reply_to_message_id": None}),
        ("SendRequest", "related_to_itself", {**req, "related_message_ids": [req["message_id"]]}),
        ("SendRequest", "too_many_related",
         {**req, "related_message_ids": [str(_id(400 + n)) for n in range(11)]}),
        ("SendRequest", "bad_digest_form", {**req, "content_digest": "md5:00"}),
        ("SendRequest", "revision_zero", {**req, "content_revision": 0}),
        ("SendRequest", "too_many_files",
         {**req, "attachments": [{**att, "attachment_id": str(_id(500 + n))} for n in range(11)]}),
        ("AttachmentRef", "path_in_filename", {**att, "filename": "../etc/passwd"}),
        ("AttachmentRef", "dot_name", {**att, "filename": ".."}),
        ("AttachmentRef", "file_too_large", {**att, "size_bytes": 25 * 1024 * 1024 + 1}),
        ("AttachmentRef", "upper_case_hash", {**att, "sha256": "A" * 64}),
        ("AttachmentRef", "media_type_not_a_type", {**att, "media_type": "pdf"}),
        ("AttachmentRef", "control_character", {**att, "filename": "a\u0007.pdf"}),
        ("AttachmentRef", "not_nfc", {**att, "filename": "e\u0301.pdf"}),
        ("AttachmentRef", "leading_space", {**att, "filename": " plan.pdf"}),
        ("AttachmentRef", "media_type_with_parameters",
         {**att, "media_type": "text/plain; charset=utf-8"}),
        ("SendRequest", "package_too_large",
         {**req, "attachments": [{**att, "attachment_id": str(_id(600 + n)),
                                  "size_bytes": 25 * 1024 * 1024} for n in range(5)]}),
        ("ReceiptBatch", "unknown_kind", {"node_id": str(_id(102)), "receipts": [
            {"message_id": str(_id(1)), "kind": "replied"}]}),
        ("ReceiptBatch", "empty", {"node_id": str(_id(102)), "receipts": []}),
        ("ReceiptBatch", "too_many", {"node_id": str(_id(102)), "receipts": [
            {"message_id": str(_id(700 + n)), "kind": "received"} for n in range(101)]}),
        ("AnnouncementRequest", "claim_with_token",
         {"node_id": str(_id(101)), "step": "claim", "lease_token": str(_id(301))}),
        ("AnnouncementRequest", "completed_without_token",
         {"node_id": str(_id(101)), "step": "completed"}),
        ("AttachmentStatus", "unknown_state",
         {**valid["AttachmentStatus"]["verified"], "state": "quarantined"}),
        ("WorkCommandRequest", "unknown_command",
         {"change": {"command": "archive", "expected_revision": 1}}),
        ("WorkCommandRequest", "no_expected_revision", {"change": {"command": "open"}}),
        ("WorkState", "unknown_placement",
         {**valid["WorkState"]["self_message"], "placement": "archived"}),
    ]
    return [{"model": m, "case": c, "value": v} for m, c, v in cases]


def fixtures_data(w) -> dict:
    examples = _examples(w)
    valid = {model: {case: inst.model_dump(mode="json") for case, inst in items}
             for model, items in examples.items()}
    vectors = []
    for case, req in examples["SendRequest"]:
        sender = TIM if case != "reply_with_related" else MATT
        content = w.content_of(req)
        vectors.append({"case": case, "server_id": SERVER_ID, "sender_github_id": sender,
                        "content": content.model_dump(mode="json"),
                        "canonical": w.canonical_bytes(w.digest_input(
                            content, server_id=SERVER_ID, sender_github_id=sender)).decode(),
                        "digest": req.content_digest})
    return {"interface_revision": w.INTERFACE_REVISION, "server_id": SERVER_ID,
            "valid": valid, "invalid": _invalid(valid), "digest_vectors": vectors}


def _local_examples(w, local) -> dict:
    """{model name: [(case, value)]} of the page's shapes, for the queue and
    signals fixtures to start from: owner Tim, correspondent Matt, Tim's two
    nodes (101, 103), a self-message on one node and on two."""
    ex = _examples(w)
    msg = dict(ex["AcceptedMessage"])
    rec = dict(ex["ReceiptFacts"])
    work = dict(ex["WorkState"])
    pdf = ex["AttachmentRef"][0][1]
    received_by = lambda m, nodes: w.ReceiptFacts(
        message_id=m.message_id, recipient_github_id=m.recipient_github_id,
        received_at=T0 + timedelta(minutes=10),
        nodes=[w.NodeReceipt(node_id=_id(n), received_at=T0 + timedelta(minutes=10 + i))
               for i, n in enumerate(nodes)], revision=1 + len(nodes))
    self_m = msg["self_files_only"]
    self_one = local.LocalMessage(message=self_m, direction="outgoing", self_message=True,
                                  receipts=received_by(self_m, [101]),
                                  files=[local.LocalFile(attachment_id=pdf.attachment_id,
                                                         state="available")])
    self_two = self_one.model_copy(update={"receipts": received_by(self_m, [101, 103])})
    root = local.LocalMessage(message=msg["new_thread"], direction="outgoing",
                              self_message=False, receipts=rec["received_and_read"])
    reply = local.LocalMessage(message=msg["reply_with_related"], direction="incoming",
                               self_message=False, receipts=rec["read_before_received"])
    sending = local.LocalSend(message_id=_id(5), content_revision=2, state="submitting",
                              content_digest="sha256:" + "b" * 64, attempts=1)
    failed = sending.model_copy(update={"state": "failed", "failure": "transport",
                                        "next_attempt_at": T0 + timedelta(minutes=12)})
    reply_draft = local.Draft(message_id=_id(5), thread_id=_id(1),
                              in_reply_to_message_id=_id(2), recipient_github_id=MATT,
                              draft_revision=4, subject="Deploy window", text="Friday it is.",
                              send=sending)
    new_draft = local.Draft(message_id=_id(6), thread_id=_id(6), recipient_github_id=MATT,
                            draft_revision=1, text="", attachments=[pdf],
                            files=[local.LocalFile(attachment_id=pdf.attachment_id,
                                                   state="uploading", bytes_done=16384)],
                            send=local.LocalSend(message_id=_id(6), content_revision=1,
                                                 state="approved",
                                                 content_digest="sha256:" + "c" * 64),
                            capture_id=7)
    exchange = local.LocalItem(work_state=work["reply_arrived_your_turn"],
                               messages=[root, reply], drafts=[reply_draft],
                               dismiss_blockers=["send_unresolved"])
    self_item_one = local.LocalItem(work_state=work["self_message"], messages=[self_one])
    self_item_two = local.LocalItem(work_state=work["self_message"], messages=[self_two])
    dismissed = local.LocalItem(work_state=w.apply_work_command(
        work["outbox_their_turn"], w.Dismiss(expected_revision=1), T0 + timedelta(hours=1)),
        messages=[root])
    event = local.TransferEvent(
        event_id=local.transfer_event_id(_id(2), "incoming"), message_id=_id(2),
        thread_id=_id(1), direction="incoming", owner_github_id=TIM, source_node_id=_id(101),
        sender_github_id=MATT, recipient_github_id=TIM, occurred_at=T0 + timedelta(minutes=5),
        replay=False)
    return {
        "LocalFile": [("uploading", new_draft.files[0]), ("verified", local.LocalFile(
            attachment_id=pdf.attachment_id, state="verified", bytes_done=48213))],
        "LocalSend": [("submitting", sending), ("failed_transport", failed)],
        "Draft": [("reply_submitting", reply_draft), ("new_files_only_approved", new_draft)],
        "DraftSave": [("new_conversation", local.DraftSave(recipient_github_id=MATT,
                                                           subject="Deploy window",
                                                           text="Friday?")),
                      ("reply_edit", local.DraftSave(
                          expected_draft_revision=3, thread_id=_id(1),
                          in_reply_to_message_id=_id(2), recipient_github_id=MATT,
                          subject="Deploy window", text="Friday it is."))],
        "DraftAction": [("review", local.DraftAction(expected_draft_revision=4))],
        "LocalMessage": [("self_one_node", self_one), ("self_two_nodes", self_two),
                         ("incoming_reply", reply)],
        "LocalItem": [("exchange_your_turn", exchange), ("self_one_node", self_item_one),
                      ("self_two_nodes", self_item_two), ("dismissed", dismissed)],
        "QueueSnapshot": [("tim", local.QueueSnapshot(
            server_id=SERVER_ID, owner_github_id=TIM, availability="available",
            items=[self_item_one, exchange], unsent=[new_draft], history_count=1)),
            ("not_paired", local.QueueSnapshot(availability="not_paired", items=[]))],
        "HistoryPage": [("last_page", local.HistoryPage(items=[dismissed]))],
        "WorkCommandResult": [("pending_offline", local.WorkCommandResult(
            work_state=work["self_message"], pending=True))],
        "TransferEvent": [("incoming_reply", event)],
        "HumanTransferFrame": [("incoming_reply", local.HumanTransferFrame(event=event))],
    }


def local_fixtures_data(w, local) -> dict:
    return {model: {case: inst.model_dump(mode="json") for case, inst in items}
            for model, items in _local_examples(w, local).items()}


def fixtures_text(w, local) -> str:
    data = {**fixtures_data(w), "local_valid": local_fixtures_data(w, local)}
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# -- the page's types -----------------------------------------------------------------

def _ts_type(schema: dict) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " | ".join(_ts_type(s) for s in schema[key])
    kind = schema.get("type")
    if kind == "array":
        inner = _ts_type(schema.get("items", {}))
        return f"({inner})[]" if " | " in inner else f"{inner}[]"
    return {"string": "string", "integer": "number", "number": "number",
            "boolean": "boolean", "null": "null"}.get(kind, "unknown")


def _ts_interface(name: str, schema: dict) -> str:
    lines = []
    if schema.get("description"):
        lines.append("/** " + " ".join(schema["description"].split()) + " */")
    lines.append(f"export interface {name} {{")
    for field, prop in schema.get("properties", {}).items():
        lines.append(f"  {field}: {_ts_type(prop)};")
    lines.append("}")
    return "\n".join(lines)


def types_text(w, local) -> str:
    from pydantic.json_schema import models_json_schema
    wire_models = [w.AttachmentRef, w.MessageContent, w.SendRequest, w.AcceptedMessage,
                   w.SendResult, w.NodeReceipt, w.ReceiptFacts, w.ReceiptItem, w.ReceiptBatch,
                   w.ReceiptBatchResult, w.WorkState, w.WorkCommandRequest, w.SyncEntry,
                   w.SyncPage, w.TeamMember, w.RecipientList, w.AnnouncementRequest,
                   w.AnnouncementLease, w.AttachmentCreate, w.AttachmentStatus]
    local_models = [local.LocalFile, local.LocalSend, local.Draft, local.DraftSave,
                    local.DraftAction, local.LocalMessage, local.LocalItem,
                    local.QueueSnapshot, local.HistoryPage, local.WorkCommandResult,
                    local.TransferEvent, local.HumanTransferFrame]
    _, schema = models_json_schema([(m, "serialization") for m in wire_models + local_models],
                                   ref_template="#/$defs/{model}")
    defs = schema["$defs"]
    from typing import get_args
    unions = {
        "Placement": get_args(w.Placement), "ReceiptKind": get_args(w.ReceiptKind),
        "StagingState": get_args(w.StagingState),
        "AnnouncementStep": get_args(w.AnnouncementStep),
        "AnnouncementState": get_args(w.AnnouncementState),
        "SendState": get_args(local.SendState), "Availability": get_args(local.Availability),
        "Direction": get_args(local.Direction), "FileState": get_args(local.FileState),
        "DismissBlocker": get_args(local.DismissBlocker), "ErrorCode": tuple(w.ERRORS),
        "LocalErrorCode": tuple(local.LOCAL_ERRORS),
    }
    out = [
        "// GENERATED by scripts/generate_messaging_contract.py from",
        "// team_server/teamserver/messaging_models.py and jarvis/messaging_ports.py.",
        "// Do not edit: edit those and regenerate. Wire names are snake_case;",
        "// every field is always present in what JARVIS serves. Character limits",
        "// count Unicode code points (`[...text].length`), not UTF-16 units.",
        "",
        f"export const CAPABILITY = {json.dumps(w.CAPABILITY)};",
        f"export const INTERFACE_REVISION = {json.dumps(w.INTERFACE_REVISION)};",
        f"export const SCHEMA_VERSION = {w.SCHEMA_VERSION};",
        f"export const MESSAGES_HINT = {json.dumps(local.MESSAGES_HINT)};",
        f"export const TRANSFER_FRAME = {json.dumps(local.TRANSFER_FRAME)};",
        "",
        "export const LIMITS = {",
    ]
    for name in ("SUBJECT_MAX_CHARS", "TEXT_MAX_CHARS", "RELATED_MAX", "ATTACHMENTS_MAX",
                 "FILENAME_MAX_BYTES", "FILE_MAX_BYTES", "PACKAGE_MAX_BYTES"):
        out.append(f"  {name}: {getattr(w, name)},")
    for name in ("HISTORY_PAGE_DEFAULT", "HISTORY_PAGE_MAX"):
        out.append(f"  {name}: {getattr(local, name)},")
    out += ["} as const;", ""]
    for name, values in unions.items():
        out.append(f"export type {name} = {' | '.join(json.dumps(v) for v in values)};")
    for table, code_type, specs in (("ERRORS", "ErrorCode", w.ERRORS),
                                    ("LOCAL_ERRORS", "LocalErrorCode", local.LOCAL_ERRORS)):
        out += ["", f"export const {table}: Record<{code_type}, {{ status: number; "
                "retryable: boolean; shown: string }> = {"]
        for code, spec in specs.items():
            out.append(f"  {code}: {{ status: {spec.status}, retryable: "
                       f"{'true' if spec.retryable else 'false'}, shown: "
                       f"{json.dumps(spec.shown)} }},")
        out.append("};")
    for table, key_type, shown in (("AVAILABILITY_SHOWN", "Availability",
                                    local.AVAILABILITY_SHOWN),
                                   ("DISMISS_BLOCKERS_SHOWN", "DismissBlocker",
                                    local.DISMISS_BLOCKERS_SHOWN)):
        out += ["", f"export const {table}: Record<{key_type}, string> = {{"]
        for key, text in shown.items():
            out.append(f"  {key}: {json.dumps(text)},")
        out.append("};")
    out.append("")
    for name in sorted(defs):
        out += [_ts_interface(name, defs[name]), ""]
    commands = [n for n in sorted(defs) if defs[n].get("properties", {}).get("command")]
    out.append(f"export type WorkCommand = {' | '.join(commands)};")
    return "\n".join(out) + "\n"


# -- write or check -----------------------------------------------------------------

def outputs() -> dict[Path, str]:
    w, local = _load()
    return {MIRROR: mirror_text(), FIXTURES: fixtures_text(w, local),
            TYPES: types_text(w, local)}


def stale() -> list[str]:
    return [str(p.relative_to(ROOT)) for p, text in outputs().items()
            if not p.exists() or p.read_text() != text]


def main(argv: list[str]) -> int:
    if "--check" in argv:
        bad = stale()
        for path in bad:
            print(f"stale: {path} (run scripts/generate_messaging_contract.py)")
        return 1 if bad else 0
    MIRROR.write_text(mirror_text())          # first: the rest is built on it
    for path, text in outputs().items():
        path.write_text(text)
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
