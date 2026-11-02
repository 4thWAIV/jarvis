/**
 * The one-line bundle states the Queue shows elsewhere: a message's one-off
 * request and its primary file (QueueItemView.tsx), the owner's review
 * progress on a thread (its cues), and before sending, whether the
 * recipient's JARVIS should read the draft's files (MessageComposer.tsx).
 * Each says nothing while there is nothing out of the ordinary to say.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import type { Readiness, ReviewProgress } from "./bundleContract";
import {
  bundlesChanged, bundleWords as W, readReadiness, readReview, taskOf,
} from "./bundles";
import type { AttachmentRef } from "./messagingContract";

/** What the sender asked for, and which file is the one to review. */
export function TaskLine({ message, attachments }: { message: object; attachments: AttachmentRef[] }) {
  const task = taskOf(message);
  if (!task) return null;
  const primary = task.primary && attachments.find((a) => a.attachment_id === task.primary!.attachment_id
                                                        && a.sha256 === task.primary!.sha256);
  return (
    <p className="message-task">
      <span className="bundle-badge">{W.outcome[task.outcome_kind] ?? W.outcome.other}</span>{" "}
      <span className="message-task-words">{task.requested_outcome}</span>
      {primary && (
        <span className="message-task-primary" aria-label={W.primary}>
          {" · "}{task.primary!.member_path || primary.filename}
        </span>
      )}
    </p>
  );
}

/** A read that is asked again on every "bundles" hint and says nothing
 *  when it cannot be read. */
function useHinted<T>(read: () => Promise<T>, key: string): T | null {
  const hint = useSyncExternalStore(bundlesChanged.subscribe, bundlesChanged.get);
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    let live = true;
    read().then((v) => { if (live) setValue(v); }, () => { if (live) setValue(null); });
    return () => { live = false; };
  }, [key, hint]);  // eslint-disable-line react-hooks/exhaustive-deps -- `read` is keyed by `key`
  return value;
}

/** The owner's review progress on a thread, as one label. */
export function ReviewProgressLabel({ thread }: { thread: string }) {
  const review = useHinted(() => readReview(thread), thread);
  const progress: ReviewProgress | null | undefined = review?.progress;
  if (!progress) return null;
  return <span className={`queue-cue bundle-progress progress-${progress}`}>{W.progress[progress]}</span>;
}

/** Before sending: shown only when the recipient may not read the files. */
export function ReadinessLine({ messageId, revision }: { messageId: string; revision: number }) {
  const result = useHinted(() => readReadiness(messageId), `${messageId}:${revision}`);
  const readiness: Readiness | undefined = result?.readiness;
  if (!result || readiness === "ready") return null;
  const gaps = result.gaps.map((g) => `${g.requirement.path ?? g.requirement.format}: ${g.action}`);
  return (
    <p className={`composer-readiness readiness-${readiness}`} role="status"
       title={gaps.join("\n") || undefined}>
      {W.readiness[readiness!]}
    </p>
  );
}
