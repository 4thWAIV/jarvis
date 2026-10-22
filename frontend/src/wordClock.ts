/**
 * Which word of a sentence is being spoken now, estimated: the audio for the
 * sentence says how long it lasts, and that time is shared between its words
 * by their length. There is one voice, so there is one clock: starting a
 * sentence replaces the one before. The captions use it for the glowing word,
 * and the capture box for the line being read back.
 */

/** The words of a sentence, as everything that marks a spoken word splits them. */
export function words(sentence: string): string[] {
  return sentence.split(/\s+/).filter(Boolean);
}

function wordEnds(sentence: string): number[] {
  const lengths = words(sentence).map((w) => w.length + 1);
  const total = lengths.reduce((a, b) => a + b, 0) || 1;
  let sum = 0;
  return lengths.map((n) => (sum += n) / total);
}

let clock: { start: number; seconds: number; ends: number[]; word: number;
             onWord: (word: number) => void } | null = null;
let ticking = false;

function tick(): void {
  if (!clock) { ticking = false; return; }
  const done = (performance.now() - clock.start) / 1000 / Math.max(clock.seconds, 0.001);
  let word = clock.ends.findIndex((end) => done < end);
  if (word < 0) word = clock.ends.length - 1;
  if (word !== clock.word) {
    clock.word = word;
    clock.onWord(word);
  }
  requestAnimationFrame(tick);
}

/** A sentence lasting `seconds` has started playing: `onWord` hears 0 now,
 *  then each word as it is reached. */
export function speakWords(sentence: string, seconds: number, onWord: (word: number) => void): void {
  clock = { start: performance.now(), seconds, ends: wordEnds(sentence), word: 0, onWord };
  onWord(0);
  if (!ticking) { ticking = true; requestAnimationFrame(tick); }
}

/** Nothing is being spoken any more. */
export function stopWords(): void {
  clock = null;
}
