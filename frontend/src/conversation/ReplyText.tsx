import { useLayoutEffect, useRef } from "react";
import { markdown } from "../dashboard/markdown";

/** Reuse the app's text-node Markdown renderer: no HTML injection or new parser. */
export function ReplyText({ text }: { text: string }) {
  const root = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { root.current?.replaceChildren(markdown(text, "reply-prose")); }, [text]);
  return <div ref={root} />;
}
