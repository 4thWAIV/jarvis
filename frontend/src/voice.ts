/**
 * Voice input and audio output for JARVIS.
 *
 * Input is voiceInput.ts: the microphone streamed to JARVIS's own local
 * speech recognition. The microphone keeps listening while JARVIS speaks —
 * the server decides what is echo and what is a barge-in. Playback is a
 * strictly ordered queue of chunks, and each one ends in exactly one way the
 * page can see: played (it finished), or failed (its audio would not
 * decode). A failed chunk is never reported as played; it is reported as
 * failed, in its turn, so the server's ordering still moves on.
 *
 * The same output also plays the capture cues (`cue`): one of a few quiet
 * tones, at his cue volume, when listening starts or stops and when he
 * pauses in a turn (jarvis.ts decides which). They are not speech: no
 * chunk, no caption and no playback report, and they never touch the
 * microphone. A cue plays now or not at all: while the browser has not let
 * audio run it is dropped, never kept to sound once audio is unlocked;
 * only a Test he clicks may unlock audio for itself.
 */

import type { Tone } from "./voiceControl";

export { createVoiceInput } from "./voiceInput";
export type { InputState, InputStatus, VoiceInput, VoiceInputHooks } from "./voiceInput";

// ---------------------------------------------------------------------------
// Capture cues
// ---------------------------------------------------------------------------

/** Each tone's notes, in hertz, played one after the other: told apart by
 *  pattern and pitch, never by loudness. Placeholders until the sounds are
 *  chosen. */
export const TONE_NOTES: Readonly<Record<Tone, readonly number[]>> = {
  rise: [660, 880],
  fall: [880, 660],
  pulse: [587],
  low: [440],
  double: [740, 740],
  resolve: [784, 523],
};
/** Each note's length, and the gap between notes, in seconds: a whole cue
 *  stays under 150 ms. */
const CUE_NOTE_SEC = 0.055;
const CUE_GAP_SEC = 0.012;
/** Very quiet: each note's peak gain at the full cue volume (100). */
const CUE_GAIN_MAX = 0.1;
/** The rounded envelope's attack and release time constant, in seconds:
 *  no click at either end. */
const CUE_EDGE_SEC = 0.008;

// ---------------------------------------------------------------------------
// Audio Player — ordered chunks with acknowledgements
// ---------------------------------------------------------------------------

export interface AudioPlayer {
  enqueue(base64: string, utt: number, idx: number): Promise<void>;
  onNeedsGesture(cb: () => void): void;
  stop(): void;
  cancelUtterance(utt: number): void;
  dropQueued(): void;
  getAnalyser(): AnalyserNode;
  onPlayed(cb: (utt: number, idx: number) => void): void;
  /** A chunk reached the head of the queue and could not be played. */
  onFailed(cb: (utt: number, idx: number) => void): void;
  /** A chunk has just started playing, and how many seconds it lasts. */
  onStarted(cb: (utt: number, idx: number, seconds: number) => void): void;
  onFinished(cb: () => void): void;
  /** Play a cue's tone now, at `volume` percent, beside any speech:
   *  dropped, not delayed, when the browser has not let audio run, unless
   *  `unlock` (his own click on a Test button) asks to let it run first. */
  cue(tone: Tone, volume: number, unlock?: boolean): void;
}

interface QueuedChunk {
  utt: number;
  idx: number;
  buffer: AudioBuffer | null; // null until decoded
  failed: boolean;
}

export function createAudioPlayer(): AudioPlayer {
  const audioCtx = new AudioContext();
  const analyser = audioCtx.createAnalyser();
  analyser.fftSize = 256;
  analyser.smoothingTimeConstant = 0.8;
  analyser.connect(audioCtx.destination);

  const queue: QueuedChunk[] = [];
  let isPlaying = false;
  let currentUtt: number | null = null;
  let currentSource: AudioBufferSourceNode | null = null;
  let playedCallback: ((utt: number, idx: number) => void) | null = null;
  let failedCallback: ((utt: number, idx: number) => void) | null = null;
  let startedCallback: ((utt: number, idx: number, seconds: number) => void) | null = null;
  let finishedCallback: (() => void) | null = null;
  let needsGestureCallback: (() => void) | null = null;
  let gestureNoticeShown = false;

  async function ensureRunning(): Promise<void> {
    if (audioCtx.state === "running") return;
    // resume() never rejects without a user gesture in Chrome — it just stays
    // pending. Wait a moment, then tell the user, then keep waiting: the
    // promise resolves on their first click and playback continues from here.
    const resumed = audioCtx.resume();
    const timeout = new Promise<void>((resolve) => setTimeout(resolve, 1500));
    await Promise.race([resumed, timeout]);
    const state: string = audioCtx.state; // TS narrows the early return away; re-read it
    if (state !== "running") {
      if (!gestureNoticeShown) {
        gestureNoticeShown = true;
        needsGestureCallback?.();
      }
      await resumed;
    }
  }

  function playNext() {
    // Chunks that failed to decode are reported as failed, in order, so the
    // server's ordering advances without counting them as heard.
    while (queue.length > 0 && queue[0].failed) {
      const dead = queue.shift()!;
      failedCallback?.(dead.utt, dead.idx);
    }
    if (queue.length === 0) {
      isPlaying = false;
      currentSource = null;
      finishedCallback?.();
      return;
    }
    const head = queue[0];
    if (!head.buffer) {
      // Not decoded yet: enqueue() will call playNext() when it is.
      isPlaying = false;
      currentSource = null;
      return;
    }
    queue.shift();
    isPlaying = true;
    const source = audioCtx.createBufferSource();
    source.buffer = head.buffer;
    source.connect(analyser);
    currentSource = source;
    currentUtt = head.utt;
    source.onended = () => {
      if (currentSource === source) {
        currentSource = null;
        currentUtt = null;
        playedCallback?.(head.utt, head.idx);
        playNext();
      }
    };
    source.start();
    startedCallback?.(head.utt, head.idx, head.buffer.duration);
  }

  function playCue(tone: Tone, volume: number, unlock = false): void {
    // Now or never: a source scheduled on a suspended context would
    // sound later, once audio is unlocked. His click on Test is the one
    // exception: it is the gesture that unlocks it, for this cue alone.
    if (audioCtx.state !== "running") {
      if (unlock) audioCtx.resume().then(() => playCue(tone, volume)).catch(() => {});
      return;
    }
    const peak = CUE_GAIN_MAX * Math.min(100, Math.max(0, volume)) / 100;
    // Straight to the output, not through the analyser: the orb draws
    // his speech, not the cue.
    let at = audioCtx.currentTime;
    for (const hz of TONE_NOTES[tone]) {
      const osc = audioCtx.createOscillator();
      osc.type = "sine";
      osc.frequency.value = hz;
      const gain = audioCtx.createGain();
      gain.gain.setValueAtTime(0, at);
      gain.gain.setTargetAtTime(peak, at, CUE_EDGE_SEC / 3);
      gain.gain.setTargetAtTime(0, at + CUE_NOTE_SEC - CUE_EDGE_SEC, CUE_EDGE_SEC / 3);
      osc.connect(gain).connect(audioCtx.destination);
      osc.onended = () => { osc.disconnect(); gain.disconnect(); };
      osc.start(at);
      osc.stop(at + CUE_NOTE_SEC + CUE_EDGE_SEC);
      at += CUE_NOTE_SEC + CUE_GAP_SEC;
    }
  }

  return {
    async enqueue(base64: string, utt: number, idx: number) {
      // Reserve the slot BEFORE any await, so order is preserved even if a later
      // chunk's decode (or the context resume) completes first.
      const item: QueuedChunk = { utt, idx, buffer: null, failed: false };
      queue.push(item);
      try {
        await ensureRunning();
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
        item.buffer = await audioCtx.decodeAudioData(bytes.buffer.slice(0));
      } catch (err) {
        console.error("[audio] chunk unplayable:", err);
        item.failed = true; // reported as failed in order, so the server's sequence advances
      }
      if (!isPlaying) playNext();
    },

    stop() {
      queue.length = 0;
      currentUtt = null;
      if (currentSource) {
        const src = currentSource;
        currentSource = null; // so onended does not ack or chain
        try {
          src.stop();
        } catch {
          // Already stopped
        }
      }
      isPlaying = false;
    },

    cancelUtterance(utt) {
      for (let i = queue.length - 1; i >= 0; i--) if (queue[i].utt === utt) queue.splice(i, 1);
      if (currentUtt === utt && currentSource) {
        const source = currentSource;
        currentSource = null; currentUtt = null; isPlaying = false;
        try { source.stop(); } catch { /* already ended */ }
      }
      if (!isPlaying) playNext();
    },

    dropQueued() {
      // The server keeps exactly one chunk alive: the one playing, or — if
      // nothing has started yet — the head it is still expecting an ack for.
      queue.length = currentSource ? 0 : Math.min(queue.length, 1);
    },

    getAnalyser() {
      return analyser;
    },

    onPlayed(cb) {
      playedCallback = cb;
    },

    onFailed(cb) {
      failedCallback = cb;
    },

    onStarted(cb) {
      startedCallback = cb;
    },

    onFinished(cb) {
      finishedCallback = cb;
    },

    onNeedsGesture(cb) {
      needsGestureCallback = cb;
    },

    cue: playCue,
  };
}
