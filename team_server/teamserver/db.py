"""MongoDB: the client, the collections, idempotent index initialization and
the installation's stable server ID.

Collections
  installation           one document, `_id: "installation"`: server ID
  team                   one document, `_id: "team"`: the team and its roster
  sessions               product sessions, by SHA-256 of the credential
  oauth_states           one-use GitHub login transactions
  device_authorizations  ten-minute device pairing transactions
  audit                  administrative history (kept AUDIT_DAYS)
  network_nodes          each JARVIS backend's current record, by node ID
                         (removed network.NODE_PURGE_AFTER after its last update)
  network_meta           one document, `_id: "network"`: the snapshot revision

TTL indexes only clean up. Every reader compares expiry timestamps itself,
because TTL deletion runs about once a minute.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from pymongo import ASCENDING, AsyncMongoClient, WriteConcern
from pymongo.asynchronous.database import AsyncDatabase

from teamserver import clock

SCHEMA_VERSION = 1
AUDIT_DAYS = 90
# Expired transactions are kept a day past their use window so that an
# exchange after expiry reads "expired" rather than "unknown".
TRANSACTION_PURGE_AFTER = timedelta(days=1)

INSTALLATION_ID = "installation"
TEAM_ID = "team"


def connect(uri: str) -> AsyncMongoClient:
    return AsyncMongoClient(
        uri,
        appname="jarvis-team-server",
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        timeoutMS=10000,
        retryWrites=True,
    )


def database(client: AsyncMongoClient, name: str) -> AsyncDatabase:
    return client.get_database(name, write_concern=WriteConcern(w="majority"))


async def init(db: AsyncDatabase) -> str:
    """Create indexes and the installation document. Idempotent; returns the
    server ID."""
    await db.sessions.create_index([("expires_at", ASCENDING)],
                                   expireAfterSeconds=0, name="ttl_expires")
    await db.sessions.create_index([("github_id", ASCENDING)], name="by_member")
    await db.oauth_states.create_index([("purge_at", ASCENDING)],
                                       expireAfterSeconds=0, name="ttl_purge")
    await db.device_authorizations.create_index(
        [("purge_at", ASCENDING)], expireAfterSeconds=0, name="ttl_purge")
    await db.device_authorizations.create_index(
        [("poll_hash", ASCENDING)], unique=True, name="poll_hash_unique")
    await db.device_authorizations.create_index(
        [("user_code", ASCENDING)], unique=True, name="user_code_unique")
    await db.audit.create_index([("at", ASCENDING)],
                                expireAfterSeconds=AUDIT_DAYS * 86400,
                                name="ttl_audit")
    await db.network_nodes.create_index([("owner_github_id", ASCENDING)],
                                        name="by_owner")
    await db.network_nodes.create_index([("purge_at", ASCENDING)],
                                        expireAfterSeconds=0, name="ttl_purge")
    await db.installation.update_one(
        {"_id": INSTALLATION_ID},
        {"$setOnInsert": {"server_id": str(uuid.uuid4()),
                          "schema_version": SCHEMA_VERSION,
                          "created_at": clock.now()}},
        upsert=True)
    doc = await db.installation.find_one({"_id": INSTALLATION_ID})
    return doc["server_id"]
