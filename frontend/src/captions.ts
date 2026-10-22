/**
 * The conversation the centre views draw (the Main view's caption box, and
 * the Conversation view's history, blue box and white box): the user's words
 * as the speech recognition hears them, and JARVIS's words sentence by
 * sentence as they are spoken, with the WORD being spoken now marked so it
 * can glow (wordClock.ts). User turns the brain made a capture of are marked
 * with it, and drawn as a collapsed item. jarvis.ts calls the functions
 * below; the views only read `conversation`.
 */

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

/** What the recogniser hears. Interim text keeps replacing the turn being
 *  heard; a final result closes it, so the next words start a new turn.
 *  Returns the turn's id, which the page sends with a final transcript so a
 *  capture made from it can name it. */
export function userHeard(text: string, final: boolean): number {
  const { turns, speaking } = conversation.get();
  const last = turns[turns.length - 1];
  const turn: Turn = last && last.speaker === "user" && last.open
    ? { ...last, sentences: [text], open: !final }
    : { id: nextId++, speaker: "user", sentences: [text], open: !final };
  const rest = last && last.speaker === "user" && last.open ? turns.slice(0, -1) : turns;
  commit([...rest, turn], speaking);
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
  const last = turns[turns.length - 1];
  let turn: Turn;
  let next: Turn[];
  if (last && last.speaker === "jarvis" && last.utt === utt) {
    turn = { ...last, sentences: [...last.sentences, text] };
    next = [...turns.slice(0, -1), turn];
  } else {
    const closed = last && last.open ? [...turns.slice(0, -1), { ...last, open: false }] : turns;
    turn = { id: nextId++, speaker: "jarvis", sentences: [text], open: false, utt };
    next = [...closed, turn];
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
