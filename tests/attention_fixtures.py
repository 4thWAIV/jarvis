"""The attention record for a test: a fresh store in the test's own data
directory, set as the one the running server attends with, and the items a
test reads back from it."""
from __future__ import annotations

import pytest

from jarvis import attention_store


@pytest.fixture
def attention(monkeypatch):
    """A fresh attention record, used by `attention_store.current()` and,
    when the server module is loaded, by `server._attend`."""
    store = attention_store.AttentionStore()
    attention_store.use(store)
    try:
        from jarvis import server
        monkeypatch.setattr(server, "attention_record", store)
    except Exception:
        pass
    yield store
    attention_store.use(None)


def recorded(store: attention_store.AttentionStore) -> list[tuple[str, str, str]]:
    """(kind, source_id, event) of every item, oldest first."""
    return [(i.kind, i.source_id, i.event) for i in reversed(store.items(
        (attention_store.PENDING, attention_store.SEEN, attention_store.RESOLVED)))]
