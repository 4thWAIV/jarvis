import json

import httpx
import pytest

from jarvis import language_kit as kit, language_kit_updates as updates


def test_failed_ci_keeps_the_working_version_and_retries(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    kit._atomic_json(kit.root() / "active.json", {"active": "kept", "previous": None})
    def respond(request):
        if "/releases" in str(request.url):
            release = {
                "tag_name": kit.PINNED_VERSION, "prerelease": True, "draft": False,
                "published_at": "2026-11-08T23:46:44Z", "assets": [{
                    "name": kit.ASSET, "digest": "sha256:" + kit.PINNED_DIGEST,
                    "size": 168015,
                    "browser_download_url": f"https://github.com/{kit.REPOSITORY}/releases/download/{kit.PINNED_VERSION}/{kit.ASSET}"}]}
            return httpx.Response(200, headers={"etag": "candidate"}, json=release if "/tags/" in str(request.url) else [release])
        if "/check-runs?" in str(request.url):
            return httpx.Response(200, json={"check_runs": [
                {"name": "gate", "status": "completed", "conclusion": "failure"}]})
        return httpx.Response(200, json={"sha": kit.PINNED_COMMIT})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = updates.Updater(client).check(force=True)
    assert "required gate" in result["error"]
    assert result["next_check"] > result["last_check"]
    assert result["failures"] == 1
    assert "etag" not in result
    assert json.loads((kit.root() / "active.json").read_text())["active"] == "kept"


def test_conditional_check_does_not_download_or_notify(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    kit._atomic_json(kit.root() / "updates.json", {"etag": "current", "next_check": 0})
    calls = []
    def respond(request):
        calls.append(request)
        assert request.headers["if-none-match"] == "current"
        return httpx.Response(304)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = updates.Updater(client).check(force=True)
    assert len(calls) == 1 and result["failures"] == 0
    assert not (tmp_path / "messages").exists()
    assert not (tmp_path / "packages").exists()


def test_download_failure_clears_etag_and_preserves_active(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    kit._atomic_json(kit.root() / "updates.json", {"etag": "old", "next_check": 0})
    def respond(request):
        return httpx.Response(503, text="publisher unavailable")
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = updates.Updater(client).check(force=True)
    assert "503" in result["error"] and "etag" not in result
    assert result["active"] is None


def test_malformed_release_metadata_remains_a_visible_retryable_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"null"))) as client:
        result = updates.Updater(client).check(force=True)
    assert "malformed release metadata" in result["error"]
    assert result["failures"] == 1 and result["next_check"] > result["last_check"]


@pytest.mark.asyncio
async def test_running_server_waits_for_explicit_adoption_then_checks_without_restart(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    waiting = asyncio.Queue()
    calls = []
    async def pause(seconds):
        future = asyncio.get_running_loop().create_future()
        await waiting.put(future)
        await future
    monkeypatch.setattr(updates.asyncio, "sleep", pause)
    monkeypatch.setattr(updates.Updater, "check", lambda self: calls.append("checked"))
    runtime = updates.Runtime()
    runtime.start()
    try:
        assert runtime.task is not None, "A running server must notice later explicit adoption."
        first = await asyncio.wait_for(waiting.get(), 1)
        assert calls == []
        same = runtime.task
        runtime.start()
        assert runtime.task is same
        kit._atomic_json(kit.root() / "active.json", {"active": "explicitly-adopted"})
        first.set_result(None)
        await asyncio.wait_for(waiting.get(), 1)
        assert calls == ["checked"]
    finally:
        await runtime.stop()
    assert runtime.task is None and runtime.client is None


# ── a held candidate: recorded, announced silently, approved, cleared ──────
#
# Kits made on disk directly (installation.json and its files), so activate
# and status run without the upstream asset or Node; `stage` is replaced by
# one that returns the candidate already in place.

from jarvis import attention_store
from jarvis.attention_store import AttentionStore

CANDIDATE = "0.2.0-pre-release"


def _fake_kit(version: str, *, arch: bytes = b"arch: 1\n", style: bytes = b"a {}\n") -> kit.Kit:
    files = {name: b"" for name in kit.REQUIRED_FILES}
    files.update({"arch.yaml": arch, "style.css": style, "metalang.yaml": b"meta: 1\n"})
    digest = kit.sha256(version.encode() + arch + style)
    folder = kit.root() / "versions" / digest
    folder.mkdir(parents=True)
    for name, raw in files.items():
        (folder / name).write_bytes(raw)
    kit._atomic_json(folder / "installation.json", {
        "release": {"version": version, "commit": "c" * 40, "digest": digest,
                    "checks": ["gate", "release"], "repository": kit.REPOSITORY},
        "files": {name: kit.sha256(raw) for name, raw in files.items()}})
    return kit.Kit(folder)


def _respond(candidate: kit.Kit, body: bytes = b"zip"):
    tag, digest = candidate.release.version, candidate.release.digest

    def respond(request):
        url = str(request.url)
        if "/releases/download/" in url:
            return httpx.Response(200, content=body)
        if "/releases" in url:
            return httpx.Response(200, headers={"etag": "e"}, json=[{
                "tag_name": tag, "prerelease": True, "draft": False,
                "published_at": "2026-11-14T00:00:00Z", "assets": [{
                    "name": kit.ASSET, "digest": "sha256:" + digest, "size": len(body),
                    "browser_download_url":
                        f"https://github.com/{kit.REPOSITORY}/releases/download/{tag}/{kit.ASSET}"}]}])
        if "/check-runs?" in url:
            return httpx.Response(200, json={"check_runs": [
                {"id": 1, "name": n, "status": "completed", "conclusion": "success"}
                for n in ("gate", "release")]})
        return httpx.Response(200, json={"sha": "c" * 40})
    return respond


def _check_with(candidate: kit.Kit, monkeypatch) -> dict:
    """One forced check whose newest release is `candidate`."""
    monkeypatch.setattr(kit, "stage", lambda path, release: candidate)
    with httpx.Client(transport=httpx.MockTransport(_respond(candidate))) as client:
        return updates.Updater(client).check(force=True)


def _check_304() -> dict:
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(304))) as client:
        return updates.Updater(client).check(force=True)


@pytest.fixture
def adopted(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    working = _fake_kit("0.1.0-pre-release")
    kit.activate(working)
    return working


def _kit_items(store: AttentionStore) -> list:
    return [i for i in store.items(states=attention_store.STATES) if i.kind == updates.KIND]


def test_a_language_change_is_held_with_its_files_and_is_one_silent_item(adopted, monkeypatch):
    candidate = _fake_kit(CANDIDATE, arch=b"arch: 2\n")
    state = _check_with(candidate, monkeypatch)
    assert state["active"]["digest"] == adopted.release.digest, "the working kit stays active"
    held = state["held"]
    assert (held["version"], held["digest"], held["reason"], held["changed"]) == (
        CANDIDATE, candidate.release.digest, updates.REVIEW, ["arch.yaml"])
    assert state["compatibility_review_required"] is True
    assert "arch.yaml" in state["error"]

    store = AttentionStore()
    updates.attend(store)
    [item] = _kit_items(store)
    assert (item.source_id, item.event, item.state, item.audible) == (
        candidate.release.digest, updates.REVIEW, attention_store.PENDING, False)
    assert item.facts == {"version": CANDIDATE, "digest": candidate.release.digest,
                          "files": "arch.yaml"}
    assert updates.KIND in attention_store.SILENT_KINDS

    # The next checks refuse it again, or answer 304: still one item, unchanged.
    since = held["since"]
    again = _check_with(candidate, monkeypatch)
    assert again["held"]["since"] == since
    quiet = _check_304()
    assert quiet["held"]["digest"] == candidate.release.digest
    assert quiet["compatibility_review_required"] is True
    updates.attend(store)
    [same] = _kit_items(store)
    assert (same.id, same.revision) == (item.id, item.revision)


def test_activating_the_reviewed_candidate_clears_the_refusal_and_resolves_it(adopted, monkeypatch):
    candidate = _fake_kit(CANDIDATE, arch=b"arch: 2\n")
    _check_with(candidate, monkeypatch)
    store = AttentionStore()
    monkeypatch.setattr(updates.runtime, "attention", lambda: store)
    updates.attend(store)
    assert updates.main(["activate-reviewed", "--digest", candidate.release.digest]) == 0
    saved = json.loads((kit.root() / "updates.json").read_text())
    assert saved["held"] is None and saved["error"] is None
    assert saved["compatibility_review_required"] is False
    state = updates.status()
    assert state["active"]["digest"] == candidate.release.digest and state["held"] is None
    [item] = _kit_items(store)
    assert item.state == attention_store.RESOLVED


def test_a_304_after_activation_never_brings_the_review_flag_back(adopted, monkeypatch):
    candidate = _fake_kit(CANDIDATE, arch=b"arch: 2\n")
    _check_with(candidate, monkeypatch)
    kit.activate(candidate, compatibility_reviewed=True)   # activated, updates.json not told
    state = _check_304()
    assert state["active"]["digest"] == candidate.release.digest
    assert state["compatibility_review_required"] is False
    assert state["held"] is None and state["error"] is None
    saved = json.loads((kit.root() / "updates.json").read_text())
    assert saved["compatibility_review_required"] is False and saved["held"] is None


def test_an_older_status_file_s_flag_is_held_with_its_candidate(adopted, monkeypatch):
    """updates.json written before holds were kept: its flag and candidate
    become the hold (the files worked out from both kits), and an active
    candidate clears the flag."""
    candidate = _fake_kit(CANDIDATE, arch=b"arch: 2\n")
    legacy = {"compatibility_review_required": True, "error": None, "next_check": 0,
              "candidate": {"version": CANDIDATE, "digest": candidate.release.digest}}
    kit._atomic_json(kit.root() / "updates.json", legacy)
    held = updates.held()
    assert (held["digest"], held["reason"], held["changed"]) == (
        candidate.release.digest, updates.REVIEW, ["arch.yaml"])
    kit.activate(candidate, compatibility_reviewed=True)
    state = updates.status()
    assert state["held"] is None and state["compatibility_review_required"] is False


def test_an_automatic_activation_clears_a_stale_hold(adopted, monkeypatch):
    _check_with(_fake_kit(CANDIDATE, arch=b"arch: 2\n"), monkeypatch)
    store = AttentionStore()
    updates.attend(store)
    newer = _fake_kit("0.3.0-pre-release", style=b"b {}\n")   # no language change
    state = _check_with(newer, monkeypatch)
    assert state["active"]["digest"] == newer.release.digest
    assert state["held"] is None and state["error"] is None
    assert state["compatibility_review_required"] is False
    updates.attend(store)
    assert [i.state for i in _kit_items(store)] == [attention_store.RESOLVED]


def test_a_candidate_activated_elsewhere_is_no_longer_held(adopted, monkeypatch):
    candidate = _fake_kit(CANDIDATE, arch=b"arch: 2\n")
    _check_with(candidate, monkeypatch)
    kit.activate(candidate, compatibility_reviewed=True)   # not through this module
    assert updates.held() is None
    store = AttentionStore()
    store.record(updates.KIND, candidate.release.digest, updates.REVIEW, audible=False)
    updates.attend(store)
    assert [i.state for i in _kit_items(store)] == [attention_store.RESOLVED]


def test_approve_held_activates_only_the_candidate_held(adopted, monkeypatch):
    with pytest.raises(kit.KitError, match="No language kit is waiting"):
        updates.approve_held(adopted.release.digest)
    candidate = _fake_kit(CANDIDATE, arch=b"arch: 2\n")
    _check_with(candidate, monkeypatch)
    other = _fake_kit("0.9.0-pre-release", arch=b"arch: 9\n")
    with pytest.raises(kit.KitError, match="nothing was activated"):
        updates.approve_held(other.release.digest)
    assert updates.status()["active"]["digest"] == adopted.release.digest
    state = updates.approve_held(candidate.release.digest)
    assert state["active"]["digest"] == candidate.release.digest and state["held"] is None


def test_a_rollback_held_candidate_is_held_as_refused_with_its_reason(adopted, monkeypatch):
    candidate = _fake_kit(CANDIDATE, style=b"b {}\n")
    kit.activate(candidate)
    kit.rollback()                                   # the candidate is held now
    state = _check_with(candidate, monkeypatch)
    assert state["held"]["reason"] == updates.REFUSED
    assert "held after rollback" in updates.facts(state["held"])["reason"]
