/**
 * Where a capture's words were in the conversation: a collapsed item named
 * by what the capture does (its kind: "Transmission to <agent>" for a
 * message to an agent), or "Capture <number>" until that is known. Each kind
 * has its own look; a transmission is amber and sits on the right. Opening
 * it shows what Tim said, in a quiet grey, then the capture itself: the
 * cleaned-up version, which for a transmission is what was approved and sent.
 *
 * Opening and closing it is `conversation.record.expand` on its turn's
 * record key (captions.ts), for a click and the brain alike; `partRef`, its
 * turn's target, registers its head as the record's `expand` and its body
 * as its `answer`.
 */

import { turnKey, useRecordOpen, type Turn } from "./captions";
import { captureKind, KIND_LABEL } from "./capture";
import { bind, bindingProps } from "./uiNavigation";

export function CaptureChip({ turn, partRef }: { turn: Turn;
                                                 partRef?: (part: string) => (el: Element | null) => void }) {
  const recordId = turnKey(turn);
  const open = useRecordOpen(recordId, true);
  const capture = turn.capture!;
  const kind = captureKind(capture);
  return (
    <span className={`capture-chip${kind ? ` kind-${kind}` : ""}${open ? " open" : ""}`}>
      <button type="button" className="capture-chip-head" aria-expanded={open} ref={partRef?.("expand")}
              {...bindingProps(bind("conversation.record.expand", { recordId, open: !open }))}>
        {kind ? KIND_LABEL[kind](capture) : `Capture ${capture.id}`}
      </button>
      {open && (
        <span className="capture-chip-body" ref={partRef?.("answer")}>
          <span className="capture-chip-said">{turn.sentences.join(" ")}</span>
          <span className="capture-chip-captured">{capture.text}</span>
        </span>
      )}
    </span>
  );
}
