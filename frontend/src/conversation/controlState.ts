/**
 * The conversation control's one state model: what the message input's
 * heading row says, and with what light and buttons, worked out from the
 * stores that already know (voiceState, voiceTurn, the conversation's
 * turns, voice input's status, its health, the voice socket) and nothing
 * else. The design is the approved all-states page
 * (.agents/in-progress/conversation-control-states.html).
 *
 * Which shows, as approved:
 *   - a live state keeps the control: words he is saying, a turn not sent
 *     yet, JARVIS thinking or speaking, or two of them at once (an
 *     overlap, called out in yellow);
 *   - an error shows only when the control would otherwise be empty;
 *   - a one-off error (a reply failed or cancelled, not all read aloud,
 *     words not sent) stays until he speaks or sends again;
 *   - a lasting one (not connected, no microphone, another page listening,
 *     ...) stays while it holds and comes back after a live state;
 *   - a typed send refused wins over Reconnecting, and Reconnecting carries
 *     what the lost connection cut (the reply, else his listening) as its
 *     detail. Several other errors at once: the newest.
 *
 * Every state's words, light and buttons come from the tables below; the
 * control (ConversationControl.tsx) only draws a `ControlView`.
 */

import { conversation, type Turn } from "../captions";
import { formatDateTime, saidShown, type PlainKey, type Translate } from "../locale";
import { inputStatus } from "../mic";
import type { OrbState } from "../orb";
import { createStore } from "../store";
import type { PauseCue } from "../voiceControl";
import { causeSaid, health, type Health } from "../voiceHealth";
import type { InputStatus } from "../voiceInput";
import { voiceConnected, type VoiceTurn } from "../voiceStatus";

// ── what the control can show ─────────────────────────────────────────────

/** The activity light: dim and still with nothing to show; his voice in
 *  white, JARVIS in blue, two states in yellow, a failure in red (still,
 *  or lit one bar at a time while it is trying again by itself). */
export type Light = "rest" | "hearing" | "held" | "pause" | "stalled" | "thinking" | "speaking"
  | "overlap" | "error" | "retrying";

/** The control's buttons. */
export type Action = "send" | "sendHeard" | "retry" | "draft" | "skip" | "listenHere" | "startListening"
  | "readAgain";

/** Every button's words, title, look and kind of action, in one place. */
export const ACTIONS: Readonly<Record<Action, {
  id: string; label: PlainKey; title: PlainKey; primary?: boolean; more?: boolean;
  kind: "domain" | "speech" | "microphone" | "refresh";
}>> = {
  send: { id: "voice-turn-send", label: "main.conversation.turnSend", title: "main.conversation.turnSendTitle",
          primary: true, kind: "domain" },
  sendHeard: { id: "voice-turn-send", label: "main.conversation.turnSend",
               title: "main.conversation.turnSendStalledTitle", primary: true, kind: "domain" },
  retry: { id: "voice-turn-retry", label: "main.conversation.turnRetry", title: "main.conversation.turnRetryTitle",
           kind: "domain" },
  draft: { id: "voice-turn-draft", label: "main.conversation.turnDraft", title: "main.conversation.turnDraftTitle",
           kind: "domain" },
  skip: { id: "control-skip", label: "main.conversation.skip", title: "main.conversation.skipTitle", more: true,
          kind: "speech" },
  listenHere: { id: "control-listen", label: "left.mic.listenHere", title: "left.mic.listenHere", kind: "microphone" },
  startListening: { id: "control-listen", label: "left.mic.startListening", title: "left.mic.startListening",
                    kind: "microphone" },
  readAgain: { id: "control-read-again", label: "main.conversation.retry", title: "main.conversation.readAgainTitle",
               kind: "refresh" },
};

const SEND: readonly Action[] = ["send"];
const STALLED: readonly Action[] = ["retry", "sendHeard", "draft"];
const NONE: readonly Action[] = [];

/** Which part of the conversation the row is while it shows a state: his
 *  unsent turn ("What you said, not sent yet") or the reply's playback. */
export type Group = "turn" | "playback";
export const GROUP_LABEL: Readonly<Record<Group, PlainKey>> = {
  turn: "main.conversation.turn",
  playback: "main.conversation.playback",
};

/** What the row draws: the light, the title (and its narrow form), the
 *  detail line (an overlap's yellow lead, a note, his words) and the
 *  buttons. `id` is the state's name on the approved page. */
export interface ControlView {
  id: string;
  tone: "plain" | "overlap" | "error";
  light: Light;
  /** Lit bars of the pause light: 1, 2 or 3. */
  stage?: 1 | 2 | 3;
  title: string | null;
  /** The title under 560 px, where the long one does not fit. */
  short?: string;
  /** An overlap's second state, in yellow, before his words. */
  lead?: string;
  /** JARVIS's or an error's sentence, one line, whole in its tooltip. */
  note?: string;
  quiet?: boolean;
  /** His words, newest end kept in view. */
  words?: string;
  /** Words he is saying now, after earlier `words` still waiting. */
  heard?: string;
  actions: readonly Action[];
  group?: Group;
  pause?: PauseCue;
}

// ── the live states ───────────────────────────────────────────────────────

interface LiveSpec {
  title: PlainKey; short?: PlainKey; light: Light; stage?: 1 | 2 | 3; note?: PlainKey; quiet?: boolean;
  actions: readonly Action[]; group?: Group; error?: boolean;
}

const LIVE = {
  hearing: { title: "main.conversation.hearing", light: "hearing", actions: NONE },
  "turn-gathering": { title: "main.conversation.turnGathering", light: "held", actions: SEND, group: "turn" },
  "turn-pause-tentative": { title: "main.conversation.turnPause.tentative", light: "pause", stage: 1, actions: SEND,
                            group: "turn" },
  "turn-pause-deciding": { title: "main.conversation.turnPause.deciding", light: "pause", stage: 2, actions: SEND,
                           group: "turn" },
  "turn-pause-likely": { title: "main.conversation.turnPause.likely", light: "pause", stage: 3, actions: SEND,
                         group: "turn" },
  "turn-commit-pending": { title: "main.conversation.turnGathering", light: "held", actions: SEND, group: "turn" },
  "turn-stalled": { title: "main.conversation.turnStalled", short: "main.conversation.turnStalledShort",
                    light: "stalled", actions: STALLED, group: "turn", error: true },
  thinking: { title: "main.conversation.isThinking", note: "main.conversation.replyAbove", quiet: true,
              light: "thinking", actions: NONE, group: "playback" },
  speaking: { title: "main.conversation.isSpeaking", note: "main.conversation.followReply", light: "speaking",
              actions: ["skip"], group: "playback" },
} satisfies Record<string, LiveSpec>;

/** Two states at once: the first in the title, the second as the
 *  detail's yellow lead, his words after it. */
const OVERLAPS = {
  "overlap-reading-waiting": { first: "main.conversation.overlapReading", second: "main.conversation.overlapWaiting",
                               group: "turn" },
  "overlap-reading-hearing": { first: "main.conversation.overlapReading", second: "main.conversation.overlapHearing" },
  "overlap-hearing-earlier": { first: "main.conversation.overlapHearing", second: "main.conversation.overlapEarlier",
                               group: "turn" },
  "overlap-thinking-waiting": { first: "main.conversation.overlapThinking", second: "main.conversation.overlapWaiting",
                                group: "turn" },
} satisfies Record<string, { first: PlainKey; second: PlainKey; group?: Group }>;

/** His unsent turn's state, by what the server last said of it. */
function turnState(turn: VoiceTurn): keyof typeof LIVE {
  if (turn.state === "stalled") return "turn-stalled";
  if (turn.pause) return `turn-pause-${turn.pause}`;
  return turn.state === "commit-pending" ? "turn-commit-pending" : "turn-gathering";
}

// ── the errors ────────────────────────────────────────────────────────────

const ERRORS = {
  "err-reply-failed": { title: "main.conversation.replyFailed", light: "error" },
  "err-reply-cancelled": { title: "main.conversation.replyCancelled", light: "error" },
  "err-reading-failed": { title: "main.conversation.readingFailed", light: "error" },
  "err-reconnecting": { title: "main.conversation.reconnecting", short: "main.conversation.reconnectingShort",
                        light: "retrying" },
  "err-typed-refused": { title: "main.conversation.notConnected", light: "retrying" },
  "err-fell-behind": { title: "main.voice.dropReason.fellBehind", light: "retrying" },
  "err-too-slow": { title: "main.voice.dropReason.tooSlow", short: "main.conversation.error.tooSlowShort",
                    light: "retrying" },
  "err-backed-up": { title: "main.voice.dropReason.backedUp", light: "retrying" },
  "err-mic-disconnected": { title: "main.voice.dropReason.disconnected", light: "retrying" },
  "err-overload": { title: "main.voice.incident.overload", light: "retrying" },
  "err-decoder-failed": { title: "main.voice.incident.decoder-failed", light: "retrying" },
  "err-server-interrupted": { title: "main.voice.incident.interrupted", light: "retrying" },
  "err-invalid-format": { title: "main.conversation.error.audioUnreadable", light: "retrying" },
  "err-lost-recognition": { title: "main.conversation.error.recognitionLost", light: "retrying" },
  "err-stop-unfinished": { title: "main.voice.incident.interrupted", light: "error" },
  "err-mute-unfinished": { title: "main.voice.incident.interrupted", light: "error" },
  "err-model-unavailable": { title: "main.voice.incident.model-unavailable", light: "error" },
  "err-no-microphone": { title: "main.conversation.error.noMicrophone", light: "error" },
  "err-cannot-open": { title: "main.conversation.error.cannotOpen", light: "retrying" },
  "err-no-worklet": { title: "main.conversation.error.cannotStream", short: "main.conversation.error.cannotStreamShort",
                      light: "error" },
  "err-blocked": { title: "main.conversation.error.blocked", light: "error" },
  "err-click-to-start": { title: "left.mic.state.permission", light: "error" },
  "err-busy": { title: "main.voice.incident.busy", light: "error", actions: ["listenHere"] },
  "err-taken": { title: "main.conversation.error.listeningMoved", light: "error", actions: ["startListening"] },
  "err-settings-unread": { title: "main.conversation.error.settingsUnread", light: "error" },
  "err-no-frames": { title: "main.voice.health.no-frames", light: "error" },
  "err-history-unavailable": { title: "main.conversation.error.historyUnavailable", light: "retrying",
                               actions: ["readAgain"] },
  "err-audio-click": { title: "main.conversation.error.audioNeedsClick", light: "error" },
} satisfies Record<string, { title: PlainKey; short?: PlainKey; light: "error" | "retrying";
                             actions?: readonly Action[] }>;

export type ErrorId = keyof typeof ERRORS;

/** Voice input's words, by their message (voiceInput.ts), as the error
 *  they are. Words not here (asking, connecting, loading, lost JARVIS)
 *  are not an error of their own. */
const INPUT_ERRORS: Readonly<Record<string, ErrorId>> = {
  "left.input.fellBehind": "err-fell-behind",
  "left.input.tooSlow": "err-too-slow",
  "left.input.backedUp": "err-backed-up",
  "left.input.disconnected": "err-mic-disconnected",
  "left.input.overload": "err-overload",
  "left.input.decoderFailed": "err-decoder-failed",
  "left.input.interrupted": "err-server-interrupted",
  "left.input.invalidFormat": "err-invalid-format",
  "left.input.lostRecognition": "err-lost-recognition",
  "left.input.stopUnfinished": "err-stop-unfinished",
  "left.input.muteUnfinished": "err-mute-unfinished",
  "left.input.modelUnavailable": "err-model-unavailable",
  "left.input.noMicrophone": "err-no-microphone",
  "left.input.cannotOpen": "err-cannot-open",
  "left.input.noWorklet": "err-no-worklet",
  "left.input.workletFailed": "err-no-worklet",
  "left.input.blocked": "err-blocked",
  "left.input.clickToStart": "err-click-to-start",
  "left.input.busy": "err-busy",
  "left.input.taken": "err-taken",
  "left.input.settingsUnread": "err-settings-unread",
};

/** An error that holds now: which, its identity (a new identity is a new
 *  error, so the newest is known; it names everything its detail says, so
 *  the same identity is the same error), which comes first (0, a typed send
 *  refused; 1, Reconnecting; 2, the rest, newest first) and its detail in
 *  the language in force. */
export interface ErrorFact {
  id: ErrorId;
  key: string;
  tier: 0 | 1 | 2;
  detail: (t: Translate) => string | null;
}

/** Why a reply failed, in the interface's language (the History's note
 *  under the reply says the same). */
export function failureNote(failure: NonNullable<Turn["failure"]>, t: Translate): string {
  if (failure.reason === "rate_limited" && failure.resetsAt) {
    return t("main.conversation.replyReason.rate_limited_until",
             { time: formatDateTime(failure.resetsAt * 1000, { hour: "numeric", minute: "2-digit" }) });
  }
  return t(`main.conversation.replyReason.${failure.reason}`);
}

/** The newest of his turns, heard or typed: what clears a one-off error. */
const lastUserTurn = (turns: readonly Turn[]): number =>
  turns.reduce((n, turn) => turn.speaker === "user" && turn.id > n ? turn.id : n, 0);

/** Words not sent, kept until he speaks or sends again: voice input's
 *  interruption, and the newest of his turns when it came. */
export interface Interruption { status: InputStatus; after: number; n: number }

export interface ErrorSources {
  turns: readonly Turn[];
  connected: boolean;
  input: InputStatus;
  interruption: Interruption | null;
  health: Health | null;
  /** The n-th typed send refused, until he edits or sends. */
  refused: number | null;
  audioNeedsClick: boolean;
  historyUnavailable: boolean;
}

const baseKey = (key: string) => key.replace(/\+detail$/, "");

/** Every error that holds now. */
export function errorFacts(s: ErrorSources): ErrorFact[] {
  const facts: ErrorFact[] = [];
  const lastUser = lastUserTurn(s.turns);
  const reply = [...s.turns].reverse().find(turn => turn.speaker === "jarvis" && !turn.display);
  const unanswered = reply && reply.id > lastUser ? reply : null;
  if (s.refused !== null) facts.push({ id: "err-typed-refused", key: `typed:${s.refused}`, tier: 0,
                                       detail: t => t("main.conversation.notSentKept") });
  if (!s.connected) {
    // What the lost connection cut: the reply, else his listening.
    const input = s.input.words;
    const cut = unanswered?.interrupted ? "reply" : input?.key === "left.input.lostJarvis" ? "listening" : "";
    facts.push({ id: "err-reconnecting", key: `reconnecting:${cut}`, tier: 1,
                 detail: t => cut === "reply" ? t("main.conversation.interrupted")
                   : cut === "listening" ? input!.say(t) : null });
  } else if (unanswered?.complete && unanswered.outcome === "failed") {
    const failure = unanswered.failure;
    facts.push({ id: "err-reply-failed", key: `failed:${unanswered.id}`, tier: 2,
                 detail: t => failure ? failureNote(failure, t) : null });
  } else if (unanswered?.complete && unanswered.outcome === "cancelled") {
    facts.push({ id: "err-reply-cancelled", key: `cancelled:${unanswered.id}`, tier: 2, detail: () => null });
  } else if (unanswered?.reading === "failed") {
    facts.push({ id: "err-reading-failed", key: `reading:${unanswered.id}`, tier: 2, detail: () => null });
  }
  const held = s.interruption;
  if (held && held.status.words && lastUser <= held.after) {
    const id = INPUT_ERRORS[baseKey(held.status.words.key)] ?? "err-server-interrupted";
    const words = held.status.words;
    facts.push({ id, key: `interrupted:${held.n}`, tier: 2, detail: t => words.say(t) });
  }
  const words = s.input.words;
  const lasting = words && s.input.state !== "interrupted" ? INPUT_ERRORS[baseKey(words.key)] : undefined;
  if (words && lasting) facts.push({ id: lasting, key: `input:${s.input.message}`, tier: 2, detail: t => words.say(t) });
  if (s.health?.state === "no-frames") {
    const h = s.health;
    facts.push({ id: "err-no-frames", key: `no-frames:${h.cause ?? ""}`, tier: 2,
                 detail: () => h.cause ? saidShown(causeSaid(h)).text : null });
  }
  if (s.historyUnavailable) facts.push({ id: "err-history-unavailable", key: "history", tier: 2,
                                         detail: t => t("main.conversation.historyUnavailable") });
  if (s.audioNeedsClick) facts.push({ id: "err-audio-click", key: "audio", tier: 2,
                                      detail: t => t("main.conversation.clickToEnableAudio") });
  return facts;
}

/** The one error shown of those that hold: a typed send refused, then
 *  Reconnecting, then the newest (`since`: when each was first seen). */
export function errorInForce(facts: readonly ErrorFact[], since: (key: string) => number): ErrorFact | null {
  let best: ErrorFact | null = null;
  for (const fact of facts) {
    if (!best || fact.tier < best.tier || (fact.tier === best.tier && since(fact.key) > since(best.key))) best = fact;
  }
  return best;
}

// ── the one derivation ───────────────────────────────────────────────────

export interface ControlInput {
  state: OrbState;
  turn: VoiceTurn | null;
  /** The words of his open turn, being heard now. */
  hearing: string | null;
  error: ErrorFact | null;
}

const EMPTY: ControlView = { id: "empty", tone: "plain", light: "rest", title: null, actions: NONE };

function live(id: keyof typeof LIVE, t: Translate, words?: string): ControlView {
  const spec: LiveSpec = LIVE[id];
  return { id, tone: spec.error ? "error" : "plain", light: spec.light, stage: spec.stage, title: t(spec.title),
           short: spec.short && t(spec.short), note: spec.note && t(spec.note), quiet: spec.quiet, words,
           actions: spec.actions, group: spec.group };
}

/** What the control shows: a live state (an overlap first), else the
 *  error in force, else nothing. */
export function controlView({ state, turn, hearing, error }: ControlInput, t: Translate): ControlView {
  const unsent = turn && turn.text.trim() ? turn : null;
  const speaking = state === "speaking", thinking = state === "thinking";
  const overlap = (id: keyof typeof OVERLAPS, words: string, heard?: string): ControlView => {
    const spec: { first: PlainKey; second: PlainKey; group?: Group } = OVERLAPS[id];
    return { id, tone: "overlap", light: "overlap", title: t("main.conversation.overlapTitle", { first: t(spec.first) }),
             lead: t("main.conversation.overlapLead", { second: t(spec.second) }), words, heard,
             actions: !spec.group ? NONE : unsent?.state === "stalled" ? STALLED : SEND, group: spec.group,
             pause: unsent?.pause };
  };
  if (speaking && unsent) return overlap("overlap-reading-waiting", unsent.text);
  if (speaking && hearing) return overlap("overlap-reading-hearing", hearing);
  if (hearing && unsent) return overlap("overlap-hearing-earlier", unsent.text, hearing);
  if (thinking && unsent) return overlap("overlap-thinking-waiting", unsent.text);
  if (unsent) return { ...live(turnState(unsent), t, unsent.text), pause: unsent.pause };
  if (hearing) return live("hearing", t, hearing);
  if (speaking) return live("speaking", t);
  if (thinking) return live("thinking", t);
  if (!error) return EMPTY;
  const spec: { title: PlainKey; short?: PlainKey; light: Light; actions?: readonly Action[] } = ERRORS[error.id];
  return { id: error.id, tone: "error", light: spec.light, title: t(spec.title), short: spec.short && t(spec.short),
           note: error.detail(t) ?? undefined, actions: spec.actions ?? NONE };
}

// ── the error stores ─────────────────────────────────────────────────────

/** The n-th typed send refused (not connected), until he edits or sends. */
export const typedRefused = createStore<number | null>(null);
let refusals = 0;
export const refuseTyped = () => typedRefused.set(++refusals);
export const clearTypedRefusal = () => typedRefused.set(null);
/** The browser will not play a reply's audio until the page is clicked. */
export const audioNeedsClick = createStore(false);
/** The conversation's record could not be read; it is being read again. */
export const historyUnavailable = createStore(false);

let interruption: Interruption | null = null;
let interruptions = 0;
const firstSeen = new Map<string, number>();
let seen = 0;

/** The error shown when nothing live is. */
export const controlError = createStore<ErrorFact | null>(null);

function settle(): void {
  const facts = errorFacts({
    turns: conversation.get().turns, connected: voiceConnected.get(), input: inputStatus.get(), interruption,
    health: health.get(), refused: typedRefused.get(), audioNeedsClick: audioNeedsClick.get(),
    historyUnavailable: historyUnavailable.get(),
  });
  const keys = new Set(facts.map(fact => fact.key));
  for (const key of firstSeen.keys()) if (!keys.has(key)) firstSeen.delete(key);
  for (const key of keys) if (!firstSeen.has(key)) firstSeen.set(key, ++seen);
  const next = errorInForce(facts, key => firstSeen.get(key) ?? 0);
  // Kept as it is while it is the same error (its detail is worded when
  // drawn, in the language in force then).
  if (controlError.get()?.key !== next?.key) controlError.set(next);
}

inputStatus.subscribe(() => {
  const status = inputStatus.get();
  if (status.state === "interrupted" && status.words) {
    interruption = { status, after: lastUserTurn(conversation.get().turns), n: ++interruptions };
  }
  settle();
});
for (const store of [conversation, voiceConnected, health, typedRefused, audioNeedsClick, historyUnavailable]) {
  store.subscribe(settle);
}
