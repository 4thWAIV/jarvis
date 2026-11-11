"""Settings is authoritative across processes and preserves record storage."""
from types import SimpleNamespace

import pytest
from jarvis import knowledge_api as api, knowledge_client as kc, knowledge_settings as settings, records
from jarvis.knowledge import embedder
from jarvis.knowledge.engine import Engine
from jarvis.knowledge.uds import EngineUDS


def test_saved_false_overrides_legacy_environment(monkeypatch):
    monkeypatch.setenv("JARVIS_KNOWLEDGE", "1")
    assert kc.enabled()
    settings.change(False)
    assert not kc.enabled()
    assert records.get(settings.PATH).data == {"enabled": False}
    with pytest.raises(ValueError):
        settings.change("false")


def test_engine_reads_same_saved_setting_and_reopens(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_KNOWLEDGE", "0")
    monkeypatch.setattr(embedder, "RUNNER", lambda: embedder.FakeEmbedder(8))
    directory = tmp_path / "settings-engine"
    engine = Engine.open(directory)
    try:
        records.bind(EngineUDS(kc.InProcessTransport(engine)))
        assert not engine.indexing_enabled()
        assert engine.vectors.embedder is None
        settings.change(True)
        assert kc.enabled() and engine.indexing_enabled()
        engine.maintain()
        assert engine.vectors.embedder is not None
        settings.change(False)
        engine.set_pace("free")
        assert engine.vectors.paused
        records.update('/work/test.work', lambda _: {'title': 'Storage still works'})
        assert records.get('/work/test.work').data['title'] == 'Storage still works'
        engine.maintain()
        assert engine.vectors.paused
    finally:
        records.bind(None)
        engine.close()
    engine = Engine.open(directory)
    try:
        assert not engine.indexing_enabled()
        assert engine.items.get('/work/test.work', streams=['data'])['data']['title'] == 'Storage still works'
    finally:
        engine.close()


def test_settings_pause_stops_both_indexing_workers(monkeypatch):
    events = []
    client = SimpleNamespace(pace=lambda mode: events.append(('pace', mode)))
    runtime = SimpleNamespace(
        ingest=SimpleNamespace(stop=lambda: events.append('ingest stopped'), wake=lambda: events.append('wake')),
        content=SimpleNamespace(stop=lambda: events.append('content stopped')),
        pace=SimpleNamespace(mode='free'), supervisor=SimpleNamespace(pid=lambda: 123, client=client))
    ctx = api.Context(lambda: runtime)
    monkeypatch.setattr(api, 'status', lambda _: {'state': 'running'})
    assert api.change_settings(ctx, False)['enabled'] is False
    assert events == ['ingest stopped', 'content stopped', ('pace', 'free')]
    events.clear()
    assert api.change_settings(ctx, True)['enabled'] is True
    assert events == ['wake', ('pace', 'free')]
