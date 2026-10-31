"""The published API contract (team_server/openapi.json) is what the app
serves. Regenerate with:  python -m teamserver.openapi > openapi.json"""
import json
from pathlib import Path

from teamserver.openapi import contract

ROUTES = {
    ("get", "/version"), ("get", "/health/live"), ("get", "/health/ready"),
    ("get", "/api/v1/me"), ("get", "/api/v1/team"), ("get", "/api/v1/status"),
    ("post", "/api/v1/logout"), ("get", "/api/v1/team/members"),
    ("post", "/api/v1/team/members/resolve"), ("put", "/api/v1/team/members/{github_id}"),
    ("post", "/api/v1/team/members/{github_id}/revoke-sessions"),
    ("post", "/api/v1/device-authorizations"),
    ("post", "/api/v1/device-authorizations/approve"),
    ("post", "/api/v1/device-authorizations/exchange"),
    ("post", "/api/v1/network/nodes/self"),
    ("post", "/api/v1/messages"), ("get", "/api/v1/messages/sync"),
    ("get", "/api/v1/messages/recipients"), ("post", "/api/v1/messages/receipts"),
    ("put", "/api/v1/messages/threads/{thread_id}/work-state"),
    ("get", "/api/v1/messages/{message_id}"),
    ("post", "/api/v1/messages/{message_id}/announcement"),
    ("post", "/api/v1/attachments"), ("get", "/api/v1/attachments/{attachment_id}"),
    ("put", "/api/v1/attachments/{attachment_id}/bytes"),
    ("post", "/api/v1/attachments/{attachment_id}/finalize"),
    ("get", "/api/v1/messages/{message_id}/attachments/{attachment_id}/content"),
}


def test_committed_contract_matches_the_app():
    committed = json.loads((Path(__file__).resolve().parent.parent / "openapi.json").read_text())
    assert committed == contract()


def test_contract_has_exactly_the_planned_routes():
    spec = contract()
    found = {(m, p) for p, ops in spec["paths"].items() for m in ops}
    assert found == ROUTES
