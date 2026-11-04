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
 * Nothing is heard without the listening grant: his deliberate Start
 * listening, Unmute or Mic on in this page load, never kept across a reload
 * and never taken from a click elsewhere, the browser's permission or the
 * audio being unlocked. A page loads not listening (or muted, or off, as
 * mic.ts restored), with no microphone and no recogniser socket. The grant
 * takes the recogniser from another page that has it (the server tells
 * that page, which lets go); without it a page is told busy and does not
 * hold the microphone while it waits. How a stop ends what he was saying
 * is his setting (voiceControl.ts): Keep finishes only the audio from
 * before the stop and sends its words together once finished, or offers
 * them as an unsent draft if it cannot finish; Discard throws them away,
 * with the turn the server was gathering. Leaving the page stops listening
 * only under Stop listening, and coming back never starts it again.
 *
 * Every session belongs to one capture epoch (a number this page moves on
 * at every enable, stop and loss) and carries it, with the page's client
 * id, on its start and on every word it gives; a retry runs only within
 * the grant and epoch it was scheduled in.
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
import type { Quiet } from "./mic";
import { CLIENT_ID } from "./uiClient";
import type { VoiceInputSettings } from "./voiceControl";
import { healthHeard, healthLost, takeTelemetry } from "./voiceHealth";
import workletUrl from "./pcm-worklet.ts?worker&url";

export type InputState =
  | "disarmed"      // not listening: no grant in this page load
  | "starting"      // asking for the microphone
  | "permission"    // the browser has not given us the microphone
  | "loading"       // the recogniser is loading; what is said is being held
  | "listening"
  | "paused"        // JARVIS is speaking and the mic is set to stop for it
  | "muted"         // the microphone is open and nothing is sent
  | "muted-inactive" // muted, and no microphone open in this page load
  | "finishing"     // stopped: the words from before the stop are being finished
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
  /** Start listening, on his click (the listening grant): this page takes
   *  the recogniser from any other page that has it. Refused (false) while
   *  muted or off, which have their own controls, or before the voice input
   *  settings have been read. */
  start(): boolean;
  /** The quiet preference changed: by this page (`here`: his click, or the
   *  brain asking for quiet) or by another tab. Only a change made here can
   *  start listening; one from another tab only ever stops it. */
  setQuiet(next: Quiet, here: boolean): void;
  /** JARVIS is speaking and the mic stops for it: what was being heard is
   *  discarded as echo. */
  pause(): void;
  resume(): void;
}

export interface VoiceInputHooks {
  onLevel?: (level: number) => void;
  onStatus?: (status: InputStatus) => void;
  /** Whether a final transcript can reach JARVIS now (the voice socket is up). */
  canDeliver?: () => boolean;
  /** The voice input settings in force, or null until they have been read. */
  settings?: () => VoiceInputSettings | null;
  /** Unfinished input was discarded: the server drops the turn it was
   *  gathering from this page. */
  onDiscard?: () => void;
  /** Words kept on a stop that could not be finished: never sent, offered
   *  as an unsent draft. */
  onDraft?: (text: string) => void;
}

const FRAME_MS = 1000 * (protocol.frameBytes / 2) / protocol.sampleRate;
const HOLD_FRAMES = Math.round((protocol.startupBufferSec * 1000) / FRAME_MS);
const SOCKET_LIMIT_BYTES = protocol.queueLimitSec * protocol.sampleRate * 2;
// Held audio is sent at one and a half times real time (frames per frame's
// worth of time), so a backlog drains without arriving faster than the
// recogniser can take it.
const PACE_PER_TICK = 1.5;
const RETRY_MAX_MS = 30000;
// A finishing session may wait for the recogniser to load before its held
// audio can be sent, then has the server's own finish limit to finalise it.
const FINISH_WAIT_MS = 1000 * (protocol.startupBufferSec + protocol.finishLimitSec);
// How long the worklet has to hand over its partial last frame.
const LAST_FRAME_WAIT_MS = 300;
// Connecting normally takes milliseconds; only say so if it does not.
const SAY_CONNECTING_AFTER_MS = 1000;
const METER_EVERY_MS = 100;
const TELEMETRY_EVERY_MS = 1000 * protocol.telemetryEverySec;

interface Frame { pcm: ArrayBuffer; at: number }

/** Every captured frame, as 16 kHz mono PCM16, while this page holds the
 *  listening grant, whether or not it is sent to the recogniser: muted,
 *  JARVIS hears nothing and a listener still does; without the grant, or
 *  with the mic off, there are no frames for anyone. A page package's frame
 *  listens through this (packageBridge.ts), since a frame without an origin
 *  cannot have the microphone itself. Returns the way to stop listening. */
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
  /** The capture epoch it was started in: carried on its words. */
  epoch: number;
  ready: boolean;
  closing: boolean;        // stopped: send what is held, then finish
  lastFrame: boolean;      // the worklet's partial last frame is in the outbox
  finishSent: boolean;
  closed: boolean;         // nothing it says is acted on any more
  outbox: Frame[];
  credit: number;
  finals: Set<number>;
  /** Stopped under Keep: its words are held here until it has finished,
   *  then go together, or become a draft if it cannot finish. Null: each
   *  final goes as it comes. */
  held: string[] | null;
}

export function createVoiceInput(
  onTranscript: (text: string, epoch: number) => void,
  onInterim: (text: string, epoch: number) => void,
  onMicEvent: (event: string) => void = () => {},
  hooks: VoiceInputHooks = {}
): VoiceInput {
  let armed = false;         // the listening grant: his Start, in this page load only
  let quietNow: Quiet = "none";
  let paused = false;
  let mic: Microphone | null = null;
  let micOpening = 0;        // generation of the getUserMedia call in flight
  let session: Session | null = null;
  let closing: Session | null = null;   // the stopped or muted session, finishing
  let epoch = 0;             // the capture epoch (voice control contract, section 4)
  let claim = false;         // the next session takes the recogniser from another page
  let retryDelay = 1000;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let status: InputStatus = { state: "disarmed", message: "", words: null };
  let framesCaptured = 0;    // since the last telemetry
  let framesSent = 0;

  const muted = () => quietNow === "muted";
  const off = () => quietNow === "off";
  /** Nothing is sent: muted, or the microphone is off. */
  const quiet = () => quietNow !== "none";

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

  /** The state with nothing being heard: off, muted (with the microphone
   *  open or not), or not listening. */
  function rest(words: InputWords | null = null) {
    if (off()) setStatus("off");
    else if (muted()) setStatus(mic ? "muted" : "muted-inactive");
    else setStatus("disarmed", words);
  }

  /** The words as the server's log has them: always in English. */
  const logged = (words: InputWords) =>
    words.say(((key: string, ...args: unknown[]) =>
      (translate as (lang: "en", k: string, ...a: unknown[]) => string)("en", key, ...args)) as Translate);

  // ── the microphone ────────────────────────────────────────────────────

  async function openMicrophone() {
    // Never without the grant, and never to reproduce a muted state.
    if (!armed || off() || mic || micOpening) return;
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
      if (!armed || mine !== micOpening && micOpening) return;
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
    if (mine !== micOpening || !armed || off()) {     // stopped or turned off while asking
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
    if (mine !== micOpening || !armed || off()) {
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
        epoch++;
        dropSession(inputWords("left.input.disconnected"));
        if (armed && !off()) scheduleRetry(openMicrophone);
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
        if (muted()) setStatus("muted"); else beginSession();
      });
      document.addEventListener("click", resume, { once: true });
      document.addEventListener("keydown", resume, { once: true });
      return;
    }
    if (muted()) setStatus("muted"); else beginSession();
  }

  /** The browser's recording indicator goes out at once; the worklet may
   *  still hand over the partial frame it holds. */
  function releaseTracks() {
    if (!mic) return;
    clearInterval(mic.meter);
    mic.stream.getTracks().forEach((t) => { t.onended = null; t.stop(); });
    hooks.onLevel?.(0);
  }

  function closeMicrophone() {
    micOpening = 0;
    if (!mic) return;
    releaseTracks();
    mic.node.port.onmessage = null;
    mic.ctx.close().catch(() => {});
    mic = null;
  }

  // ── the recognition session ───────────────────────────────────────────

  function beginSession() {
    if (!armed || paused || quiet() || !mic || session) return;
    const url = `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}${protocol.path}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    const s: Session = { ws, id: null, epoch, ready: false, closing: false, lastFrame: false,
                         finishSent: false, closed: false, outbox: [], credit: PACE_PER_TICK,
                         finals: new Set(), held: null };
    const claiming = claim;
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
                               format: protocol.format, diagnostics: true,
                               clientId: CLIENT_ID, captureEpoch: s.epoch, claim: claiming }));
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
        // Admitted: this page owns the recogniser now, and a retry later in
        // this grant does not take it from anyone.
        if (session === s) claim = false;
        retryDelay = 1000;
        mark("speech recognition ready");
        // An interruption stays on screen until something is heard again.
        if (session === s && status.state !== "interrupted") setStatus("listening");
        pump(s);
      } else if (type === "interim") {
        if (session === s || s.closing) onInterim(String(msg.text ?? ""), s.epoch);
      } else if (type === "final") {
        // Only the live session's words, or a stopped one finishing: a
        // session dropped as echo, discarded or interrupted never submits
        // anything.
        if (session !== s && !s.closing) return;
        const segment = Number(msg.segment);
        if (s.finals.has(segment)) return;
        s.finals.add(segment);
        const text = String(msg.text ?? "").trim();
        if (!text) return;
        if (s.held) { s.held.push(text); return; }
        deliver(s, text);
      } else if (type === "finished") {
        const held = s.held;
        s.held = null;
        held?.forEach((text) => deliver(s, text));
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
        if (armed) beginSession();
        else if (status.state === "finishing") rest();
        keptAsDraft(s);       // closed before it finished
      }
      if (session !== s) return;
      session = null;
      if (!armed || paused || quiet()) return;
      // The server went away mid-session (a restart): what was in flight is lost.
      epoch++;
      if (s.ready) {
        setStatus("interrupted", inputWords("left.input.lostRecognition"));
      }
      scheduleRetry(beginSession);
    };
  }

  function deliver(s: Session, text: string) {
    if (hooks.canDeliver && !hooks.canDeliver()) {
      interruptWith(inputWords("left.input.lostJarvis"));
      return;
    }
    onTranscript(text, s.epoch);
    if (session === s && status.state === "interrupted") setStatus("listening");
  }

  /** A session stopped under Keep that ended without finishing: what it
   *  heard is never sent, and becomes an unsent draft. */
  function keptAsDraft(s: Session) {
    const held = s.held;
    s.held = null;
    if (!held?.length) return;
    mark("stopped session could not finish; its words are a draft");
    hooks.onDraft?.(held.join(" "));
    setStatus("interrupted", inputWords("left.input.stopUnfinished"));
  }

  function onServerError(s: Session, code: string, message: string) {
    mark(`speech recognition error: ${code}${message ? ` (${message})` : ""}`);
    s.closed = true;
    const live = session === s;
    if (live) session = null;
    if (closing === s) {
      closing = null;
      if (armed) beginSession(); else if (status.state === "finishing") rest();
    }
    s.ws.close();
    if (!live) {
      keptAsDraft(s);
      return;
    }
    if (code === "taken") {
      // He started listening on another page: this one lets go, as if he
      // had stopped it here, and does not take it back by itself.
      revoke();
      if (hooks.settings?.()?.stop !== "keep") hooks.onDiscard?.();
      rest(inputWords("left.input.taken"));
      return;
    }
    if (code === "busy") {
      // Another page is listening. Not taken from it without his Start
      // here, and the microphone is not held while waiting.
      revoke();
      setStatus("busy", inputWords("left.input.busy"));
      return;
    }
    epoch++;
    // Said by its code; the server's own words follow as detail where they
    // say more than the code does.
    if (code === "model-unavailable") {
      setStatus("unavailable", withDetail(inputWords("left.input.modelUnavailable"), message));
    } else {
      setStatus("interrupted", withDetail(SERVER_ERRORS[code] ?? inputWords("left.input.interrupted"),
                                          code === "overload" ? "" : message));
    }
    if (armed && !paused && !quiet()) scheduleRetry(beginSession);
  }

  function onFrame(pcm: ArrayBuffer) {
    // Without the grant nothing is heard by anyone; a session stopped under
    // Keep is given only what the worklet still holds from before the stop.
    if (!armed) {
      if (closing && !closing.lastFrame && closing.held) { closing.outbox.push({ pcm, at: performance.now() }); pump(closing); }
      return;
    }
    framesCaptured++;
    frameListeners.forEach((listener) => listener(pcm.slice(0)));
    // While quiet, only the frames the worklet flushes for the muted session.
    const s = quiet() ? (closing && !closing.lastFrame ? closing : null) : session;
    if (!s || (paused && !quiet())) return;
    s.outbox.push({ pcm, at: performance.now() });
    if (s === session && s.outbox.length > HOLD_FRAMES) {
      epoch++;
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
    if (off() || !armed) closeMicrophone();
  }

  function pump(s: Session) {
    if (!s.ready || s.ws.readyState !== WebSocket.OPEN) return;
    while (s.outbox.length && s.credit >= 1) {
      if (s.ws.bufferedAmount > SOCKET_LIMIT_BYTES) {
        epoch++;
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

  /** Discard a session's audio without sending it. */
  function abortSession(s: Session) {
    s.closed = true;
    s.held = null;
    try {
      if (s.ws.readyState === WebSocket.OPEN) s.ws.send(JSON.stringify({ type: "abort" }));
      s.ws.close();
    } catch { /* closing anyway */ }
  }

  /** Discard the live session's audio without sending it. */
  function dropSession(words?: InputWords) {
    const s = session;
    session = null;
    if (s) abortSession(s);
    if (words) {
      mark(`input interrupted: ${logged(words)}`);
      setStatus("interrupted", words);
    }
  }

  /** Stop taking audio, ask the worklet for its partial last frame, and
   *  finish the session with everything held, even if the recogniser is
   *  still loading (it is sent once ready). `hold`: its words wait for the
   *  finish and go together, or become an unsent draft if it cannot
   *  finish (a stop under Keep); otherwise each goes as it comes (mute). */
  function finishSession(hold: boolean) {
    const s = session;
    session = null;
    if (s) {
      s.closing = true;
      if (hold) s.held = [];
      closing = s;
      setTimeout(() => {
        if (s.closed) return;
        const keeping = s.held !== null;
        s.closed = true;
        if (closing === s) closing = null;
        try { s.ws.close(); } catch { /* closing anyway */ }
        if (!keeping) {
          mark("muted session could not finish");
          setStatus("interrupted", inputWords("left.input.muteUnfinished"));
          return;
        }
        rest();
        keptAsDraft(s);
      }, FINISH_WAIT_MS);
    }
    if (mic) {
      mic.node.port.postMessage({ type: "flush" });
      setTimeout(() => onLastFrame(new ArrayBuffer(0)), LAST_FRAME_WAIT_MS);
    } else {
      onLastFrame(new ArrayBuffer(0));
    }
  }

  /** The finishing session keeps whatever it already has; nothing more is
   *  flushed into it. */
  function endMutedSession() {
    if (closing && !closing.lastFrame) {
      closing.lastFrame = true;
      pump(closing);
    }
  }

  function abortClosing() {
    if (closing) { abortSession(closing); closing = null; }
  }

  function interruptWith(words: InputWords) {
    epoch++;
    dropSession(words);
    if (armed && !paused && !quiet()) scheduleRetry(beginSession);
  }

  /** The grant ends: nothing is taken or sent from here until his next
   *  Start, and the microphone is released. */
  function revoke() {
    armed = false;
    claim = false;
    epoch++;
    cancelRetry();
    dropSession();
    closeMicrophone();
  }

  /** Stop listening, by his Mic off or by leaving the page under Stop
   *  listening: the setting decides what becomes of what he was saying.
   *  Keep finishes only the audio from before the stop, releasing the
   *  microphone at once; Discard throws away the live and finishing
   *  sessions and the turn the server is gathering. Either way nothing
   *  more is heard until he enables listening again. */
  function stopListening() {
    const keep = hooks.settings?.()?.stop === "keep";
    armed = false;
    claim = false;
    epoch++;
    cancelRetry();
    if (keep && session) {
      finishSession(true);
      releaseTracks();
      setStatus("finishing");
      return;
    }
    dropSession();
    if (!keep) {
      abortClosing();
      hooks.onDiscard?.();
    }
    closeMicrophone();
    rest();
  }

  function scheduleRetry(what: () => void) {
    if (retryTimer) return;
    const delay = retryDelay;
    const at = epoch;
    retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      // Only within the same grant and epoch: a retry never arms a page.
      if (armed && epoch === at) what();
    }, delay);
  }

  function cancelRetry() {
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }
  }

  /** Arm: his Start, Unmute or Mic on on this page. */
  function arm(): boolean {
    if (!hooks.settings?.()) {
      rest(inputWords("left.input.settingsUnread"));
      return false;
    }
    armed = true;
    claim = true;
    epoch++;
    retryDelay = 1000;
    cancelRetry();
    return true;
  }

  // Leaving the page: Stop listening ends a listening session; Continue
  // keeps one he started. Coming back never starts one.
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "hidden" || !armed || quiet()) return;
      if (hooks.settings?.()?.switch !== "stop") return;
      mark("page hidden: listening stopped");
      stopListening();
    });
  }

  return {
    start() {
      if (quiet()) return false;
      if (!arm()) return false;
      if (mic) beginSession(); else void openMicrophone();
      return true;
    },
    setQuiet(next: Quiet, here: boolean) {
      const was = quietNow;
      if (next === was) return;
      if (next === "off") {
        if (was === "none" && armed) {
          quietNow = next;          // the rest reads as Mic off
          stopListening();
        } else {
          quietNow = next;
          endMutedSession();
          armed = false;
          claim = false;
          epoch++;
          cancelRetry();
          closeMicrophone();
          rest();
        }
        return;
      }
      if (next === "muted") {
        quietNow = next;
        if (was === "none" && armed) {
          // Mute: the words already spoken are finished and sent; the
          // microphone stays open.
          cancelRetry();
          finishSession(false);
          hooks.onLevel?.(0);
        }
        rest();
        return;
      }
      // Unmute, or Mic on.
      quietNow = next;
      if (!here) {
        // Another tab's: only ever quieter here, so this page lets go.
        endMutedSession();
        revoke();
        rest();
        return;
      }
      endMutedSession();
      if (!arm()) { closeMicrophone(); return; }
      if (!mic) void openMicrophone();
      else if (!closing) beginSession();       // else once the muted one has finished
      if (paused) setStatus("paused");
    },
    pause() {
      if (paused) return;
      paused = true;
      dropSession();
      if (armed && !quiet()) setStatus("paused");
    },
    resume() {
      if (!paused) return;
      paused = false;
      if (!armed || quiet()) return;
      if (mic) beginSession(); else void openMicrophone();
    },
  };
}
