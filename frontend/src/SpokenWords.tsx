/**
 * Words JARVIS is saying, with the word being spoken now glowing and the one
 * before it still fading out. This is the one way the page marks a spoken
 * word: the captions, a reply in the Conversation view while it is read
 * aloud, and a capture being read back in its box all draw it with this, so
 * they look and move the same. Which word is being spoken comes from
 * wordClock.ts.
 */

import { words } from "./wordClock";

/** How word `j` looks while word `word` is spoken (null: none is). */
export function spokenWordClass(j: number, word: number | null): string {
  return `spoken-word${word === null ? "" : j === word ? " now" : j === word - 1 ? " fading" : ""}`;
}

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
        <span key={j} className={spokenWordClass(j, word)}>
          {(j > 0 || lead) ? " " : ""}{w}
        </span>
      ))}
    </span>
  );
}

/** Text already drawn under `root` (a reply the Markdown renderer drew),
 *  each of its words put in a span to be given `spokenWordClass`, as
 *  SpokenWords draws them. Text nodes only, never markup. Returns the
 *  spans in reading order. */
export function markWords(root: Element): HTMLElement[] {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const texts: Text[] = [];
  while (walker.nextNode()) texts.push(walker.currentNode as Text);
  const spans: HTMLElement[] = [];
  for (const node of texts) {
    if (!node.data.trim()) continue;
    const parts = document.createDocumentFragment();
    for (const part of node.data.split(/(\s+)/)) {
      if (!part) continue;
      if (!part.trim()) { parts.append(part); continue; }
      const span = document.createElement("span");
      span.className = spokenWordClass(spans.length, null);
      span.textContent = part;
      spans.push(span);
      parts.append(span);
    }
    node.replaceWith(parts);
  }
  return spans;
}

const letters = (word: string) => word.toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

// How far ahead a spoken word is looked for among the shown ones: past a
// code block or an address that was shown and not said.
const LOOK_AHEAD = 120;

/** Which shown word each spoken word is, or null for one not shown. They
 *  are matched by their letters, in order: what is shown and not said (code,
 *  addresses, Markdown) is passed over, and a match further on than the
 *  next few words counts only when the word after it matches too, so a
 *  common word said and not shown does not carry the mark ahead. */
export function alignWords(shown: string[], spoken: string[]): (number | null)[] {
  const seen = shown.map(letters);
  const said = spoken.map(letters);
  const at: (number | null)[] = [];
  let next = 0;
  said.forEach((word, i) => {
    if (!word) { at.push(null); return; }
    for (let k = next; k < Math.min(seen.length, next + LOOK_AHEAD); k++) {
      if (seen[k] !== word) continue;
      const after = said[i + 1];
      if (k - next > 3 && after && seen[k + 1] !== undefined && seen[k + 1] !== after) continue;
      at.push(k);
      next = k + 1;
      return;
    }
    at.push(null);
  });
  return at;
}
