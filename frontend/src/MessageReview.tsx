/**
 * The inline review of one frozen revision, on the item it belongs to:
 * exactly who it goes to (the person and the JARVIS that holds their
 * inbox), the subject, the whole text as it was saved, and every file with
 * its size and declared type, then the total when there is more than one. Nothing here approves on its
 * own: the buttons are the existing capture box's (CaptureBox.tsx) on the
 * capture JARVIS opened for this review, so Send is the existing
 * `POST /api/capture/{n}/send` and the voice read-back is the same capture
 * read aloud. An edit after this makes a new revision, which closes the
 * capture; it has to be reviewed again.
 */

import { useSyncExternalStore } from "react";
import { bundleWords } from "./bundles";
import { captures } from "./capture";
import { CaptureButtons } from "./CaptureBox";
import type { Draft } from "./messagingContract";
import { useT } from "./locale";
import { directory } from "./queue";
import { FileCard } from "./QueueFiles";
import { recipientLabel, sizeText } from "./queueStore";

export function MessageReview({ draft }: { draft: Draft }) {
  const t = useT();
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const open = useSyncExternalStore(captures.subscribe, captures.get);
  // Its buttons only on the capture bound to this message's revision: a
  // number alone could name another message's capture.
  const capture = open.find((c) => c.id === draft.capture_id
    && c.human?.message_id === draft.message_id
    && c.human?.content_revision === draft.send.content_revision);
  const total = draft.attachments.reduce((n, a) => n + a.size_bytes, 0);
  return (
    <section className="message-review" aria-labelledby={`review-${draft.message_id}`}
             data-message-id={draft.message_id} data-content-revision={draft.send.content_revision}>
      <h3 className="review-heading" id={`review-${draft.message_id}`}>{t("main.queue.review.heading")}</h3>
      <dl className="review-fields">
        <dt>{t("main.queue.to")}</dt>
        <dd className="review-to">{recipientLabel(dir, draft.recipient_github_id)}</dd>
        <dt>{t("main.queue.subject")}</dt>
        <dd className="review-subject">{draft.subject || t("main.queue.noSubject")}</dd>
        {draft.cited.length > 0 && (
          <>
            <dt>{bundleWords.cited}</dt>
            <dd className="review-cited">{draft.cited.map((c) => c.path).join("; ")}</dd>
          </>
        )}
      </dl>
      {draft.text
        ? <div className="review-text message-text">{draft.text}</div>
        : <p className="review-no-text">{t("main.queue.review.noText")}</p>}
      {draft.attachments.length > 0 && (
        <div className="review-files">
          {draft.attachments.map((a) => (
            <FileCard key={a.attachment_id} fileRef={a} incoming={false} showType
                      file={draft.files.find((f) => f.attachment_id === a.attachment_id)} />
          ))}
          {draft.attachments.length > 1 && (
            <p className="review-total">
              {t("main.queue.review.total", { count: draft.attachments.length, size: sizeText(total) })}
            </p>
          )}
        </div>
      )}
      {capture
        ? <div className="review-capture" data-capture={capture.id}><CaptureButtons capture={capture} /></div>
        : <p className="review-waiting" role="status">{t("main.queue.review.opening")}</p>}
    </section>
  );
}
