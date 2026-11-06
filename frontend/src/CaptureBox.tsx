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
import { CENTRE_SOCKET } from "./ConversationView";
import { SaidText, type Said } from "./locale";
import { useT } from "./locale";
import { useUiTarget } from "./uiRegistry";

const FADE_MS = 250;

/** The centre views the capture boxes are drawn in. */
type CaptureView = "main.conversation" | "main.main";

/** Every open capture, in the order they opened, in `view` (the Main view
 *  unless said). Each box is registered there as a `capture` target. */
export function CaptureList({ view = "main.main" }: { view?: CaptureView }) {
  const open = useSyncExternalStore(captures.subscribe, captures.get);
  if (!open.length) return null;
  return (
    <div className="capture-list">
      {open.map((c) => <CaptureBox key={c.id} capture={c} view={view} />)}
    </div>
  );
}

function CaptureBox({ capture, view }: { capture: Capture; view: CaptureView }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view, kind: "capture", key: String(capture.id) },
                           label: capture.to ? t("main.capture.messageTo", { to: capture.to })
                             : t("main.capture.numbered", { id: String(capture.id) }),
                           socket: CENTRE_SOCKET });
  // Opened from his words as heard, so the first thing seen is them turning
  // into the restatement.
  const [shown, setShown] = useState(() => ({
    text: capture.text, leaving: heardText(capture.heard) || null as string | null }));
  const height = useRef<HTMLDivElement | null>(null);
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
    let frame = 0;
    if (from !== undefined && to !== undefined) {
      setPx(from);
      frame = requestAnimationFrame(() => setPx(to));
    }
    const done = setTimeout(() => setShown((s) => ({ ...s, leaving: null })), FADE_MS);
    return () => { clearTimeout(done); cancelAnimationFrame(frame); };
  }, [shown]);

  const message = capture.to ? " message" : "";
  return (
    <div className={`capture-box ${capture.state}${message}`} data-capture={capture.id}
         aria-live="polite" ref={ui.whole}>
      <span className="capture-state">{t(`main.capture.state.${capture.state}`)}</span>
      {capture.to && <span className="capture-to" ref={ui.part("recipient")}>{t("main.capture.sendTo", { to: capture.to })}</span>}
      <div className="capture-body" style={px === null ? undefined : { height: px }}
           ref={(el) => { height.current = el; ui.part("body")(el); }}>
        <CaptureText key={`in-${shown.text}`} capture={capture} text={shown.text} />
        {shown.leaving !== null &&
          <div className="capture-text leaving" aria-hidden="true">{shown.leaving}</div>}
      </div>
      <CaptureButtons capture={capture} actionsRef={ui.part("actions")} />
    </div>
  );
}

/** Accept, Delete and Send. Each waits for the server, which changes the box
 *  itself; a refusal is shown until the next press. The Queue's inline
 *  review (MessageReview.tsx) uses these same buttons on its capture. They
 *  are Tim's own acts on the capture, never navigation: the brain can point
 *  at them (`actionsRef`) but not press them. */
export function CaptureButtons({ capture, actionsRef }: {
  capture: Capture; actionsRef?: (el: Element | null) => void }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [refused, setRefused] = useState<Said | null>(null);
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
      {refused && <SaidText said={refused} as="div" className="capture-refused" role="alert" />}
      <div className="capture-buttons" ref={actionsRef}>
        {/* Read aloud; heard to the end, it counts as read back. */}
        <button data-ui-action="domain" type="button" className="capture-read" disabled={busy || sending || beingRead}
                title={t("main.capture.readAloud")} onClick={() => act("read")}>
          {beingRead ? t("main.capture.reading") : t("main.capture.read")}
        </button>
        <button data-ui-action="domain" type="button" className={`capture-accept${capture.accepted ? " done" : ""}`}
                disabled={busy || sending || capture.accepted}
                title={capture.accepted ? t("main.capture.acceptedTitle") : t("main.capture.acceptTitle")}
                onClick={() => act("accept")}>
          {capture.accepted ? t("main.capture.accepted") : t("main.capture.accept")}
        </button>
        <button data-ui-action="domain" type="button" className="capture-delete" disabled={busy || sending}
                onClick={() => act("delete")}>{t("main.capture.delete")}</button>
        <button data-ui-action="domain" type="button" className="capture-send" disabled={busy || sending || !capture.to}
                title={capture.to ? t("main.capture.sendTitle", { to: capture.to }) : t("main.capture.unaddressed")}
                onClick={() => act("send")}>{t("main.capture.send")}</button>
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
