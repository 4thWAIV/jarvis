/**
 * Voice input: the microphone, streamed to JARVIS's own local speech
 * recognition (/ws/transcribe, transcription_api.py), with nothing sent to
 * a browser or cloud recogniser.
 *
 * One getUserMedia stream feeds both the level meter and an AudioWorklet
 * (pcm-worklet.ts) that cuts it into 100 ms frames of 16 kHz PCM16. While
 * input is on, capture is continuous: there are no recogniser sessions to
 * rotate and no gaps between them, and a pause in speech only ends a
 * segment on the server. What it cannot do is hear before the browser has
 * the microphone, or while it is muted, and the status says which.
 *
 * Audio captured while the recogniser is still loading is held (up to
 * `startupBufferSec`) and sent in order, paced, once it is ready. Past any
 * limit the whole attempt is dropped and the page says so; no frame is ever
 * quietly skipped, because losing the start of a sentence is the failure
 * this replaced.
 *
 * Each session asks for diagnostics: once a second it sends the server what
 * the page measured (voiceHealth.takeTelemetry, with the frames captured and
 * sent and the audio waiting here), and the server answers with the health
 * the Diagnostics views draw.
 */

import protocol from "./transcribeProtocol.json";
import { healthHeard, healthLost, takeTelemetry } from "./voiceHealth";
import workletUrl from "./pcm-worklet.ts?worker&url";

export type InputState =
  | "starting"      // asking for the microphone
  | "permission"    // the browser has not given us the microphone
  | "loading"       // the recogniser is loading; what is said is being held
  | "listening"
  | "paused"        // JARVIS is speaking and the mic is set to stop for it
  | "muted"
  | "busy"          // another JARVIS page has the recogniser
  | "interrupted"   // something was said that was not sent; listening again
  | "unavailable";  // no microphone, no recogniser, or no AudioWorklet

export interface InputStatus {
  state: InputState;
  message: string;
}

export interface VoiceInput {
  start(): void;
  stop(): void;
  /** JARVIS is speaking and the mic stops for it: what was being heard is
   *  discarded as echo. */
  pause(): void;
  resume(): void;
  /** The mute button: take nothing new, release the microphone, and still
   *  send the words already spoken. */
  mute(): void;
  /** Take the microphone again after mute, with a fresh session. */
  unmute(): void;
  /** The words in flight cannot be delivered: drop them and say so. */
  interrupt(message: string): void;
}

export interface VoiceInputHooks {
  onLevel?: (level: number) => void;
  onStatus?: (status: InputStatus) => void;
  /** Whether a final transcript can reach JARVIS now (the voice socket is up). */
  canDeliver?: () => boolean;
}

const FRAME_MS = 1000 * (protocol.frameBytes / 2) / protocol.sampleRate;
const HOLD_FRAMES = Math.round((protocol.startupBufferSec * 1000) / FRAME_MS);
const SOCKET_LIMIT_BYTES = protocol.queueLimitSec * protocol.sampleRate * 2;
// Held audio is sent at one and a half times real time (frames per frame's
// worth of time), so a backlog drains without arriving faster than the
// recogniser can take it.
const PACE_PER_TICK = 1.5;
const RETRY_MAX_MS = 30000;
// A muted session may wait for the recogniser to load before its held audio
// can be sent, then has the server's own finish limit to finalise it.
const FINISH_WAIT_MS = 1000 * (protocol.startupBufferSec + protocol.finishLimitSec);
// Connecting normally takes milliseconds; only say so if it does not.
const SAY_CONNECTING_AFTER_MS = 1000;
const METER_EVERY_MS = 100;
const TELEMETRY_EVERY_MS = 1000 * protocol.telemetryEverySec;

interface Frame { pcm: ArrayBuffer; at: number }

interface Microphone {
  stream: MediaStream;
  ctx: AudioContext;
  node: AudioWorkletNode;
  meter: ReturnType<typeof setInterval>;
}

interface Session {
  ws: WebSocket;
  id: string | null;
  ready: boolean;
  closing: boolean;        // mute: send what is held, then finish
  lastFrame: boolean;      // the worklet's partial last frame is in the outbox
  finishSent: boolean;
  closed: boolean;         // nothing it says is acted on any more
  outbox: Frame[];
  credit: number;
  finals: Set<number>;
}

export function createVoiceInput(
  onTranscript: (text: string) => void,
  onInterim: (text: string) => void,
  onMicEvent: (event: string) => void = () => {},
  hooks: VoiceInputHooks = {}
): VoiceInput {
  let wanted = false;        // start() until stop()
  let paused = false;
  let muted = false;
  let mic: Microphone | null = null;
  let micOpening = 0;        // generation of the getUserMedia call in flight
  let session: Session | null = null;
  let closing: Session | null = null;   // the muted session, finishing
  let retryDelay = 1000;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let status: InputStatus = { state: "starting", message: "" };
  let framesCaptured = 0;    // since the last telemetry
  let framesSent = 0;

  const mark = (what: string) => {
    console.info(`[voice] ${new Date().toLocaleTimeString()} ${what}`);
    onMicEvent(what);
  };

  function setStatus(state: InputState, message = "") {
    if (state === status.state && message === status.message) return;
    status = { state, message };
    hooks.onStatus?.(status);
  }

  // ── the microphone ────────────────────────────────────────────────────

  async function openMicrophone() {
    if (mic || micOpening) return;
    const mine = ++micOpening;
    setStatus("starting", "Asking for the microphone…");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) {
      if (mine === micOpening) micOpening = 0;
      const name = (e as Error)?.name;
      mark(`microphone refused: ${name}`);
      if (name === "NotAllowedError" || name === "SecurityError") {
        setStatus("permission", "The microphone is blocked for this page. Allow it in the " +
                  "address bar, then reload. Typing still works.");
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        setStatus("unavailable", "No microphone found. Connect one; typing still works.");
        scheduleRetry(openMicrophone);
      } else {
        setStatus("unavailable", `The microphone could not be opened (${name}).`);
        scheduleRetry(openMicrophone);
      }
      return;
    }
    if (mine !== micOpening || !wanted || muted) {   // stopped or muted while asking
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    let ctx: AudioContext;
    try {
      // Asking for 16 kHz lets the browser resample; the worklet checks the
      // rate it really got and resamples itself if it differs.
      ctx = new AudioContext({ sampleRate: protocol.sampleRate });
    } catch {
      ctx = new AudioContext();
    }
    if (!ctx.audioWorklet) {
      stream.getTracks().forEach((t) => t.stop());
      ctx.close().catch(() => {});
      micOpening = 0;
      setStatus("unavailable", "This browser cannot stream the microphone (no AudioWorklet).");
      return;
    }
    try {
      await ctx.audioWorklet.addModule(workletUrl);
    } catch (e) {
      stream.getTracks().forEach((t) => t.stop());
      ctx.close().catch(() => {});
      micOpening = 0;
      setStatus("unavailable", `The audio worklet failed to load (${(e as Error)?.message}).`);
      return;
    }
    if (mine !== micOpening || !wanted || muted) {
      stream.getTracks().forEach((t) => t.stop());
      ctx.close().catch(() => {});
      return;
    }
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "jarvis-pcm", {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
    });
    // Connected through a muted gain so the graph keeps pulling it; the
    // microphone is never routed to the speakers.
    const silent = ctx.createGain();
    silent.gain.value = 0;
    source.connect(node).connect(silent).connect(ctx.destination);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    source.connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    const meter = setInterval(() => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      hooks.onLevel?.(Math.sqrt(sum / buf.length));
    }, METER_EVERY_MS);

    node.port.onmessage = (event: MessageEvent) => {
      const data = event.data;
      if (data?.type === "frame") onFrame(data.pcm as ArrayBuffer);
      else if (data?.type === "last") onLastFrame(data.pcm as ArrayBuffer);
      else if (data?.type === "rate") mark(`capturing at ${data.inRate} Hz`);
    };
    for (const track of stream.getAudioTracks()) {
      track.onended = () => {
        mark("microphone track ended");
        closeMicrophone();
        dropSession("The microphone was disconnected, so what was being said was not sent.");
        if (wanted && !muted) scheduleRetry(openMicrophone);
      };
    }
    mic = { stream, ctx, node, meter };
    micOpening = 0;
    retryDelay = 1000;
    mark("microphone attached");
    if (ctx.state !== "running") {
      // Not silence: the browser has not let audio run yet. Say so, and
      // start on the first click or key.
      setStatus("permission", "Click anywhere to start listening.");
      const resume = () => ctx.resume().then(() => { if (mic?.ctx === ctx) beginSession(); });
      document.addEventListener("click", resume, { once: true });
      document.addEventListener("keydown", resume, { once: true });
      return;
    }
    beginSession();
  }

  function closeMicrophone() {
    micOpening = 0;
    if (!mic) return;
    clearInterval(mic.meter);
    mic.node.port.onmessage = null;
    mic.stream.getTracks().forEach((t) => { t.onended = null; t.stop(); });
    mic.ctx.close().catch(() => {});
    mic = null;
    hooks.onLevel?.(0);
  }

  // ── the recognition session ───────────────────────────────────────────

  function beginSession() {
    if (!wanted || paused || muted || !mic || session) return;
    const url = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}${protocol.path}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    const s: Session = { ws, id: null, ready: false, closing: false, lastFrame: false,
                         finishSent: false, closed: false, outbox: [], credit: PACE_PER_TICK,
                         finals: new Set() };
    session = s;
    // Credit by the time that has passed, not by ticks: a busy page's timers
    // fire late, and counting ticks would then send slower than the
    // microphone captures, so held audio would never drain.
    let ticked = performance.now();
    const timer = setInterval(() => {
      const now = performance.now();
      s.credit = Math.min(s.credit + PACE_PER_TICK * (now - ticked) / FRAME_MS, PACE_PER_TICK * 2);
      ticked = now;
      pump(s);
    }, FRAME_MS);
    const telemetry = setInterval(() => {
      if (session !== s || !s.ready || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify(takeTelemetry({
        framesCaptured, framesSent,
        heldSec: s.outbox.length * FRAME_MS / 1000,
        socketSec: ws.bufferedAmount / (protocol.sampleRate * 2),
      })));
      framesCaptured = framesSent = 0;
    }, TELEMETRY_EVERY_MS);
    if (status.state !== "interrupted") setStatus("loading");
    setTimeout(() => {
      if (session === s && !s.ready && status.state === "loading" && !status.message) {
        setStatus("loading", "Connecting to speech recognition…");
      }
    }, SAY_CONNECTING_AFTER_MS);

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "start", version: protocol.version,
                               sampleRate: protocol.sampleRate, channels: protocol.channels,
                               format: protocol.format, diagnostics: true }));
    };
    ws.onmessage = (event: MessageEvent) => {
      if (s.closed) return;
      let msg: Record<string, unknown>;
      try { msg = JSON.parse(String(event.data)); } catch { return; }
      if (s.id === null) s.id = String(msg.session ?? "");
      // Anything from a socket that is no longer ours, or a session id that
      // is not this socket's, is stale.
      if (msg.session !== s.id) return;
      const type = msg.type as string;
      if (type === "health") {
        if (session === s) healthHeard(msg);
      } else if (type === "loading") {
        setStatus("loading", "Speech recognition is loading; keep talking, it is being held.");
      } else if (type === "ready") {
        s.ready = true;
        retryDelay = 1000;
        mark("speech recognition ready");
        // An interruption stays on screen until something is heard again.
        if (session === s && status.state !== "interrupted") setStatus("listening");
        pump(s);
      } else if (type === "interim") {
        if (session === s || s.closing) onInterim(String(msg.text ?? ""));
      } else if (type === "final") {
        // Only the live session's words, or the muted one finishing: a
        // session dropped as echo or interrupted never submits anything.
        if (session !== s && !s.closing) return;
        const segment = Number(msg.segment);
        if (s.finals.has(segment)) return;
        s.finals.add(segment);
        const text = String(msg.text ?? "").trim();
        if (!text) return;
        if (hooks.canDeliver && !hooks.canDeliver()) {
          interruptWith("Lost the connection to JARVIS, so that was not sent. " +
                        "Say it again once it reconnects.");
          return;
        }
        onTranscript(text);
        if (session === s && status.state === "interrupted") setStatus("listening");
      } else if (type === "finished") {
        ws.close();
      } else if (type === "error") {
        onServerError(s, String(msg.code ?? ""), String(msg.message ?? ""));
      }
    };
    ws.onclose = () => {
      s.closed = true;
      clearInterval(timer);
      clearInterval(telemetry);
      if (!session || session === s) healthLost();
      if (closing === s) closing = null;
      if (session !== s) return;
      session = null;
      if (!wanted || paused || muted) return;
      // The server went away mid-session (a restart): what was in flight is lost.
      if (s.ready) {
        setStatus("interrupted", "Lost the connection to speech recognition; reconnecting.");
      }
      scheduleRetry(beginSession);
    };
  }

  function onServerError(s: Session, code: string, message: string) {
    mark(`speech recognition error: ${code}`);
    s.closed = true;
    if (session === s) session = null;
    if (closing === s) closing = null;
    s.ws.close();
    if (code === "busy") {
      setStatus("busy", message || "Another JARVIS page is listening. Typing still works here.");
    } else if (code === "model-unavailable") {
      setStatus("unavailable", message);
    } else {
      setStatus("interrupted", `${message} Listening again.`);
    }
    if (wanted && !paused && !muted) scheduleRetry(beginSession);
  }

  function onFrame(pcm: ArrayBuffer) {
    framesCaptured++;
    // After mute, only the frames the worklet flushes for the muted session.
    const s = muted ? (closing && !closing.lastFrame ? closing : null) : session;
    if (!s || (paused && !muted)) return;
    s.outbox.push({ pcm, at: performance.now() });
    if (s === session && s.outbox.length > HOLD_FRAMES) {
      dropSession(s.ready
        ? "Speech recognition fell behind, so that was not sent. Listening again."
        : "Speech recognition took too long to start, so what you said was not sent. " +
          "Say it again once it is listening.");
      scheduleRetry(beginSession);
      return;
    }
    pump(s);
  }

  function onLastFrame(pcm: ArrayBuffer) {
    const s = closing;
    if (s && !s.lastFrame) {
      if (pcm.byteLength) s.outbox.push({ pcm, at: performance.now() });
      s.lastFrame = true;
      pump(s);
    }
    if (muted) closeMicrophone();
  }

  function pump(s: Session) {
    if (!s.ready || s.ws.readyState !== WebSocket.OPEN) return;
    while (s.outbox.length && s.credit >= 1) {
      if (s.ws.bufferedAmount > SOCKET_LIMIT_BYTES) {
        dropSession("The connection to speech recognition is backed up, so that was not " +
                    "sent. Listening again.");
        scheduleRetry(beginSession);
        return;
      }
      s.ws.send(s.outbox.shift()!.pcm);
      s.credit -= 1;
      framesSent++;
    }
    if (s.closing && s.lastFrame && !s.finishSent && !s.outbox.length) {
      s.ws.send(JSON.stringify({ type: "finish" }));
      s.finishSent = true;          // once; the server closes it after "finished"
    }
  }

  /** Discard the session's audio without sending it. */
  function dropSession(message?: string) {
    const s = session;
    session = null;
    if (s) {
      s.closed = true;
      try {
        if (s.ws.readyState === WebSocket.OPEN) s.ws.send(JSON.stringify({ type: "abort" }));
        s.ws.close();
      } catch { /* closing anyway */ }
    }
    if (message) {
      mark(`input interrupted: ${message}`);
      setStatus("interrupted", message);
    }
  }

  /** Mute: stop taking audio, ask the worklet for its partial last frame,
   *  and finish the session with everything held, even if the recogniser
   *  is still loading (it is sent once ready). The microphone is released
   *  when the last frame arrives, or after a moment if it never does. */
  function finishSession() {
    const s = session;
    session = null;
    if (s) {
      s.closing = true;
      closing = s;
      setTimeout(() => {
        if (s.closed) return;
        s.closed = true;
        if (closing === s) closing = null;
        try { s.ws.close(); } catch { /* closing anyway */ }
        mark("muted session could not finish");
        setStatus("interrupted", "The last words before mute could not be finished, so " +
                  "they were not sent.");
      }, FINISH_WAIT_MS);
    }
    if (mic) {
      mic.node.port.postMessage({ type: "flush" });
      setTimeout(() => onLastFrame(new ArrayBuffer(0)), 300);
    } else {
      onLastFrame(new ArrayBuffer(0));
    }
  }

  function interruptWith(message: string) {
    dropSession(message);
    if (wanted && !paused && !muted) scheduleRetry(beginSession);
  }

  function scheduleRetry(what: () => void) {
    if (retryTimer) return;
    const delay = retryDelay;
    retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (wanted) what();
    }, delay);
  }

  function cancelRetry() {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  }

  return {
    start() {
      wanted = true;
      paused = false;
      if (!muted) {
        if (mic) beginSession(); else void openMicrophone();
      }
    },
    stop() {
      wanted = false;
      cancelRetry();
      dropSession();
      if (closing) { try { closing.ws.close(); } catch { /* closing anyway */ } closing = null; }
      closeMicrophone();
    },
    pause() {
      if (paused) return;
      paused = true;
      dropSession();
      if (!muted) setStatus("paused");
    },
    resume() {
      if (!paused) return;
      paused = false;
      if (!wanted || muted) return;
      if (mic) beginSession(); else void openMicrophone();
    },
    mute() {
      if (muted) return;
      muted = true;
      cancelRetry();
      finishSession();
      setStatus("muted");
    },
    unmute() {
      if (!muted) return;
      // The muted session keeps whatever it already has; the old microphone
      // goes now rather than waiting for its last partial frame.
      if (closing && !closing.lastFrame) {
        closing.lastFrame = true;
        pump(closing);
      }
      closeMicrophone();
      muted = false;
      retryDelay = 1000;
      // A fresh microphone and, once it is capturing, a fresh session.
      if (wanted) void openMicrophone();
      if (paused) setStatus("paused");
    },
    interrupt(message: string) {
      interruptWith(message);
    },
  };
}
