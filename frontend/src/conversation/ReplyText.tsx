import { useLayoutEffect, useRef } from "react";
import type { Spoken } from "../captions";
import { markdown } from "../dashboard/markdown";
import { alignWords, markWords, spokenWordClass } from "../SpokenWords";

/** Reuse the app's text-node Markdown renderer: no HTML injection or new parser.
 *  While it is read aloud (`spoken`), the word being said glows as it does
 *  everywhere JARVIS speaks (SpokenWords.tsx); the whole text stays shown. */
export function ReplyText({ text, spoken = null }: { text: string; spoken?: Spoken | null }) {
  const root = useRef<HTMLDivElement>(null);
  const marked = useRef<{ spans: HTMLElement[]; said: string; at: (number | null)[] }>({ spans: [], said: "", at: [] });
  const reading = spoken !== null;
  useLayoutEffect(() => {
    const prose = markdown(text, "reply-prose");
    marked.current = { spans: reading ? markWords(prose) : [], said: "", at: [] };
    root.current?.replaceChildren(prose);
  }, [text, reading]);
  useLayoutEffect(() => {
    const now = marked.current;
    if (!spoken) return;
    const said = spoken.words.join(" ");
    if (now.said !== said) {
      now.at = alignWords(now.spans.map(span => span.textContent ?? ""), spoken.words);
      now.said = said;
    }
    // A word said and not shown keeps the mark on the last one shown.
    let word: number | null = null;
    for (let i = Math.min(spoken.word, now.at.length - 1); i >= 0 && word === null; i--) word = now.at[i];
    now.spans.forEach((span, j) => { span.className = spokenWordClass(j, word); });
  }, [text, spoken]);
  return <div ref={root} />;
}
