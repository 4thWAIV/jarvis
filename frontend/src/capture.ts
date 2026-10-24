/**
 * The open captures: Tim's words, restated, waiting to be sent to a session.
 * Several can be open at once. The server sends a `capture` message for one
 * when it opens or changes ("capturing"), while it is read back ("reading"),
 * while it is sent ("transmitting"), and when it is gone (state null); the
 * capture boxes (CaptureBox.tsx) draw them. A capture's words came from user
 * turns in the conversation (`heard`), which are then drawn as a collapsed
 * item instead.
 *
 * While one is read back, the audio chunks that read it carry its number, and
 * `captureSpeaking` follows the word being read so the box can light its line.
 */

import { claimTurns } from "./captions";
import { createStore } from "./store";
import { speakWords, stopWords, words } from "./wordClock";

export type CaptureState = "capturing" | "reading" | "transmitting";

export interface Capture {
  id: number;
  state: CaptureState;
  text: string;
  /** The session it is for; empty until the brain knows. A capture with one
   *  is a message for an agent, which is a transmission: amber. */
  to: string;
  /** The ids of the user turns it was made from. */
  heard: number[];
  /** Read back to him in full, or accepted on screen, since it last changed. */
  accepted: boolean;
}

export interface CaptureMessage {
  type: "capture";
  id: number;
  state: CaptureState | null;
  text?: string;
  to?: string;
  heard?: number[];
  accepted?: boolean;
}

/** In the order they opened. */
export const captures = createStore<Capture[]>([]);

/** The word of a capture being read back now, counted across its text. */
export const reading = createStore<{ id: number; word: number } | null>(null);

// Words of the capture already read in earlier chunks of this read-back.
let readSoFar = 0;

// While the page is reading the server's list, the captures a live message
// changed meanwhile: those messages are newer than the list.
let changedWhileLoading: Set<number> | null = null;

export function captureChanged(msg: CaptureMessage): void {
  changedWhileLoading?.add(msg.id);
  const list = captures.get();
  const old = list.find((c) => c.id === msg.id);
  if (!msg.state) {
    if (old) captures.set(list.filter((c) => c.id !== msg.id));
    if (reading.get()?.id === msg.id) reading.set(null);
    return;
  }
  if (msg.state === "reading" && old?.state !== "reading") readSoFar = 0;
  if (msg.state !== "reading" && reading.get()?.id === msg.id) reading.set(null);
  const next: Capture = {
    id: msg.id,
    state: msg.state,
    text: msg.text ?? old?.text ?? "",
    to: msg.to ?? old?.to ?? "",
    heard: msg.heard ?? old?.heard ?? [],
    accepted: msg.accepted ?? old?.accepted ?? false,
  };
  captures.set(old ? list.map((c) => (c.id === msg.id ? next : c)) : [...list, next]);
  if (next.heard.length) claimTurns(next.heard, next.id, next.to, next.text);
}

/** What a capture does, which decides how it is named and drawn. A capture
 *  for an agent is a transmission; notes, files and other kinds will have
 *  their own. Null until the brain has said what it is for. */
export type CaptureKind = "transmission";

export function captureKind(capture: { to: string }): CaptureKind | null {
  return capture.to ? "transmission" : null;
}

/** How the conversation names a capture of each kind. */
export const KIND_LABEL: Record<CaptureKind, (capture: { to: string }) => string> = {
  transmission: (c) => `Transmission to ${c.to}`,
};

/** The capture box's buttons. Read reads it aloud, which, heard to the end,
 *  counts as read back; Accept counts as his having read it; Send accepts it
 *  and sends it to the agent it is addressed to. Resolves to null, or to why
 *  it was refused, for the box to show. The server's `capture` messages
 *  change the box itself. */
export type CaptureAction = "read" | "accept" | "delete" | "send";

export function captureAction(id: number, action: CaptureAction): Promise<string | null> {
  return fetch(`/api/capture/${id}/${action}`, { method: "POST" })
    .then(async (r) => {
      if (r.ok) return null;
      const body = await r.json().catch(() => null);
      return typeof body?.detail === "string" ? body.detail : `It could not ${action} (${r.status}).`;
    })
    .catch(() => `It could not ${action}: JARVIS is not answering.`);
}

/** A chunk reading capture `id` has started playing and lasts `seconds`. */
export function captureSpeaking(id: number, text: string, seconds: number): void {
  const offset = readSoFar;
  readSoFar += words(text).length;
  speakWords(text, seconds, (word) => reading.set({ id, word: offset + word }));
}

/** Nothing is being read any more. */
export function captureQuiet(): void {
  if (reading.get()) {
    stopWords();
    reading.set(null);
  }
}

/** The captures open on the server, for a page that has just (re)connected.
 *  They replace whatever the page held: a capture the server no longer has
 *  (sent while the page was away, or lost to a restart) is gone. One a live
 *  message changed while the list was on its way is left as that message
 *  made it. */
export function loadCapture(): void {
  const live = new Set<number>();
  changedWhileLoading = live;
  fetch("/api/capture").then((r) => (r.ok ? r.json() : null))
    .then((body: { captures?: CaptureMessage[] } | null) => {
      if (changedWhileLoading === live) changedWhileLoading = null;
      if (!body || !Array.isArray(body.captures)) return;
      captures.set(captures.get().filter((c) => live.has(c.id)));
      if (reading.get() && !live.has(reading.get()!.id)) reading.set(null);
      for (const c of body.captures) if (!live.has(c.id)) captureChanged(c);
    }).catch(() => {
      if (changedWhileLoading === live) changedWhileLoading = null;
    });
}
