/**
 * The conversation, centred above the footer between the two side columns:
 * always four lines tall, left-aligned, the user's words and JARVIS's in
 * turn. The word JARVIS is speaking glows, and the one before it fades, so a
 * reader can follow the voice. It scrolls itself so what is being said now
 * is always in view.
 */

import { useLayoutEffect, useRef, useSyncExternalStore } from "react";
import { conversation, type Turn } from "./captions";
import { CaptureChip } from "./CaptureChip";
import { SourcesChip } from "./SourcesChip";
import { SpokenWords } from "./SpokenWords";

export function CaptionBox() {
  const { turns, speaking } = useSyncExternalStore(conversation.subscribe, conversation.get);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Follow the conversation: after every change, the newest words are in view.
  useLayoutEffect(() => {
    const scroll = scrollRef.current;
    if (scroll) scroll.scrollTop = scroll.scrollHeight;
  }, [turns, speaking]);

  return (
    // The padding is on the box and the scrolling inside it, so the four
    // lines are exactly what shows: no sliver of the line above in the gap.
    <div id="caption-box" aria-live="polite">
      <div className="caption-scroll" ref={scrollRef}>
      {turns.map((turn) => (
        <p key={turn.id} className={`caption-turn ${turn.speaker}`}>
          <TurnWords turn={turn} speaking={speaking} />
        </p>
      ))}
      </div>
    </div>
  );
}

/** A turn's words: its sentences, with the word being spoken glowing, or the
 *  collapsed item for a turn the brain made a capture of, or for what it
 *  showed instead of saying. */
export function TurnWords({ turn, speaking }: { turn: Turn;
                            speaking: [number, number, number] | null }) {
  if (turn.capture) return <CaptureChip turn={turn} />;
  if (turn.display) return <SourcesChip display={turn.display} />;
  return (
    <>
      {turn.sentences.map((sentence, i) =>
        speaking && speaking[0] === turn.id && speaking[1] === i
          ? <SpokenWords key={i} text={sentence} word={speaking[2]} lead={i > 0} />
          : <span key={i}>{i > 0 ? " " : ""}{sentence}</span>)}
    </>
  );
}

