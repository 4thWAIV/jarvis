/**
 * Turning the microphone's samples into what /ws/transcribe takes: mono,
 * 16 kHz, signed 16-bit little-endian PCM, in 100 ms frames.
 *
 * Pure functions and small stateful classes with no Web Audio in them, so
 * the AudioWorklet (pcm-worklet.ts) uses them as they are and the tests run
 * them under node. Each carries its state across the worklet's 128-sample
 * blocks: nothing is lost or repeated at a block boundary.
 */

import protocol from "../../jarvis/transcribeProtocol.json";

export const TARGET_RATE: number = protocol.sampleRate;
export const FRAME_SAMPLES: number = protocol.frameBytes / 2;

/** Average the channels of one block into one. */
export function downmix(channels: Float32Array[]): Float32Array {
  if (channels.length === 1) return channels[0];
  const out = new Float32Array(channels[0].length);
  for (const ch of channels) for (let i = 0; i < out.length; i++) out[i] += ch[i];
  for (let i = 0; i < out.length; i++) out[i] /= channels.length;
  return out;
}

/**
 * Sample-rate conversion to 16 kHz with an anti-alias filter: a
 * Blackman-windowed sinc low-pass at 0.45 of the output rate, evaluated at
 * each output sample's exact fractional position, so any input rate works
 * (44.1 kHz as well as 48 kHz). The input not yet used and the position of
 * the next output sample are kept between calls. The first input sample is
 * not dropped: the history starts with zeros, so it comes out `halfTaps`
 * input samples late rather than not at all.
 */
export class Resampler {
  private readonly step: number;
  private readonly cutoff: number;
  private history: Float32Array;
  private position: number;

  constructor(readonly inRate: number, readonly outRate = TARGET_RATE,
              private readonly halfTaps = 32) {
    this.step = inRate / outRate;
    // In cycles per input sample; never above the input's own Nyquist.
    this.cutoff = Math.min(0.5, (0.45 * outRate) / inRate);
    this.history = new Float32Array(halfTaps);
    this.position = halfTaps;
  }

  push(input: Float32Array): Float32Array {
    if (this.inRate === this.outRate) return input;
    const joined = new Float32Array(this.history.length + input.length);
    joined.set(this.history);
    joined.set(input, this.history.length);
    const out: number[] = [];
    const n = this.halfTaps;
    while (Math.floor(this.position) + n < joined.length) {
      const centre = Math.floor(this.position);
      const frac = this.position - centre;
      let acc = 0;
      let norm = 0;
      for (let k = -n + 1; k <= n; k++) {
        const x = k - frac;
        const w = this.kernel(x);
        acc += joined[centre + k] * w;
        norm += w;
      }
      out.push(norm ? acc / norm : 0);
      this.position += this.step;
    }
    const keepFrom = Math.max(0, Math.floor(this.position) - n + 1);
    this.history = joined.slice(keepFrom);
    this.position -= keepFrom;
    return Float32Array.from(out);
  }

  /** The output still held back for the filter's look-ahead (a fraction
   *  of a millisecond), for the end of a capture. */
  flush(): Float32Array {
    if (this.inRate === this.outRate) return new Float32Array(0);
    return this.push(new Float32Array(this.halfTaps));
  }

  private kernel(x: number): number {
    const fc = this.cutoff;
    const t = 2 * fc * x;
    const sinc = x === 0 ? 1 : Math.sin(Math.PI * t) / (Math.PI * t);
    const r = (x + this.halfTaps) / (2 * this.halfTaps);       // 0..1 across the window
    const window = r <= 0 || r >= 1 ? 0
      : 0.42 - 0.5 * Math.cos(2 * Math.PI * r) + 0.08 * Math.cos(4 * Math.PI * r);
    return 2 * fc * sinc * window;
  }
}

/** Floats in [-1, 1] as signed 16-bit little-endian bytes, clipped. */
export function toPcm16(samples: Float32Array): Uint8Array {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, Math.round(s < 0 ? s * 32768 : s * 32767), true);
  }
  return bytes;
}

/** Cuts a stream of samples into frames of exactly FRAME_SAMPLES, keeping
 *  the remainder for the next call. */
export class Framer {
  private pending = new Float32Array(0);

  constructor(private readonly size = FRAME_SAMPLES) {}

  push(samples: Float32Array): Float32Array[] {
    const joined = new Float32Array(this.pending.length + samples.length);
    joined.set(this.pending);
    joined.set(samples, this.pending.length);
    const frames: Float32Array[] = [];
    let at = 0;
    for (; at + this.size <= joined.length; at += this.size) {
      frames.push(joined.slice(at, at + this.size));
    }
    this.pending = joined.slice(at);
    return frames;
  }

  /** What is left over, for the last frame before a finish. */
  drain(): Float32Array {
    const rest = this.pending;
    this.pending = new Float32Array(0);
    return rest;
  }
}
