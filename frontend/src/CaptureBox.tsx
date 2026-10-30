/**
 * The capture boxes: what Tim said to send, one box per open capture, with
 * its state over the top right of the border. White until it is known to be
 * a message for an agent; amber from then on, through transmitting; gone once
 * it is sent. A message says who it goes to over the top left ("Send to:"),
 * opposite its state. Each grows to fit.
 *
 * Accept, Delete and Send sit in the bottom right: Tim acting on it by hand.
 * Accept counts as his having read it; Send accepts it and sends it to the
 * agent it is addressed to. A refusal is shown under the text.
 *
 * When its text changes (his words as heard, then the brain's restatement)
 * the old text fades out as the new fades in, and the box eases to its new
 * height. While it is read back, the word being read glows (SpokenWords.tsx).
 */

import { useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { heardText } from "./captions";
import { captureAction, captures, reading, type Capture, type CaptureAction } from "./capture";
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
      {capture.to && <span className="capture-to">Send to: {capture.to}</span>}
      <div className="capture-body" ref={height} style={px === null ? undefined : { height: px }}>
        <CaptureText key={`in-${shown.text}`} capture={capture} text={shown.text} />
        {shown.leaving !== null &&
          <div className="capture-text leaving" aria-hidden="true">{shown.leaving}</div>}
      </div>
      <CaptureButtons capture={capture} />
    </div>
  );
}

/** Accept, Delete and Send. Each waits for the server, which changes the box
 *  itself; a refusal is shown until the next press. The Queue's inline
 *  review (MessageReview.tsx) uses these same buttons on its capture. */
export function CaptureButtons({ capture }: { capture: Capture }) {
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  const sending = capture.state === "transmitting";
  const beingRead = capture.state === "reading";
  const act = (action: CaptureAction) => {
    setBusy(true);
    setRefused(null);
    captureAction(capture.id, action).then((why) => {
      setBusy(false);
      setRefused(why);
    });
  };
  return (
    <>
      {refused && <div className="capture-refused" role="alert">{refused}</div>}
      <div className="capture-buttons">
        {/* Read aloud; heard to the end, it counts as read back. */}
        <button type="button" className="capture-read" disabled={busy || sending || beingRead}
                title="Read it aloud" onClick={() => act("read")}>
          {beingRead ? "Reading" : "Read"}
        </button>
        <button type="button" className={`capture-accept${capture.accepted ? " done" : ""}`}
                disabled={busy || sending || capture.accepted}
                title={capture.accepted ? "Accepted: counts as read" : "Accept: counts as read"}
                onClick={() => act("accept")}>
          {capture.accepted ? "✓ Accepted" : "✓ Accept"}
        </button>
        <button type="button" className="capture-delete" disabled={busy || sending}
                onClick={() => act("delete")}>Delete</button>
        <button type="button" className="capture-send" disabled={busy || sending || !capture.to}
                title={capture.to ? `Send to ${capture.to}` : "Not addressed to anyone yet"}
                onClick={() => act("send")}>Send</button>
      </div>
    </>
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
