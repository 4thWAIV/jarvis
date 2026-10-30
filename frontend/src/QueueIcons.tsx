/**
 * The Queue's one set of state icons, for its rows, the open item and its
 * actions. Every icon is decorative and sits beside its words (`Cue`), so a
 * screen reader hears one label and nothing depends on colour: the owner's
 * turn is a solid person badge, the correspondent's an outlined arrow, and
 * the owner's own read cues (Unread, Read by you) are drawn apart from what
 * happened at the other end (Awaiting receipt, Received by their JARVIS,
 * Read by …). No icon moves.
 */

import type { ReactNode } from "react";
import type { Delivery } from "./queueStore";

export type CueKind =
  | "your-turn" | "their-turn" | "unread" | "read-by-you" | "addressed" | "replied"
  | "files" | "related" | "awaiting" | "received" | "read-by-them" | "dismissed";

/** The icon for how far an outgoing message got. */
export const DELIVERY_CUE: Record<Delivery, CueKind> = {
  awaiting: "awaiting", received: "received", read: "read-by-them",
};

const STROKE = { fill: "none", stroke: "currentColor", strokeWidth: 1.8,
                 strokeLinecap: "round", strokeLinejoin: "round" } as const;

const eye = (
  <>
    <path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12z" {...STROKE} />
    <circle cx="12" cy="12" r="2.6" {...STROKE} />
  </>
);

const DRAWN: Record<CueKind | "queue" | "new", ReactNode> = {
  // Solid: the owner has the next action.
  "your-turn": (
    <>
      <circle cx="12" cy="8" r="4" fill="currentColor" />
      <path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7z" fill="currentColor" />
    </>
  ),
  // Outlined and pointing away: the correspondent has it.
  "their-turn": <path d="M4 12h13M12 6l6 6-6 6" {...STROKE} />,
  unread: <circle cx="12" cy="12" r="5" fill="currentColor" />,
  "read-by-you": eye,
  addressed: <path d="M4 12.5l5 5L20 6.5" {...STROKE} />,
  replied: <path d="M10 6L4 12l6 6M4 12h10a6 6 0 0 1 6 6" {...STROKE} />,
  files: (
    <>
      <path d="M6 3h8l4 4v14H6z" {...STROKE} />
      <path d="M14 3v4h4" {...STROKE} />
    </>
  ),
  related: (
    <>
      <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" {...STROKE} />
      <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" {...STROKE} />
    </>
  ),
  awaiting: (
    <>
      <circle cx="12" cy="12" r="8.5" {...STROKE} />
      <path d="M12 7.5V12l3 2" {...STROKE} />
    </>
  ),
  received: (
    <>
      <path d="M3 13l3-8h12l3 8v6H3z" {...STROKE} />
      <path d="M3 13h5l1.5 3h5L16 13h5" {...STROKE} />
    </>
  ),
  "read-by-them": eye,
  dismissed: (
    <>
      <circle cx="12" cy="12" r="8.5" {...STROKE} />
      <path d="M8 12.3l2.8 2.8L16.2 9.5" {...STROKE} />
    </>
  ),
  // The Queue tab: a stack of items, the newest on top.
  queue: (
    <>
      <rect x="4" y="4" width="16" height="5" rx="1" {...STROKE} />
      <path d="M4 13h16M4 18h16" {...STROKE} />
    </>
  ),
  new: <path d="M12 5v14M5 12h14" {...STROKE} />,
};

export function QueueIcon({ kind, size = 14 }: { kind: keyof typeof DRAWN; size?: number }) {
  return (
    <svg className={`queue-icon icon-${kind}`} width={size} height={size} viewBox="0 0 24 24"
         aria-hidden="true" focusable="false">
      {DRAWN[kind]}
    </svg>
  );
}

/** One cue: its icon and its words, always both. `title` adds detail on
 *  hover; the words alone must already say it. */
export function Cue({ kind, children, title }: { kind: CueKind; children: ReactNode; title?: string }) {
  return (
    <span className={`queue-cue cue-${kind}`} data-cue={kind} title={title}>
      <QueueIcon kind={kind} size={12} />
      <span className="cue-words">{children}</span>
    </span>
  );
}
