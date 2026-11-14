import asyncio
import io
import wave
from pathlib import Path
import pytest
from jarvis import local_tts
from tests.settings_fixtures import hold_setting


def test_missing_model_is_actionable_and_does_not_download(tmp_path):
    assert 'Install' in local_tts.model_problem(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_missing_model_never_starts_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(local_tts, 'model_dir', lambda: tmp_path)
    async def forbidden(*a, **kw):
        pytest.fail('a missing model must not start a process or download')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    with pytest.raises(ValueError, match='not installed'):
        await local_tts.LocalVoice().synthesize('Hello')


@pytest.mark.asyncio
async def test_local_provider_never_falls_back_to_fish(monkeypatch):
    from jarvis import server
    hold_setting("tts_provider", 'local')
    async def broken(*args):
        raise RuntimeError('worker failed')
    async def fish(*a, **kw):
        pytest.fail('local failure must not call Fish')
    monkeypatch.setattr(local_tts.client, 'synthesize', broken)
    monkeypatch.setattr(server.tts, 'synthesize_chunk', fish)
    assert await server._synth_for_speech('Hello') is None


def test_provider_values_are_bounded_and_enum_checked():
    from jarvis import settings_service, settings_specs
    check = settings_service.check
    assert check(settings_specs.spec('tts_provider'), 'local') == 'local'
    with pytest.raises(settings_service.Refused):
        check(settings_specs.spec('tts_provider'), 'other')
    assert check(settings_specs.spec('local_voice'), 'george') == 'george'
    with pytest.raises(settings_service.Refused):
        check(settings_specs.spec('local_voice'), '../voice')


def test_wav_playback_has_real_ack_floor():
    from jarvis.speech import ack_floor_seconds, ACK_FLOOR_FACTOR
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b'\0\0' * 24000)
    assert ack_floor_seconds(buf.getvalue()) == pytest.approx(ACK_FLOOR_FACTOR)
    assert ack_floor_seconds(b'RIFFbroken') == 0


@pytest.mark.asyncio
async def test_utterance_cancel_stops_queued_and_inflight_synthesis():
    from jarvis.speech import SpeechScheduler
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def synth(text):
        started.set()
        try: await asyncio.Future()
        finally: cancelled.set()
    async def emit(msg): pass
    s = SpeechScheduler(synth, emit)
    await s.start()
    try:
        u = s.begin_turn()
        s.feed(u, 'Long generation.')
        await s.end_turn(u)
        await asyncio.wait_for(started.wait(), 1)
        await s.barge_in()
        await asyncio.wait_for(cancelled.wait(), 1)
        assert u.cancelled
    finally: await s.stop()


def test_local_diagnostics_do_not_require_fish():
    from jarvis.subsystem_diagnostics import voice_output, Outcomes
    result = voice_output(False, Outcomes(), 1, {'state': 'ready', 'error': None})
    assert result.state == 'ok'
    assert result.checks[0].id == 'local'


def test_bad_archive_keeps_existing_files(tmp_path, monkeypatch):
    from jarvis.setup_speech_output import install
    monkeypatch.setattr(local_tts, 'model_dir', lambda: tmp_path / 'model')
    existing = tmp_path / 'model'; existing.mkdir()
    (existing / 'original').write_text('keep')
    archive = tmp_path / 'bad.tar.bz2'; archive.write_bytes(b'invalid')
    with pytest.raises(SystemExit, match='SHA-256'):
        install(archive)
    assert (existing / 'original').read_text() == 'keep'


@pytest.mark.asyncio
async def test_unknown_provider_never_calls_fish(monkeypatch):
    """A provider that is not one of the two cannot be chosen at all, and a
    voice that somehow reads one fails closed."""
    from jarvis import server, settings_service
    with pytest.raises(settings_service.Refused):
        hold_setting("tts_provider", 'typo')
    monkeypatch.setattr(server, '_setting', lambda key: 'typo' if key == 'tts_provider'
                        else settings_service.value(key))
    async def fish(*a, **kw): pytest.fail('invalid setting must fail closed')
    monkeypatch.setattr(server.tts, 'synthesize_chunk', fish)
    assert await server._synth_for_speech('Hello') is None



def test_unknown_names_and_numbers_are_spelled_instead_of_skipped():
    known = set('jarvis version ready a b c d e f g h i j k l m n o p q r s t u v w x y z zero one two three four five six seven eight nine'.split())
    text, spelled = local_tts.readable_text('Jarvis version 42, Denice and i9.', known)
    assert text == 'Jarvis version four two, d e n i c e a n d i nine.'
    assert spelled == ['42', 'Denice', 'and', 'i9']
    with pytest.raises(ValueError, match='cannot pronounce'):
        local_tts.readable_text('日本語', known)



def test_decimal_and_percent_keep_their_meaning():
    known = set('four two five point percent'.split())
    text, _ = local_tts.readable_text('42.5%', known)
    assert text.strip() == 'four two point five percent'
