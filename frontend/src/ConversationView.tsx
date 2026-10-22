/**
 * The Conversation view of the centre area, anchored to the bottom, top to
 * bottom:
 *
 *   - the history: every finished turn (JARVIS's in blue, Tim's in white), in
 *     all the height left up to the header, scrolling. A turn longer than
 *     four lines shows its first two and an ellipsis, and opens to full size
 *     when clicked;
 *   - the open captures (CaptureBox.tsx);
 *   - the blue box: four lines, what JARVIS is saying now, scrolled so the
 *     line being spoken stays in view;
 *   - the white box, at the very bottom: what Tim is saying now, as it is
 *     heard, one line growing to four.
 *
 * What is live is in the two boxes. Tim's finished words stay in the white
 * box until the brain answers: they go to a capture if it makes one of them
 * (and the history shows a collapsed item for them), and otherwise into the
 * history when JARVIS starts to reply. JARVIS's reply moves into the history
 * once he has finished saying it.
 */

import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { conversation, type Turn } from "./captions";
import { TurnWords } from "./CaptionBox";
import { CaptureList } from "./CaptureBox";
import { captureKind } from "./capture";
import type { OrbState } from "./orb";
import { voiceState } from "./voiceStatus";

// A history turn longer than this many lines is shown as its first two.
const LONG_LINES = 4;

/** Which turns are live and which are history. */
export function placeTurns(turns: Turn[], voice: OrbState):
    { history: Turn[]; speaking: Turn | null; hearing: Turn | null } {
  const last = turns[turns.length - 1];
  const speaking = last && last.speaker === "jarvis" && voice === "speaking" ? last : null;
  const hearing = last && last.speaker === "user" && !last.capture ? last : null;
  const history = turns.filter((t) => t !== speaking && t !== hearing);
  return { history, speaking, hearing };
}

export function ConversationView() {
  const { turns, speaking: word } = useSyncExternalStore(conversation.subscribe, conversation.get);
  const voice = useSyncExternalStore(voiceState.subscribe, voiceState.get);
  const { history, speaking, hearing } = placeTurns(turns, voice);
  return (
    <div id="conversation">
      <History turns={history} />
      <CaptureList />
      <SpeakingBox turn={speaking} word={word} />
      <HearingBox turn={hearing} />
    </div>
  );
}

function History({ turns }: { turns: Turn[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const newest = turns[turns.length - 1];
  // Rolling: the newest turn is in view whenever one arrives.
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [newest?.id, newest?.sentences.length]);
  return (
    <div className="conversation-history" ref={ref} aria-label="History">
      {turns.map((t) => <HistoryTurn key={t.id} turn={t} />)}
    </div>
  );
}

/** A captured turn takes its capture kind's look in the history. */
function kindClass(turn: Turn): string {
  const kind = turn.capture ? captureKind(turn.capture) : null;
  return kind ? ` kind-${kind}` : "";
}

function HistoryTurn({ turn }: { turn: Turn }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [long, setLong] = useState(false);
  const [open, setOpen] = useState(false);
  // Measured unclamped: more than LONG_LINES lines makes it a long turn.
  useLayoutEffect(() => {
    const p = ref.current;
    if (!p || long) return;
    const line = parseFloat(getComputedStyle(p).lineHeight) || 20;
    if (p.scrollHeight > LONG_LINES * line + 1) setLong(true);
  }, [turn.sentences, long]);
  const clamped = long && !open;
  return (
    <p ref={ref} className={`history-turn ${turn.speaker}${clamped ? " clamped" : ""}${
         long ? " long" : ""}${kindClass(turn)}`}
       onClick={long ? () => setOpen(!open) : undefined}
       title={long ? (open ? "Show less" : "Show all") : undefined}>
      <TurnWords turn={turn} speaking={null} />
    </p>
  );
}

function SpeakingBox({ turn, word }: { turn: Turn | null;
                                       word: [number, number, number] | null }) {
  const ref = useRef<HTMLDivElement>(null);
  // The line being spoken stays in view, with the line before it above.
  useLayoutEffect(() => {
    const box = ref.current;
    const now = box?.querySelector<HTMLElement>(".spoken-word.now");
    if (!box || !now) return;
    const line = parseFloat(getComputedStyle(box).lineHeight) || 20;
    const top = now.offsetTop;           // the box is the word's offset parent
    if (top < box.scrollTop || top + line > box.scrollTop + box.clientHeight) {
      box.scrollTop = Math.max(0, top - line);
    }
  }, [word, turn?.id]);
  return (
    <div className="conversation-speaking" ref={ref} aria-label="JARVIS is saying">
      {turn && <p className="caption-turn jarvis"><TurnWords turn={turn} speaking={word} /></p>}
    </div>
  );
}

function HearingBox({ turn }: { turn: Turn | null }) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [turn?.sentences]);
  return (
    <div className="conversation-hearing" ref={ref} aria-label="You are saying">
      {turn && <p className="caption-turn user">{turn.sentences.join(" ")}</p>}
    </div>
  );
}
