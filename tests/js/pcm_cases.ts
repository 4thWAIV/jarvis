// Runs frontend/src/pcm.ts on known signals and prints the measurements as
// JSON for tests/test_pcm.py. Bundled with esbuild (a Vite dependency).
import { downmix, Framer, Resampler, toPcm16, FRAME_SAMPLES } from "../../frontend/src/pcm";

function tone(rate: number, hz: number, seconds: number): Float32Array {
  const out = new Float32Array(Math.round(rate * seconds));
  for (let i = 0; i < out.length; i++) out[i] = Math.sin((2 * Math.PI * hz * i) / rate);
  return out;
}

function inBlocks(r: Resampler, x: Float32Array, block: number): Float32Array {
  const parts: number[] = [];
  for (let i = 0; i < x.length; i += block) parts.push(...r.push(x.subarray(i, i + block)));
  return Float32Array.from(parts);
}

function rms(x: Float32Array, from = 0): number {
  let s = 0;
  for (let i = from; i < x.length; i++) s += x[i] * x[i];
  return Math.sqrt(s / Math.max(1, x.length - from));
}

const result: Record<string, unknown> = {};
for (const rate of [44100, 48000]) {
  const impulse = new Float32Array(rate);           // one second, a click at sample 0
  impulse[0] = 1;
  const wholeR = new Resampler(rate);
  const whole = wholeR.push(impulse);
  const flushed = whole.length + wholeR.flush().length;
  const blocks = inBlocks(new Resampler(rate), impulse, 128);
  const odd = inBlocks(new Resampler(rate), impulse, 97);
  let first = -1;
  for (let i = 0; i < whole.length; i++) if (Math.abs(whole[i]) > 1e-3) { first = i; break; }
  let maxDiff = 0;
  for (let i = 0; i < Math.min(whole.length, blocks.length, odd.length); i++) {
    maxDiff = Math.max(maxDiff, Math.abs(whole[i] - blocks[i]), Math.abs(whole[i] - odd[i]));
  }
  // A step from silence to a level: the onset must be there, not trimmed.
  const step = new Float32Array(rate / 10).fill(0.5);
  const stepOut = inBlocks(new Resampler(rate), step, 128);
  result[String(rate)] = {
    inLength: impulse.length,
    lengths: [whole.length, blocks.length, odd.length],
    flushedLength: flushed,
    firstNonZero: first,
    blockMaxDiff: maxDiff,
    passband1k: rms(inBlocks(new Resampler(rate), tone(rate, 1000, 0.5), 128), 200) * Math.SQRT2,
    stopband12k: rms(inBlocks(new Resampler(rate), tone(rate, 12000, 0.5), 128), 200) * Math.SQRT2,
    stepSettled: stepOut[stepOut.length - 1],
    stepFirst: Array.from(stepOut.slice(0, 4)),
  };
}
const same = new Float32Array([0.1, -0.2]);
result.passThrough = new Resampler(16000).push(same) === same;
result.pcm16 = Array.from(toPcm16(Float32Array.from([1.5, -1.5, 0.5, -1, 0, 1])));
result.downmix = Array.from(downmix([Float32Array.from([1, 0.5]), Float32Array.from([0, -0.5])]));

const framer = new Framer();
const input = Float32Array.from({ length: 5000 }, (_, i) => i / 5000);
const frames: Float32Array[] = [];
for (let i = 0; i < input.length; i += 128) frames.push(...framer.push(input.subarray(i, i + 128)));
const rest = framer.drain();
const joined = [...frames.flatMap((f) => Array.from(f)), ...rest];
result.framer = {
  frameSamples: FRAME_SAMPLES,
  sizes: frames.map((f) => f.length),
  rest: rest.length,
  intact: joined.length === input.length && joined.every((v, i) => v === input[i]),
};
console.log(JSON.stringify(result));
