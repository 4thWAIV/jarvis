"""transcriber.py without the model: the session's segmenting over a fake
recognizer, PCM conversion, where the model is looked for, and the setup
helper's checks on the archive."""

import hashlib
import io
import struct
import sys
import tarfile
from pathlib import Path

import pytest

import transcriber
from transcriber import RecognitionEvent, SherpaSession

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import setup_speech_input  # noqa: E402


class FakeStream:
    def __init__(self):
        self.samples = []
        self.finished = False

    def accept_waveform(self, rate, samples):
        assert rate == transcriber.SAMPLE_RATE
        self.samples += samples

    def input_finished(self):
        self.finished = True


class FakeRecognizer:
    """Hears a script: `script[i]` is the text after the i-th frame, and
    `endpoints` the frames after which an endpoint is detected."""

    def __init__(self, script, endpoints=()):
        self.script = script
        self.endpoints = set(endpoints)
        self.frame = -1
        self.resets = 0
        self.tail_text = None

    def create_stream(self):
        return FakeStream()

    def is_ready(self, stream):
        return False

    def decode_stream(self, stream):
        pass

    def get_result(self, stream):
        if stream.finished:
            return self.tail_text or ""
        return self.script[self.frame]

    def is_endpoint(self, stream):
        return self.frame in self.endpoints

    def reset(self, stream):
        self.resets += 1


def _feed(session, recognizer, frames):
    events = []
    for _ in range(frames):
        recognizer.frame += 1
        events += session.accept_pcm16(b"\x00\x00" * 4)
    return events


def test_changed_text_is_an_interim_and_unchanged_text_is_nothing():
    rec = FakeRecognizer(["", "turn", "turn", "turn on", ""], endpoints={4})
    events = _feed(SherpaSession(rec), rec, 4)
    assert events == [RecognitionEvent("interim", "turn", 0),
                      RecognitionEvent("interim", "turn on", 0)]


def test_an_endpoint_gives_one_final_and_resets_without_closing():
    rec = FakeRecognizer(["lights", "lights off", "lights off", "", "", "again"],
                         endpoints={2})
    session = SherpaSession(rec)
    events = _feed(session, rec, 6)
    finals = [e for e in events if e.kind == "final"]
    assert finals == [RecognitionEvent("final", "lights off", 0)]
    assert rec.resets == 1
    assert events[-1] == RecognitionEvent("interim", "again", 1)


def test_an_endpoint_in_silence_is_not_a_command():
    rec = FakeRecognizer(["", "", ""], endpoints={1})
    events = _feed(SherpaSession(rec), rec, 3)
    assert events == []
    assert rec.resets == 1


def test_finish_pads_the_tail_and_finalises_what_is_left():
    rec = FakeRecognizer(["stop", "stop here"])
    rec.tail_text = "stop here now"
    session = SherpaSession(rec)
    _feed(session, rec, 2)
    assert session.finish() == [RecognitionEvent("final", "stop here now", 0)]
    assert len(session._stream.samples) >= transcriber.TAIL_PADDING_SEC * 16000
    assert session._stream.finished


def test_pcm16_is_read_little_endian_and_signed():
    pcm = struct.pack("<4h", 0, 16384, -32768, 32767)
    assert transcriber.pcm16_to_float(pcm) == [0.0, 0.5, -1.0, 32767 / 32768]


def test_the_model_is_found_in_the_data_folder_or_where_the_override_says(
        monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("JARVIS_STT_MODEL_DIR", raising=False)
    assert transcriber.model_dir() == tmp_path / "data" / "models" / transcriber.MODEL_NAME
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(tmp_path / "elsewhere"))
    assert transcriber.model_dir() == tmp_path / "elsewhere"


def test_a_missing_or_incomplete_model_names_the_setup_command(tmp_path):
    assert transcriber.SETUP_COMMAND in transcriber.model_problem(tmp_path / "none")
    (tmp_path / "m").mkdir()
    (tmp_path / "m" / "tokens.txt").write_text("a 0")
    problem = transcriber.model_problem(tmp_path / "m")
    assert "encoder.int8.onnx" in problem and "tokens.txt" not in problem.split("missing")[1]
    for name in transcriber.MODEL_FILES:
        (tmp_path / "m" / name).write_text("x")
    assert transcriber.model_problem(tmp_path / "m") is None


def test_building_the_engine_without_a_model_loads_nothing(tmp_path):
    with pytest.raises(FileNotFoundError, match="setup_speech_input"):
        transcriber.SherpaEngine(tmp_path / "none")


def _archive(tmp_path, entries):
    """A .tar.bz2 of (name, bytes-or-None-for-symlink) entries."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            if data is None:
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    path = tmp_path / "model.tar.bz2"
    path.write_bytes(buf.getvalue())
    return path


def _good_entries():
    return [(f"{transcriber.MODEL_NAME}/{n}", b"weights") for n in transcriber.MODEL_FILES]


@pytest.fixture
def destination(monkeypatch, tmp_path):
    dest = tmp_path / "models" / transcriber.MODEL_NAME
    monkeypatch.setenv("JARVIS_STT_MODEL_DIR", str(dest))
    return dest


def _trust(monkeypatch, archive):
    monkeypatch.setattr(transcriber, "MODEL_SHA256",
                        hashlib.sha256(archive.read_bytes()).hexdigest())


def test_setup_installs_a_verified_archive_in_one_rename(monkeypatch, tmp_path, destination):
    archive = _archive(tmp_path, _good_entries())
    _trust(monkeypatch, archive)
    assert setup_speech_input.install(archive) == destination
    assert transcriber.model_problem(destination) is None
    assert [p.name for p in destination.parent.iterdir()] == [transcriber.MODEL_NAME]


def test_setup_refuses_an_archive_whose_digest_is_not_the_releases(tmp_path, destination):
    archive = _archive(tmp_path, _good_entries())
    with pytest.raises(SystemExit, match="SHA-256"):
        setup_speech_input.install(archive)
    assert not destination.exists()


@pytest.mark.parametrize("bad", [
    ("../escape", b"x"),
    ("elsewhere/tokens.txt", b"x"),
    (f"{transcriber.MODEL_NAME}/link", None),
])
def test_setup_refuses_paths_that_escape_and_links(monkeypatch, tmp_path, destination, bad):
    archive = _archive(tmp_path, _good_entries() + [bad])
    _trust(monkeypatch, archive)
    with pytest.raises(SystemExit, match="Refused"):
        setup_speech_input.install(archive)
    assert not destination.exists()


def test_setup_refuses_an_archive_missing_a_file(monkeypatch, tmp_path, destination):
    archive = _archive(tmp_path, _good_entries()[:-1])
    _trust(monkeypatch, archive)
    with pytest.raises(SystemExit, match="incomplete"):
        setup_speech_input.install(archive)
    assert not destination.exists()
