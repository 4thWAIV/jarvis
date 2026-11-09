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
