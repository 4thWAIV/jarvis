/**
 * Where a capture's words were in the conversation: a collapsed item named
 * by what the capture does (its kind: "Transmission to <agent>" for a
 * message to an agent), or "Capture <number>" until that is known. Each kind
 * has its own look; a transmission is amber and sits on the right. Opening
 * it shows what Tim said, in a quiet grey, then the capture itself: the
 * cleaned-up version, which for a transmission is what was approved and sent.
 */

import { useState } from "react";
import type { Turn } from "./captions";
import { captureKind, KIND_LABEL } from "./capture";

export function CaptureChip({ turn }: { turn: Turn }) {
  const [open, setOpen] = useState(false);
  const capture = turn.capture!;
  const kind = captureKind(capture);
  return (
    <span className={`capture-chip${kind ? ` kind-${kind}` : ""}${open ? " open" : ""}`}>
      <button type="button" className="capture-chip-head" aria-expanded={open}
              onClick={() => setOpen(!open)}>
        {kind ? KIND_LABEL[kind](capture) : `Capture ${capture.id}`}
      </button>
      {open && (
        <span className="capture-chip-body">
          <span className="capture-chip-said">{turn.sentences.join(" ")}</span>
          <span className="capture-chip-captured">{capture.text}</span>
        </span>
      )}
    </span>
  );
}
