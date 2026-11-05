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
 * the microphone, while it is muted or while the microphone is off, and the
 * status says which. Mute keeps the microphone open and sends nothing, so
 * unmuting is only a new session; mic off releases the microphone itself.
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
 *
 * What it says is kept as its message (`InputWords`), not as text, so the
 * health dot says it in the interface's language whenever it is shown; the
 * status also carries the text as it was first shown, for the record. A
 * server's or the browser's own words are passed on as they came, marked
 * as detail.
 */

import protocol from "../../jarvis/transcribeProtocol.json";
import { language, t, translate, type MessageKey, type Said, type Translate } from "./locale";
import { healthHeard, healthLost, takeTelemetry } from "./voiceHealth";
import workletUrl from "./pcm-worklet.ts?worker&url";

export type InputState =
  | "starting"      // asking for the microphone
  | "permission"    // the browser has not given us the microphone
  | "loading"       // the recogniser is loading; what is said is being held
  | "listening"
  | "paused"        // JARVIS is speaking and the mic is set to stop for it
  | "muted"         // the microphone is open and nothing is sent
  | "off"           // the microphone is released
  | "busy"          // another JARVIS page has the recogniser
  | "interrupted"   // something was said that was not sent; listening again
  | "unavailable";  // no microphone, no recogniser, or no AudioWorklet

/** Words voice input says: their message, said again in whichever
 *  language is in force when they are shown. */
export interface InputWords {
  readonly key: string;
  readonly say: (tr: Translate) => string;
}

type AfterTwo<T> = T extends [unknown, unknown, ...infer R] ? R : never;

/** One message and its named values, as words to say later. */
export function inputWords<K extends MessageKey>(key: K,
                                                 ...args: AfterTwo<Parameters<typeof translate<K>>>): InputWords {
  return { key, say: (tr) => (tr as (k: string, ...a: unknown[]) => string)(key, ...args) };
}

/** The words, followed by the server's or the browser's own detail as it came. */
function withDetail(words: InputWords, detail: string): InputWords {
  if (!detail) return words;
  return { key: `${words.key}+detail`,
           say: (tr) => tr("said.withDetail", { message: words.say(tr), detail }) };
}

export interface InputStatus {
  state: InputState;
  /** What it said, as first shown (empty when nothing): for the record. */
  message: string;
  /** What it says, in the language in force: for showing. */
  words: InputWords | null;
}

/** What the recogniser refuses, by its error code (transcribeProtocol.json
 *  `errors`), apart from busy and model-unavailable, which have their own
 *  states. */
const SERVER_ERRORS: Record<string, InputWords> = {
  overload: inputWords("left.input.overload"),
  "decoder-failed": inputWords("left.input.decoderFailed"),
  interrupted: inputWords("left.input.interrupted"),
  "invalid-format": inputWords("left.input.invalidFormat"),
};

/** A language's name in the interface's language ("Spanish", "español"). */
function languageName(code: string | undefined): string {
  if (!code) return "";
  try {
    return new Intl.DisplayNames([language.get()], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** What a refusal of a language or speech model change, or of a model's
 *  download, says (speech_models.Refused's body: its `code`, `values`,
 *  English `error` and `detail`; an install's `errorCode` as `code`). */
export interface SpeechRefusal { code?: string; values?: Record<string, string>; error?: string; detail?: string }

/** A speech refusal as words for the person: in the language in force
 *  each time they are shown, with the server's own words after them as
 *  detail where they say more. A refusal whose code the page does not know
 *  is shown as it came, as original detail. */
export function speechRefusal(refusal: SpeechRefusal): Said {
  const v = refusal.values ?? {};
  const tr = t;
  const said: Record<string, () => string> = {
    "unknown-language": () => tr("left.speech.unknownLanguage"),
    "not-running": () => tr("left.speech.notRunning"),
    "not-installed": () => tr("left.speech.notInstalled", { language: languageName(v.language) }),
    "load-failed": () => tr("left.speech.loadFailed"),
    "switched-meanwhile": () => tr("left.speech.switchedMeanwhile",
                                   { language: languageName(v.language), wanted: languageName(v.wanted) }),
    "install-refused": () => tr("left.speech.installRefused"),
    "download-failed": () => tr("left.speech.downloadFailed"),
  };
  const words = refusal.code ? said[refusal.code] : undefined;
  if (!words) return { original: refusal.detail || refusal.error || "" };
  return { words, detail: refusal.detail || undefined };
}

export interface VoiceInput {
  start(): void;
  stop(): void;
  /** JARVIS is speaking and the mic stops for it: what was being heard is
   *  discarded as echo. */
  pause(): void;
  resume(): void;
  /** The mute button: send the words already spoken, then nothing; the
   *  microphone stays open and the level reads zero. */
  mute(): void;
  /** Send again after mute: a fresh session on the open microphone. */
  unmute(): void;
  /** The mic-off button: as mute, then release the microphone. */
  micOff(): void;
  /** Take the microphone again after mic off; still muted if mute is on. */
  micOn(): void;
  /** The words in flight cannot be delivered: drop them and say so. */
  interrupt(words: InputWords): void;
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

/** Every captured frame, as 16 kHz mono PCM16, whether or not it is sent
 *  to the recogniser: muted, JARVIS hears nothing and a listener still
 *  does; mic off, there are no frames. A page package's frame listens
 *  through this (packageBridge.ts), since a frame without an origin cannot
 *  have the microphone itself. Returns the way to stop listening. */
const frameListeners = new Set<(pcm: ArrayBuffer) => void>();
export function listenToMicrophone(listener: (pcm: ArrayBuffer) => void): () => void {
  frameListeners.add(listener);
  return () => { frameListeners.delete(listener); };
}
export const MICROPHONE_FORMAT = {
  sampleRate: protocol.sampleRate, channels: protocol.channels, format: protocol.format,
} as const;

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
  let off = false;           // the microphone is released
  let mic: Microphone | null = null;
  let micOpening = 0;        // generation of the getUserMedia call in flight
  let session: Session | null = null;
  let closing: Session | null = null;   // the muted session, finishing
  let retryDelay = 1000;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let status: InputStatus = { state: "starting", message: "", words: null };
  let framesCaptured = 0;    // since the last telemetry
  let framesSent = 0;

  /** Nothing is sent: muted, or the microphone is off. */
  const quiet = () => muted || off;

  const mark = (what: string) => {
    console.info(`[voice] ${new Date().toLocaleTimeString()} ${what}`);
    onMicEvent(what);
  };

  function setStatus(state: InputState, words: InputWords | null = null) {
    const message = words ? words.say(t) : "";
    if (state === status.state && message === status.message) return;
    status = { state, message, words };
    hooks.onStatus?.(status);
  }

  /** The words as the server's log has them: always in English. */
  const logged = (words: InputWords) =>
    words.say(((key: string, ...args: unknown[]) =>
      (translate as (lang: "en", k: string, ...a: unknown[]) => string)("en", key, ...args)) as Translate);

  // ── the microphone ────────────────────────────────────────────────────

  async function openMicrophone() {
    if (mic || micOpening) return;
    const mine = ++micOpening;
    setStatus("starting", inputWords("left.input.asking"));
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
        setStatus("permission", inputWords("left.input.blocked"));
      } else if (name === "NotFoundError" || name === "OverconstrainedError") {
        setStatus("unavailable", inputWords("left.input.noMicrophone"));
        scheduleRetry(openMicrophone);
      } else {
        setStatus("unavailable", inputWords("left.input.cannotOpen", { name: String(name) }));
        scheduleRetry(openMicrophone);
      }
      return;
    }
    if (mine !== micOpening || !wanted || off) {     // stopped or turned off while asking
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
      setStatus("unavailable", inputWords("left.input.noWorklet"));
      return;
    }
    try {
      await ctx.audioWorklet.addModule(workletUrl);
    } catch (e) {
      stream.getTracks().forEach((t) => t.stop());
      ctx.close().catch(() => {});
      micOpening = 0;
      setStatus("unavailable", inputWords("left.input.workletFailed", { error: String((e as Error)?.message) }));
      return;
    }
    if (mine !== micOpening || !wanted || off) {
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
      hooks.onLevel?.(quiet() ? 0 : Math.sqrt(sum / buf.length));
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
        dropSession(inputWords("left.input.disconnected"));
        if (wanted && !off) scheduleRetry(openMicrophone);
      };
    }
    mic = { stream, ctx, node, meter };
    micOpening = 0;
    retryDelay = 1000;
    mark("microphone attached");
    if (ctx.state !== "running") {
      // Not silence: the browser has not let audio run yet. Say so, and
      // start on the first click or key.
      setStatus("permission", inputWords("left.input.clickToStart"));
      const resume = () => ctx.resume().then(() => {
        if (mic?.ctx !== ctx) return;
        if (muted) setStatus("muted"); else beginSession();
      });
      document.addEventListener("click", resume, { once: true });
      document.addEventListener("keydown", resume, { once: true });
      return;
    }
    if (muted) setStatus("muted"); else beginSession();
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
    if (!wanted || paused || quiet() || !mic || session) return;
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
        setStatus("loading", inputWords("left.input.connecting"));
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
        setStatus("loading", inputWords("left.input.loading"));
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
          interruptWith(inputWords("left.input.lostJarvis"));
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
      if (closing === s) {
        closing = null;
        // Unmuted while it was finishing: the recogniser takes one capture
        // at a time, so the fresh session starts now.
        beginSession();
      }
      if (session !== s) return;
      session = null;
      if (!wanted || paused || quiet()) return;
      // The server went away mid-session (a restart): what was in flight is lost.
      if (s.ready) {
        setStatus("interrupted", inputWords("left.input.lostRecognition"));
      }
      scheduleRetry(beginSession);
    };
  }

  function onServerError(s: Session, code: string, message: string) {
    mark(`speech recognition error: ${code}${message ? ` (${message})` : ""}`);
    s.closed = true;
    if (session === s) session = null;
    if (closing === s) {
      closing = null;
      beginSession();
    }
    s.ws.close();
    // Said by its code; the server's own words follow as detail where they
    // say more than the code does.
    if (code === "busy") {
      setStatus("busy", inputWords("left.input.busy"));
    } else if (code === "model-unavailable") {
      setStatus("unavailable", withDetail(inputWords("left.input.modelUnavailable"), message));
    } else {
      setStatus("interrupted", withDetail(SERVER_ERRORS[code] ?? inputWords("left.input.interrupted"),
                                          code === "overload" ? "" : message));
    }
    if (wanted && !paused && !quiet()) scheduleRetry(beginSession);
  }

  function onFrame(pcm: ArrayBuffer) {
    framesCaptured++;
    frameListeners.forEach((listener) => listener(pcm.slice(0)));
    // While quiet, only the frames the worklet flushes for the muted session.
    const s = quiet() ? (closing && !closing.lastFrame ? closing : null) : session;
    if (!s || (paused && !quiet())) return;
    s.outbox.push({ pcm, at: performance.now() });
    if (s === session && s.outbox.length > HOLD_FRAMES) {
      dropSession(inputWords(s.ready ? "left.input.fellBehind" : "left.input.tooSlow"));
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
    if (off) closeMicrophone();
  }

  function pump(s: Session) {
    if (!s.ready || s.ws.readyState !== WebSocket.OPEN) return;
    while (s.outbox.length && s.credit >= 1) {
      if (s.ws.bufferedAmount > SOCKET_LIMIT_BYTES) {
        dropSession(inputWords("left.input.backedUp"));
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
  function dropSession(words?: InputWords) {
    const s = session;
    session = null;
    if (s) {
      s.closed = true;
      try {
        if (s.ws.readyState === WebSocket.OPEN) s.ws.send(JSON.stringify({ type: "abort" }));
        s.ws.close();
      } catch { /* closing anyway */ }
    }
    if (words) {
      mark(`input interrupted: ${logged(words)}`);
      setStatus("interrupted", words);
    }
  }

  /** Mute: stop taking audio, ask the worklet for its partial last frame,
   *  and finish the session with everything held, even if the recogniser
   *  is still loading (it is sent once ready). With the microphone off it
   *  is released when the last frame arrives, or after a moment if it
   *  never does. */
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
        setStatus("interrupted", inputWords("left.input.muteUnfinished"));
      }, FINISH_WAIT_MS);
    }
    if (mic) {
      mic.node.port.postMessage({ type: "flush" });
      setTimeout(() => onLastFrame(new ArrayBuffer(0)), 300);
    } else {
      onLastFrame(new ArrayBuffer(0));
    }
  }

  /** Mute or mic off from sending: finish the session and read zero. */
  function stopSending() {
    cancelRetry();
    finishSession();
    hooks.onLevel?.(0);
  }

  /** The muted session keeps whatever it already has; nothing more is
   *  flushed into it. */
  function endMutedSession() {
    if (closing && !closing.lastFrame) {
      closing.lastFrame = true;
      pump(closing);
    }
  }

  function interruptWith(words: InputWords) {
    dropSession(words);
    if (wanted && !paused && !quiet()) scheduleRetry(beginSession);
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
      if (!off) {
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
      if (!quiet()) setStatus("paused");
    },
    resume() {
      if (!paused) return;
      paused = false;
      if (!wanted || quiet()) return;
      if (mic) beginSession(); else void openMicrophone();
    },
    mute() {
      if (muted) return;
      const wasQuiet = quiet();
      muted = true;
      if (!wasQuiet) stopSending();
      if (!off) setStatus("muted");
    },
    unmute() {
      if (!muted) return;
      muted = false;
      if (off) return;
      endMutedSession();
      retryDelay = 1000;
      // The microphone is still open: only a fresh session, once the muted
      // one has finished (see ws.onclose).
      if (wanted) {
        if (!mic) void openMicrophone(); else if (!closing) beginSession();
      }
      if (paused) setStatus("paused");
    },
    micOff() {
      if (off) return;
      const wasQuiet = quiet();
      off = true;
      cancelRetry();
      if (!wasQuiet) {
        stopSending();               // released once its last frame is in
      } else {
        endMutedSession();
        closeMicrophone();
      }
      setStatus("off");
    },
    micOn() {
      if (!off) return;
      off = false;
      retryDelay = 1000;
      // A fresh microphone and, unless muted, a fresh session once it is
      // capturing.
      if (wanted) void openMicrophone();
      if (muted) setStatus("muted");
      else if (paused) setStatus("paused");
    },
    interrupt(words: InputWords) {
      interruptWith(words);
    },
  };
}
