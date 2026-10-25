/**
 * The AudioWorklet that turns the microphone into /ws/transcribe's frames:
 * every 128-sample block is downmixed, resampled to 16 kHz when the context
 * runs at another rate, and cut into 100 ms frames of PCM16, each posted to
 * the page as it fills. `{type: "flush"}` posts what is left as a short last
 * frame (for a finish). It produces no sound: its output is silent.
 */

import { downmix, Framer, Resampler, toPcm16, TARGET_RATE } from "./pcm";

// The AudioWorkletGlobalScope's own names, which the DOM lib does not declare.
declare const sampleRate: number;
declare function registerProcessor(name: string, ctor: unknown): void;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}

class PcmProcessor extends AudioWorkletProcessor {
  private readonly resampler = new Resampler(sampleRate, TARGET_RATE);
  private readonly framer = new Framer();

  constructor() {
    super();
    this.port.onmessage = (event: MessageEvent) => {
      if (event.data?.type === "flush") {
        for (const frame of this.framer.push(this.resampler.flush())) this.post("frame", frame);
        this.post("last", this.framer.drain());
      }
    };
    this.port.postMessage({ type: "rate", inRate: sampleRate });
  }

  process(inputs: Float32Array[][]): boolean {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    for (const frame of this.framer.push(this.resampler.push(downmix(input)))) {
      this.post("frame", frame);
    }
    return true;
  }

  private post(type: "frame" | "last", samples: Float32Array): void {
    const bytes = toPcm16(samples);
    this.port.postMessage({ type, pcm: bytes.buffer }, [bytes.buffer]);
  }
}

registerProcessor("jarvis-pcm", PcmProcessor);
