/**
 * Words JARVIS is saying, with the word being spoken now glowing and the one
 * before it still fading out. This is the one way the page marks a spoken
 * word: the captions, the Conversation view's blue box, and a capture being
 * read back in its box all draw it with this, so they look and move the same.
 * Which word is being spoken comes from wordClock.ts.
 */

import { words } from "./wordClock";

/** `word`: the index of the word being spoken, or null when none is. `lead`:
 *  a space before the first word, for text that follows other text. */
export function SpokenWords({ text, word, lead = false }: {
  text: string;
  word: number | null;
  lead?: boolean;
}) {
  return (
    <span>
      {words(text).map((w, j) => (
        <span key={j} className={`spoken-word${
          word === null ? "" : j === word ? " now" : j === word - 1 ? " fading" : ""}`}>
          {(j > 0 || lead) ? " " : ""}{w}
        </span>
      ))}
    </span>
  );
}
