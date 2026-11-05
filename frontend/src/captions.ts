/**
 * The conversation the centre views draw (the Main view's caption box, and
 * the Conversation view's history, blue box and white box): the user's words
 * as the speech recognition hears them, and JARVIS's words sentence by
 * sentence as they are spoken, with the WORD being spoken now marked so it
 * can glow (wordClock.ts). User turns the brain made a capture of are marked
 * with it, and drawn as a collapsed item. jarvis.ts calls the functions
 * below; the views only read `conversation`.
 *
 * It also gives each live turn its identity for pointing at (`turnKey`),
 * and keeps which of the conversation's records are open (a long turn, an
 * agent's answer, a capture or sources item), changed only by the
 * `conversation.record.expand` command.
 */

import { useEffect, useSyncExternalStore } from "react";
import type { Display } from "./sources";
import { createStore } from "./store";
import { registerCommand } from "./uiRegistry";
import { speakWords, stopWords } from "./wordClock";

export type Speaker = "user" | "jarvis";

export interface Turn {
  id: number;
  speaker: Speaker;
  sentences: string[];
  /** Full readable reply, independent of the sentences played aloud. */
  text?: string;
  complete?: boolean;
  interrupted?: boolean;
  epoch?: number;
  replyAt?: number;
  /** A user turn still being heard: the next thing heard replaces its text. */
  open: boolean;
  /** Typed submissions belong in history, never in the microphone caption. */
  typed?: boolean;
  /** When it began, in seconds: where it falls among the conversation's
   *  record (conversation.ts), which is in the server's seconds. */
  at?: number;
  /** For a JARVIS turn, the utterance its sentences belong to. */
  utt?: number;
  /** For a user turn the brain captured: which capture, who it is for, and
   *  its restated text as last seen (the text sent, once it has gone). */
  capture?: { id: number; to: string; text: string };
  /** What the brain showed instead of saying it, where it was said: a
   *  turn of its own with no words, drawn as a collapsed Sources item. */
  display?: Display;
}

export interface Conversation {
  turns: Turn[];
  /** The word being spoken now: turn id, sentence index, word index. */
  speaking: [number, number, number] | null;
}

// Enough to scroll back a little; older turns are dropped.
const KEEP_TURNS = 40;

export const conversation = createStore<Conversation>({ turns: [], speaking: null });

let nextId = 1;
let captionEpoch = 0;
export function jarvisReconnected(): void {
  for (const utt of pending.keys()) flush(utt);
  captionEpoch++;
}
export function jarvisDisconnected(): void {
  jarvisQuiet();
  const { turns } = conversation.get();
  commit(turns.map(t => t.complete === false ? { ...t, interrupted: true } : t), null);
}

/** A live turn's identity among the conversation's records, for pointing
 *  at it: `t<id>`, the id this module gave it, kept for the life of the
 *  page (the record's own items are `r<id>`, conversation.ts). Never its
 *  words or its place in the list. A turn the page has dropped, or one from
 *  before a reload, has no live key: it is found as a record. */
export const turnKey = (turn: Pick<Turn, "id">): string => `t${turn.id}`;

// ── which records are open ──────────────────────────────────────────────
//
// A long turn, a message with its agent's answer, a capture item and a
// sources item open and close; which are open is kept here, by record key
// (`turnKey`, or the record's `r<id>`), and changed only by the
// `conversation.record.expand` command, so a click and the brain open the
// same thing the same way. The same record open in the history and in its
// agent's thread is open in both.

const openRecords = createStore<ReadonlySet<string>>(new Set());
/** How many shown records of each key can open now, counted as they mount. */
const openable = new Map<string, number>();

/** Whether record `key` is open. `opens` says this one can (a short turn
 *  cannot); while it can and is mounted, the command is offered for it. */
export function useRecordOpen(key: string, opens: boolean): boolean {
  const open = useSyncExternalStore(openRecords.subscribe, () => openRecords.get().has(key));
  useEffect(() => {
    if (!opens) return;
    openable.set(key, (openable.get(key) ?? 0) + 1);
    return () => {
      const left = (openable.get(key) ?? 1) - 1;
      if (left) openable.set(key, left); else openable.delete(key);
    };
  }, [key, opens]);
  return open;
}

registerCommand("conversation.record.expand", {
  availability: ({ recordId }) => openable.has(recordId) ? { available: true } : { available: false, reason: "no-entity" },
  execute({ recordId, open }) {
    const now = new Set(openRecords.get());
    if (open) now.add(recordId); else now.delete(recordId);
    openRecords.set(now);
    return { status: "completed", effects: [] };
  },
});

function commit(turns: Turn[], speaking: [number, number, number] | null): void {
  conversation.set({ turns: turns.slice(-KEEP_TURNS), speaking });
}

/** The last turn with words, and the turns after it (only shown items).
 *  Something shown never splits a turn still being heard or spoken. */
function lastWords(turns: Turn[]): { last: Turn | undefined; at: number } {
  let at = turns.length - 1;
  while (at >= 0 && turns[at].display) at--;
  return { last: turns[at], at };
}

// Shown items whose reply has not been heard yet, by the reply's utterance.
// One whose reply never says a word goes in after this long anyway.
const pending = new Map<number, Display[]>();
const PENDING_MS = 20000;

function insert(turns: Turn[], displays: Display[], after: number): Turn[] {
  const fresh = displays.filter((d) => !turns.some((t) => t.display?.id === d.id))
    .map((display): Turn => ({ id: nextId++, at: Date.now() / 1000, speaker: "jarvis", sentences: [], open: false, display }));
  let at = after + 1;
  while (at < turns.length && turns[at].display) at++;   // after those already shown with it
  return [...turns.slice(0, at), ...fresh, ...turns.slice(at)];
}

function flush(utt: number): void {
  const waiting = pending.get(utt);
  if (!waiting) return;
  pending.delete(utt);
  const { turns, speaking } = conversation.get();
  commit(insert(turns, waiting, turns.length - 1), speaking);
}

/** What the brain showed instead of saying it joins the conversation once,
 *  after the words of the reply it goes with; until those are heard it
 *  waits for them. */
export function shown(display: Display): void {
  const { turns, speaking } = conversation.get();
  if (turns.some((t) => t.display?.id === display.id)) return;
  const utt = display.utt;
  if (utt == null) {
    commit(insert(turns, [display], turns.length - 1), speaking);
    return;
  }
  const reply = turns.map((t) => t.epoch === captionEpoch ? t.utt : undefined).lastIndexOf(utt);
  if (reply >= 0) {
    commit(insert(turns, [display], reply), speaking);
    return;
  }
  if (!pending.has(utt)) setTimeout(() => flush(utt), PENDING_MS);
  pending.set(utt, [...(pending.get(utt) ?? []), display]);
}

/** What the recogniser hears. Interim text keeps replacing the turn being
 *  heard; a final result closes it, so the next words start a new turn.
 *  Returns the turn's id, which the page sends with a final transcript so a
 *  capture made from it can name it. */
export function userHeard(text: string, final: boolean): number {
  const { turns, speaking } = conversation.get();
  const { last, at } = lastWords(turns);
  if (last && last.speaker === "user" && last.open) {
    const turn = { ...last, sentences: [text], open: !final };
    commit(turns.map((t, i) => (i === at ? turn : t)), speaking);
    return turn.id;
  }
  const turn: Turn = { id: nextId++, at: Date.now() / 1000, speaker: "user", sentences: [text], open: !final };
  commit([...turns, turn], speaking);
  return turn.id;
}

/** What the user typed instead of saying it: a finished turn of its own.
 *  A turn still being heard stops there, so the next words heard start a
 *  new turn after this one. Returns the turn's id, as `userHeard` does. */
export function userTyped(text: string): number {
  const { turns, speaking } = conversation.get();
  const turn: Turn = { id: nextId++, at: Date.now() / 1000, speaker: "user", sentences: [text], open: false, typed: true };
  commit([...turns.map((t) => (t.speaker === "user" && t.open ? { ...t, open: false } : t)), turn],
         speaking);
  return turn.id;
}

/** The user turns a capture was made from: marked with it, so they are drawn
 *  as a collapsed item instead of as words. */
export function claimTurns(ids: number[], capture: number, to: string, text: string): void {
  const { turns, speaking } = conversation.get();
  if (!turns.some((t) => ids.includes(t.id) && t.speaker === "user")) return;
  commit(turns.map((t) => (ids.includes(t.id) && t.speaker === "user"
    ? { ...t, capture: { id: capture, to, text } } : t)), speaking);
}

/** The words of those user turns, as heard. */
export function heardText(ids: number[]): string {
  return conversation.get().turns
    .filter((t) => ids.includes(t.id) && t.speaker === "user")
    .map((t) => t.sentences.join(" ")).join(" ");
}

/** A sentence of JARVIS's has started playing and lasts `seconds`.
 *  Sentences of one utterance join one turn; a new utterance starts a new
 *  one. */
export function jarvisSpeaking(utt: number, text: string, seconds: number, idx?: number): void {
  const { turns } = conversation.get();
  const at = turns.findIndex(t => t.speaker === "jarvis" && t.utt === utt && t.epoch === captionEpoch);
  const existing = turns[at];
  const sentence = idx ?? existing?.sentences.length ?? 0;
  const sentences = [...(existing?.sentences ?? [])];
  while (sentences.length < sentence) sentences.push("");
  sentences[sentence] = text;
  const turn: Turn = existing ? { ...existing, sentences } : {
    id: nextId++, at: Date.now() / 1000, speaker: "jarvis", sentences, open: false, utt, epoch: captionEpoch,
  };
  commit(existing ? turns.map((t, i) => i === at ? turn : t) : [...turns, turn], [turn.id, sentence, 0]);
  flush(utt);
  speakWords(text, seconds, word => {
    const now = conversation.get();
    if (now.speaking?.[0] === turn.id && now.speaking[1] === sentence) {
      commit(now.turns, [turn.id, sentence, word]);
    }
  });
}

/** Readable provider text is independent of the audio playback clock. */
export function jarvisReply(utt: number, text: string, done: boolean, at?: number): void {
  const { turns, speaking } = conversation.get();
  const index = turns.findIndex(t => t.speaker === "jarvis" && t.utt === utt && (
    (at != null && t.replyAt === at)
    || (t.epoch === captionEpoch && (t.replyAt == null || at == null || t.replyAt === at))));
  const existing = turns[index];
  const turn: Turn = existing ? { ...existing, text, complete: done, interrupted: false, epoch: captionEpoch, ...(at != null ? { at, replyAt: at } : {}) } : {
    id: nextId++, at: at ?? Date.now() / 1000, speaker: "jarvis", sentences: [], text,
    complete: done, open: false, utt, epoch: captionEpoch, replyAt: at,
  };
  commit(existing ? turns.map((t, i) => i === index ? turn : t) : [...turns, turn], speaking);
  flush(utt);
}

/** Compatibility for unvoiced chunks and older servers. */
export function jarvisText(utt: number, text: string, idx?: number): void {
  const { turns, speaking } = conversation.get();
  const at = turns.findIndex(t => t.speaker === "jarvis" && t.utt === utt && t.epoch === captionEpoch);
  const existing = turns[at];
  const sentences = [...(existing?.sentences ?? [])];
  const sentence = idx ?? sentences.length;
  while (sentences.length < sentence) sentences.push("");
  sentences[sentence] = text;
  const turn: Turn = existing ? { ...existing, sentences } : {
    id: nextId++, at: Date.now() / 1000, speaker: "jarvis", sentences, open: false, utt, epoch: captionEpoch,
  };
  commit(existing ? turns.map((t, i) => i === at ? turn : t) : [...turns, turn], speaking);
  flush(utt);
}

/** Nothing is being spoken any more. */
export function jarvisQuiet(): void {
  stopWords();
  const { turns, speaking } = conversation.get();
  if (speaking) commit(turns, null);
}
