/**
 * The conversation the centre views draw (the Main view's caption box, and
 * the Conversation view's history, blue box and white box): the user's words
 * as the speech recognition hears them, and JARVIS's words sentence by
 * sentence as they are spoken, with the WORD being spoken now marked so it
 * can glow (wordClock.ts). User turns the brain made a capture of are marked
 * with it, and drawn as a collapsed item. jarvis.ts calls the functions
 * below; the views only read `conversation`.
 */

import type { Display } from "./sources";
import { createStore } from "./store";
import { speakWords, stopWords } from "./wordClock";

export type Speaker = "user" | "jarvis";

export interface Turn {
  id: number;
  speaker: Speaker;
  sentences: string[];
  /** A user turn still being heard: the next thing heard replaces its text. */
  open: boolean;
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
    .map((display): Turn => ({ id: nextId++, speaker: "jarvis", sentences: [], open: false, display }));
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
  const reply = turns.map((t) => t.utt).lastIndexOf(utt);
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
  const turn: Turn = { id: nextId++, speaker: "user", sentences: [text], open: !final };
  commit([...turns, turn], speaking);
  return turn.id;
}

/** What the user typed instead of saying it: a finished turn of its own.
 *  A turn still being heard stops there, so the next words heard start a
 *  new turn after this one. Returns the turn's id, as `userHeard` does. */
export function userTyped(text: string): number {
  const { turns, speaking } = conversation.get();
  const turn: Turn = { id: nextId++, speaker: "user", sentences: [text], open: false };
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
export function jarvisSpeaking(utt: number, text: string, seconds: number): void {
  const { turns } = conversation.get();
  const { last, at } = lastWords(turns);
  let turn: Turn;
  let next: Turn[];
  if (last && last.speaker === "jarvis" && last.utt === utt) {
    // Its sources may already be shown after it; the words stay above them.
    turn = { ...last, sentences: [...last.sentences, text] };
    next = turns.map((t, i) => (i === at ? turn : t));
  } else {
    const closed = last && last.open ? turns.map((t, i) => (i === at ? { ...last, open: false } : t)) : turns;
    turn = { id: nextId++, speaker: "jarvis", sentences: [text], open: false, utt };
    next = [...closed, turn];
    const waiting = pending.get(utt);
    if (waiting) {
      pending.delete(utt);
      next = insert(next, waiting, next.length - 1);
    }
  }
  const sentence = turn.sentences.length - 1;
  commit(next, [turn.id, sentence, 0]);
  speakWords(text, seconds, (word) => {
    const { turns: now, speaking } = conversation.get();
    if (!speaking || speaking[0] !== turn.id || speaking[1] !== sentence || speaking[2] !== word) {
      commit(now, [turn.id, sentence, word]);
    }
  });
}

/** Nothing is being spoken any more. */
export function jarvisQuiet(): void {
  stopWords();
  const { turns, speaking } = conversation.get();
  if (speaking) commit(turns, null);
}
