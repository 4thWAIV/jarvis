/**
 * The capture boxes: what Tim said to send, one box per open capture, with
 * its state over the top right of the border. White until it is known to be
 * a message for an agent; amber from then on, through transmitting; gone once
 * it is sent. Each grows to fit.
 *
 * When its text changes (his words as heard, then the brain's restatement)
 * the old text fades out as the new fades in, and the box eases to its new
 * height. While it is read back, the word being read glows (SpokenWords.tsx).
 */

import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { heardText } from "./captions";
import { captures, reading, type Capture } from "./capture";
import { SpokenWords } from "./SpokenWords";

const FADE_MS = 250;

/** Every open capture, in the order they opened. */
export function CaptureList() {
  const open = useSyncExternalStore(captures.subscribe, captures.get);
  if (!open.length) return null;
  return (
    <div className="capture-list">
      {open.map((c) => <CaptureBox key={c.id} capture={c} />)}
    </div>
  );
}

function CaptureBox({ capture }: { capture: Capture }) {
  // Opened from his words as heard, so the first thing seen is them turning
  // into the restatement.
  const [shown, setShown] = useState(() => ({
    text: capture.text, leaving: heardText(capture.heard) || null as string | null }));
  const height = useRef<HTMLDivElement>(null);
  const [px, setPx] = useState<number | null>(null);

  useLayoutEffect(() => {
    if (capture.text !== shown.text) setShown({ text: capture.text, leaving: shown.text });
  }, [capture.text]);                                 // eslint-disable-line react-hooks/exhaustive-deps

  // While the texts cross, the box eases from the old text's height to the
  // new one's; afterwards it is as tall as its text again, whatever the width.
  useLayoutEffect(() => {
    const body = height.current;
    if (!shown.leaving || !body) { setPx(null); return; }
    const from = body.querySelector<HTMLElement>(".capture-text.leaving")?.offsetHeight;
    const to = body.querySelector<HTMLElement>(".capture-text.arriving")?.offsetHeight;
    if (from !== undefined && to !== undefined) {
      setPx(from);
      requestAnimationFrame(() => setPx(to));
    }
    const done = setTimeout(() => setShown((s) => ({ ...s, leaving: null })), FADE_MS);
    return () => clearTimeout(done);
  }, [shown]);

  const message = capture.to ? " message" : "";
  return (
    <div className={`capture-box ${capture.state}${message}`} data-capture={capture.id}
         aria-live="polite">
      <span className="capture-state">{capture.state}</span>
      <div className="capture-body" ref={height} style={px === null ? undefined : { height: px }}>
        <CaptureText key={`in-${shown.text}`} capture={capture} text={shown.text} />
        {shown.leaving !== null &&
          <div className="capture-text leaving" aria-hidden="true">{shown.leaving}</div>}
      </div>
    </div>
  );
}

/** The capture's words. While it is read back, the word being read glows,
 *  exactly as the word JARVIS is speaking does everywhere else. */
function CaptureText({ capture, text }: { capture: Capture; text: string }) {
  const now = useSyncExternalStore(reading.subscribe, reading.get);
  return (
    <div className="capture-text arriving">
      <SpokenWords text={text} word={now && now.id === capture.id ? now.word : null} />
    </div>
  );
}
