"""Pinned Kokoro CPU voice, in one warm, cancellable subprocess.

No model is downloaded at import/startup. Run jarvis.setup_speech_output or
use Voice settings to install it explicitly. The existing sherpa-onnx runtime
and its CPU execution provider are used on Intel and Apple Silicon alike.
"""
from __future__ import annotations
import asyncio
import base64
import io
import json
import os
import re
from pathlib import Path
import sys
import time
import wave
from jarvis import data_paths

MODEL_NAME = 'kokoro-int8-multi-lang-v1_0'
MODEL_URL = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/' + MODEL_NAME + '.tar.bz2'
MODEL_SHA256 = '72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653'
MODEL_BYTES = 132353410
VOICES = {'george': 26, 'daniel': 24, 'lewis': 27, 'emma': 21}
REQUIRED = ('model.int8.onnx', 'voices.bin', 'tokens.txt', 'lexicon-gb-en.txt', 'lexicon-us-en.txt', 'LICENSE', 'espeak-ng-data/phontab')


def model_dir() -> Path:
    return data_paths.data_dir() / 'models' / MODEL_NAME


def model_problem(path: Path | None = None) -> str | None:
    root = path or model_dir()
    if any(not (root / name).is_file() or (root / name).stat().st_size == 0 for name in REQUIRED):
        return 'Local voice model is not installed or is incomplete. Install it in Settings → Voice.'
    return None


class LocalVoice:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._process = None
        self.state = 'not_loaded'
        self.error = None
        self.last_metrics = None

    async def _stop(self):
        process, self._process = self._process, None
        if process and process.returncode is None:
            process.kill()
            await process.wait()
        self.state = 'not_loaded'

    async def close(self):
        async with self._lock:
            await self._stop()

    async def synthesize(self, text: str, voice: str = 'george') -> bytes:
        if voice not in VOICES:
            raise ValueError('Unknown local voice')
        problem = model_problem()
        if problem:
            raise ValueError(problem)
        # Only one native request is ever in flight. Other scheduler chunks
        # await this lock and can be cancelled without queuing CPU jobs.
        async with self._lock:
            try:
                if self._process is None or self._process.returncode is not None:
                    self.state = 'loading'
                    self._process = await asyncio.create_subprocess_exec(
                        sys.executable, '-m', 'jarvis.local_tts', '--worker', str(model_dir()),
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        limit=8 * 1024 * 1024)
                process = self._process
                process.stdin.write((json.dumps({'text': text, 'voice': voice}) + '\n').encode())
                await process.stdin.drain()
                line = await asyncio.wait_for(process.stdout.readline(), timeout=45)
                if not line:
                    raise RuntimeError('Local voice worker exited')
                response = json.loads(line)
                if 'error' in response:
                    raise RuntimeError(response['error'])
                self.state, self.error = 'ready', None
                self.last_metrics = response['metrics']
                return base64.b64decode(response['audio'], validate=True)
            except asyncio.CancelledError:
                # Native ONNX calls cannot be interrupted by cancelling a
                # thread. Stop this owned process; the next turn reloads it.
                await self._stop()
                raise
            except Exception as e:
                await self._stop()
                self.state, self.error = 'error', str(e)
                raise


client = LocalVoice()


_DIGITS = dict(zip("0123456789", "zero one two three four five six seven eight nine".split()))


def readable_text(text: str, known: set[str]) -> tuple[str, list[str]]:
    """The export skips unknown words. Spell them instead of losing content.

    Numeric identifiers are read digit by digit; text shown to the user is
    unchanged. This is deliberately an English preset, not transliteration.
    """
    spelled = []
    def word(match):
        value = match.group().replace("’", "'")
        if value.lower() in known:
            return value
        letters = [_DIGITS.get(c, c.lower()) for c in value if c.isalnum()]
        if not letters or any(c not in known for c in letters):
            raise ValueError("This local English voice cannot pronounce part of the reply; use its text instead")
        spelled.append(value)
        return " ".join(letters)
    return re.sub(r"[^\W_]+(?:['’][^\W_]+)*", word, text), spelled


def _worker(root: Path):
    # Keep native library stdout away from the JSON protocol.
    output = os.fdopen(os.dup(sys.stdout.fileno()), 'w', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    from array import array
    import sherpa_onnx
    t0 = time.monotonic()
    config = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
        kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
            model=str(root / 'model.int8.onnx'), voices=str(root / 'voices.bin'),
            tokens=str(root / 'tokens.txt'), data_dir=str(root / 'espeak-ng-data'),
            lexicon=','.join(str(root / n) for n in ('lexicon-us-en.txt', 'lexicon-gb-en.txt')), lang='en'),
        provider='cpu', num_threads=2))
    known = set()
    for name in ('lexicon-us-en.txt', 'lexicon-gb-en.txt'):
        with (root / name).open() as f:
            known.update(line.split()[0].lower() for line in f if line.strip())
    engine = sherpa_onnx.OfflineTts(config)
    load_sec = time.monotonic() - t0
    for line in sys.stdin:
        try:
            request = json.loads(line)
            text, voice = request['text'], request['voice']
            if not text.strip() or len(text) > 2000 or voice not in VOICES:
                raise ValueError('Local voice requires 1–2000 characters and a preset voice')
            start = time.monotonic()
            normalized, spelled = readable_text(text, known)
            audio = engine.generate(normalized, sid=VOICES[voice], speed=1.0)
            samples = array('h', (max(-32767, min(32767, int(v * 32767))) for v in audio.samples))
            if sys.byteorder != 'little':
                samples.byteswap()
            pcm = samples.tobytes()
            if not pcm:
                raise RuntimeError('Local model returned no audio')
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(audio.sample_rate)
                wav.writeframes(pcm)
            generated = time.monotonic() - start
            seconds = len(pcm) / (2 * audio.sample_rate)
            response = {'audio': base64.b64encode(buf.getvalue()).decode(),
                        'metrics': {'load_sec': load_sec, 'generation_sec': generated,
                                    'audio_sec': seconds, 'rtf': generated / seconds,
                                    'threads': 2, 'provider': 'cpu', 'model': MODEL_NAME,
                                    'spelled_words': len(spelled)}}
            load_sec = 0.0
        except Exception as e:
            response = {'error': str(e)}
        output.write(json.dumps(response) + '\n')


async def benchmark(output: Path | None = None) -> None:
    """A repeatable CPU-only trial; never changes the selected provider."""
    import platform
    result = {"architecture": platform.machine(), "runs": []}
    voice = LocalVoice()
    try:
        for text in ("Good evening, sir. All systems are ready.",
                     "The build passed all forty two checks. We can continue."):
            audio = await voice.synthesize(text)
            result["runs"].append(dict(voice.last_metrics))
        if output:
            with output.open("xb") as f:
                f.write(audio)
            result["audio_file"] = str(output)
    finally:
        await voice.close()
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        _worker(Path(sys.argv[2]))
    else:
        import argparse
        parser = argparse.ArgumentParser(description="Benchmark the local CPU voice; model must be installed first.")
        parser.add_argument('--benchmark', action='store_true')
        parser.add_argument('--output', type=Path, help="Write sample WAV to a new file")
        args = parser.parse_args()
        if args.benchmark:
            asyncio.run(benchmark(args.output))
        else:
            parser.print_help()
