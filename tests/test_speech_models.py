"""speech_models.py and the service's switch, with fake engines and fake
archives: installing a language's model (checked against its digest, in the
background with progress), switching speech input to it (one model loaded at
a time, the open capture told why it ends, the voice record told), and the
page's routes. No model is downloaded and none is loaded."""

import asyncio
import dataclasses
import hashlib
import time

import pytest
from starlette.testclient import TestClient

from jarvis import speech_models, transcriber, transcription_api, voice_diagnostics
from jarvis.transcriber import TranscriptionService
from tests.test_transcriber import _archive
from tests.test_transcription_ws import (FakeEngine, PAGE, _app as _socket_app, _open,
                                         _ready, _until, word)

ENGLISH = transcriber.find_model("en")
SPANISH = transcriber.find_model("es")


def _entries(model):
    return [(f"{model.name}/{n}", b"weights") for n in transcriber.MODEL_FILES]


def _trust(monkeypatch, model, archive):
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    monkeypatch.setattr(transcriber, "MODELS", tuple(
        dataclasses.replace(m, sha256=digest) if m == model else m
        for m in transcriber.MODELS))
    return transcriber.find_model(model.language)


def _put(model):
    folder = transcriber.model_dir(model)
    folder.mkdir(parents=True)
    for name in transcriber.MODEL_FILES:
        (folder / name).write_text("x")


@pytest.fixture(autouse=True)
def _fresh_installs(monkeypatch):
    monkeypatch.setattr(speech_models, "_installs", {})


# ── installing ───────────────────────────────────────────────────────────

def test_the_spanish_model_installs_into_its_own_folder_beside_english(monkeypatch, tmp_path):
    archive = _archive(tmp_path, _entries(SPANISH))
    spanish = _trust(monkeypatch, SPANISH, archive)
    steps = []
    destination = speech_models.install(spanish, archive, lambda *a: steps.append(a[0]))
    assert destination == transcriber.model_dir(SPANISH)
    assert transcriber.model_problem(model=SPANISH) is None
    assert transcriber.model_problem(model=ENGLISH) is not None
    assert steps == ["verifying", "installing"]


def test_an_archive_that_is_not_the_releases_is_refused(tmp_path):
    archive = _archive(tmp_path, _entries(SPANISH))
    with pytest.raises(speech_models.InstallRefused, match="SHA-256"):
        speech_models.install(SPANISH, archive)
    assert not transcriber.model_dir(SPANISH).exists()


def test_the_english_archive_is_not_accepted_as_spanish(monkeypatch, tmp_path):
    archive = _archive(tmp_path, _entries(ENGLISH))
    spanish = _trust(monkeypatch, SPANISH, archive)
    with pytest.raises(speech_models.InstallRefused, match="unexpected entry"):
        speech_models.install(spanish, archive)


@pytest.mark.asyncio
async def test_start_install_downloads_in_the_background_and_reports_progress(
        monkeypatch, tmp_path):
    archive = _archive(tmp_path, _entries(SPANISH))
    _trust(monkeypatch, SPANISH, archive)
    fetched = []

    def fake_download(url, dest, progress):
        fetched.append(url)
        dest.write_bytes(archive.read_bytes())
        progress("downloading", dest.stat().st_size, dest.stat().st_size)

    monkeypatch.setattr(speech_models, "download", fake_download)
    first = speech_models.start_install("es")
    assert first["installed"] is False and first["install"]["state"] == "downloading"
    assert speech_models.start_install("es")["install"]["state"] in speech_models.RUNNING
    await asyncio.gather(*speech_models._tasks)
    assert fetched == [SPANISH.url], "one download, however often it is asked for"
    spanish = next(m for m in speech_models.models()["models"] if m["language"] == "es")
    assert spanish["installed"] and spanish["install"]["state"] == "installed"


@pytest.mark.asyncio
async def test_a_failed_download_is_reported_not_installed(monkeypatch):
    def broken(url, dest, progress):
        raise OSError("network unreachable")

    monkeypatch.setattr(speech_models, "download", broken)
    speech_models.start_install("es")
    await asyncio.gather(*speech_models._tasks)
    spanish = speech_models.models()["models"][1]
    assert not spanish["installed"]
    assert spanish["install"]["state"] == "failed"
    assert "network unreachable" in spanish["install"]["error"]


def test_an_unknown_language_is_refused():
    with pytest.raises(speech_models.Refused) as refused:
        speech_models.start_install("fr")
    assert refused.value.status == 404 and "Spanish (es)" in refused.value.message


# ── switching: the service ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_switch_loads_the_other_model_in_place_of_the_first():
    loaded, engines = [], []

    def factory(model):
        loaded.append(model.language)
        engines.append(FakeEngine())
        return engines[-1]

    service = TranscriptionService(factory)
    service.start()
    await service.ready()
    assert (service.model, loaded) == (ENGLISH, ["en"])
    capture = service.admit()
    await service.switch(SPANISH)
    assert (service.model, service.status, loaded) == (SPANISH, "ready", ["en", "es"])
    # The capture open at the switch was told why it ended, not as a failure.
    events = [e async for e in capture.events()]
    assert len(events) == 1 and events[0].code == "interrupted"
    assert events[0].incident is False and "Spanish" in events[0].message
    assert service.active is None
    # The next capture is heard by the new model.
    service.admit()
    await asyncio.sleep(0.05)
    assert engines[0].sessions and engines[1].sessions
    await service.switch(SPANISH)
    assert loaded == ["en", "es"], "switching to the model it has loads nothing"
    await service.close()


@pytest.mark.asyncio
async def test_a_model_that_fails_to_load_fails_the_switch_and_english_comes_back():
    def factory(model):
        if model == SPANISH:
            raise RuntimeError("bad weights")
        return FakeEngine()

    service = TranscriptionService(factory)
    service.start()
    await service.ready()
    with pytest.raises(transcriber.TranscriptionError, match="bad weights"):
        await service.switch(SPANISH)
    assert service.status == "failed"
    await service.switch(ENGLISH)
    assert service.status == "ready" and service.model == ENGLISH
    await service.close()


# ── switching: the service function and the routes ───────────────────────

def _app(factory):
    """The transcription socket's test app, with the speech model routes."""
    app = _socket_app(factory)
    speech_models.mount(app.app)
    return app


def _events(kind):
    _samples, events = voice_diagnostics.history(time.time() - 60, time.time() + 1)
    return [e for e in events if e.kind == kind]


def test_the_routes_read_install_and_switch(monkeypatch, tmp_path):
    loaded = []

    def factory(model):
        loaded.append(model.language)
        return FakeEngine()

    _put(ENGLISH)
    with TestClient(_app(factory)) as client:
        got = client.get("/api/speech/models").json()
        assert got["language"] == "en"
        assert [(m["language"], m["installed"]) for m in got["models"]] == [
            ("en", True), ("es", False)]
        assert got["models"][1]["archiveBytes"] == 124_394_665

        refused = client.post("/api/speech/model", json={"language": "es"}, headers=PAGE)
        assert refused.status_code == 409 and "not installed" in refused.json()["error"]
        assert client.post("/api/speech/models/fr/install", headers=PAGE).status_code == 404

        _put(SPANISH)
        installed = client.post("/api/speech/models/es/install", headers=PAGE).json()
        assert installed["model"]["installed"] and installed["model"]["install"] is None

        switched = client.post("/api/speech/model", json={"language": "es"}, headers=PAGE)
        assert switched.status_code == 200 and switched.json()["language"] == "es"
        assert loaded[-1] == "es"
        load = _events("model-load")
        assert len(load) == 1 and "from English to Spanish" in load[0].text


def test_a_page_listening_through_a_switch_is_told_and_reconnects(monkeypatch):
    engine = FakeEngine()
    _put(ENGLISH)
    _put(SPANISH)
    with TestClient(_app(lambda _model: engine)) as client:
        with _open(client) as ws:
            _ready(ws)
            ws.send_bytes(word("hola"))
            assert client.post("/api/speech/model", json={"language": "es"},
                               headers=PAGE).status_code == 200
            error = _until(ws, "error")[-1]
            assert error["code"] == "interrupted" and "Spanish" in error["message"]
        with _open(client) as ws:
            _ready(ws)
        assert _events("interrupted") == [], "a chosen switch is not an incident"


def test_a_state_change_needs_the_pages_origin():
    with TestClient(_app(lambda _model: FakeEngine())) as client:
        r = client.post("/api/speech/model", json={"language": "en"})
        assert r.status_code == 403
        assert client.post("/api/speech/models/es/install").status_code == 403


def test_switching_with_speech_input_stopped_says_so(monkeypatch):
    _put(SPANISH)
    monkeypatch.setattr(transcription_api, "service", None)
    with pytest.raises(speech_models.Refused) as refused:
        asyncio.run(speech_models.switch("es"))
    assert refused.value.status == 503
