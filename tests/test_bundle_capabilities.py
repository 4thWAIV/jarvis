"""What this node advertises it can open, and how a recipient's nodes are
judged from the team server's snapshot (bundle_capabilities)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jarvis import bundle_capabilities as caps
from jarvis import messaging_wire as wire
from jarvis import team_api


@dataclass(frozen=True)
class Health:
    available: bool
    operations: frozenset = frozenset()
    reason: str | None = None


HEALTH = {"pdf": Health(True, frozenset({"read_text", "render"})),
          "markdown": Health(True, frozenset({"read_text"})),
          "docx": Health(False, reason="reader_unavailable")}
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def _formats(advert: dict) -> dict:
    return {f["format"]: f["operations"] for f in advert["formats"]}


def test_the_advertisement_lists_working_readers_and_always_zip():
    advert = caps.capabilities_from(HEALTH, age_ms=1500)
    wire.AttachmentCapabilities.model_validate(advert)
    assert _formats(advert) == {"zip": ["download", "list_archive"],
                                "markdown": ["download", "read_text"],
                                "pdf": ["download", "read_text", "render"]}
    assert "docx" not in _formats(advert), "a reader that failed its check is not offered"
    assert advert["observed_age_ms"] == 1500
    assert advert["work_packages"]["modes"] == ["conversation", "agent", "page"]
    assert advert["max_file_bytes"] == wire.FILE_MAX_BYTES


def test_a_reader_never_advertises_running_anything():
    advert = caps.capabilities_from({"html": Health(True, frozenset({"read_text", "execute"}))},
                                    age_ms=0)
    assert "execute" not in _formats(advert)["html"]


def test_the_health_check_runs_at_most_every_half_the_freshness():
    calls, now = [], [100.0]

    def health():
        calls.append(now[0])
        return HEALTH
    advertiser = caps.Advertiser(health=health, clock=lambda: now[0])
    assert advertiser.current()["observed_age_ms"] == 0
    now[0] += 5
    assert advertiser.current()["observed_age_ms"] == 5000
    now[0] += caps.HEALTH_EVERY_SEC
    assert advertiser.current()["observed_age_ms"] == 0
    assert calls == [100.0, 105.0 + caps.HEALTH_EVERY_SEC]


def test_a_failed_check_keeps_the_last_reading_which_then_ages():
    now, healthy = [0.0], [True]

    def health():
        if not healthy[0]:
            raise RuntimeError("osascript did not answer")
        return HEALTH
    advertiser = caps.Advertiser(health=health, clock=lambda: now[0])
    advertiser.current()
    healthy[0], now[0] = False, caps.HEALTH_EVERY_SEC + 1
    advert = advertiser.current()
    assert "pdf" in _formats(advert) and advert["observed_age_ms"] == \
        int((caps.HEALTH_EVERY_SEC + 1) * 1000)


def test_with_no_reading_ever_only_zip_is_offered():
    advertiser = caps.Advertiser(health=lambda: (_ for _ in ()).throw(NotImplementedError()),
                                 clock=lambda: 0.0)
    assert _formats(advertiser.current()) == {"zip": ["download", "list_archive"]}


def test_the_node_update_carries_the_advertisement(monkeypatch):
    monkeypatch.setattr(caps, "advertiser",
                        caps.Advertiser(health=lambda: HEALTH, clock=lambda: 0.0))
    update = team_api.node_update(None)
    assert wire.AttachmentCapabilities.model_validate(update["attachments"])
    assert "pdf" in _formats(update["attachments"])


def node(presence="online", attachments=None, checked_ago=timedelta(seconds=30)) -> dict:
    return {"presence": presence, "attachments": attachments,
            "attachments_observed_at": (NOW - checked_ago).isoformat().replace("+00:00", "Z")}


def test_standing_offline_legacy_stale_and_advertised():
    advert = caps.capabilities_from(HEALTH, 0)
    assert caps.node_standing(node("offline", advert), NOW_MS) == "offline"
    assert caps.node_standing(node("delayed", advert), NOW_MS) == "offline"
    assert caps.node_standing(node(), NOW_MS) == "legacy"
    assert caps.node_standing({"presence": "online"}, NOW_MS) == "legacy"
    old = timedelta(seconds=caps.CAPABILITY_FRESH_SEC + 1)
    assert caps.node_standing(node(attachments=advert, checked_ago=old), NOW_MS) == "stale"
    assert caps.node_standing(node(attachments=advert), NOW_MS) == "advertised"


def test_a_record_this_node_cannot_read_is_not_trusted():
    assert caps.advertised(node(attachments={"version": 2})) is None
    assert caps.advertised(node(attachments=caps.capabilities_from(HEALTH, 0))) is not None
