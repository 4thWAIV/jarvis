/**
 * The inline review of one frozen revision, on the item it belongs to:
 * exactly who it goes to (the person and the JARVIS that holds their
 * inbox), the subject, the whole text as it was saved, and every file with
 * its size and declared type, then the total. Nothing here approves on its
 * own: the buttons are the existing capture box's (CaptureBox.tsx) on the
 * capture JARVIS opened for this review, so Send is the existing
 * `POST /api/capture/{n}/send` and the voice read-back is the same capture
 * read aloud. An edit after this makes a new revision, which closes the
 * capture; it has to be reviewed again.
 */

import { useSyncExternalStore } from "react";
import { captures } from "./capture";
import { CaptureButtons } from "./CaptureBox";
import type { Draft } from "./messagingContract";
import { directory } from "./queue";
import { FileCard } from "./QueueFiles";
import { recipientLabel, sizeText } from "./queueStore";

export function MessageReview({ draft }: { draft: Draft }) {
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const open = useSyncExternalStore(captures.subscribe, captures.get);
  // Its buttons only on the capture bound to this message's revision: a
  // number alone could name another message's capture.
  const capture = open.find((c) => c.id === draft.capture_id
    && c.human?.message_id === draft.message_id
    && c.human?.content_revision === draft.send.content_revision);
  const total = draft.attachments.reduce((n, a) => n + a.size_bytes, 0);
  return (
    <section className="message-review" aria-label="Review before sending"
             data-message-id={draft.message_id} data-content-revision={draft.send.content_revision}>
      <h3 className="review-heading">Review: exactly this will be sent</h3>
      <dl className="review-fields">
        <dt>To</dt>
        <dd className="review-to">{recipientLabel(dir, draft.recipient_github_id)}</dd>
        <dt>Subject</dt>
        <dd className="review-subject">{draft.subject || "(no subject)"}</dd>
      </dl>
      {draft.text
        ? <div className="review-text message-text">{draft.text}</div>
        : <p className="review-no-text">No text: only the files below.</p>}
      {draft.attachments.length > 0 && (
        <div className="review-files">
          {draft.attachments.map((a) => (
            <FileCard key={a.attachment_id} fileRef={a} incoming={false}
                      file={draft.files.find((f) => f.attachment_id === a.attachment_id)} />
          ))}
          <p className="review-total">
            {draft.attachments.length} file{draft.attachments.length === 1 ? "" : "s"}, {sizeText(total)} in all
          </p>
        </div>
      )}
      {capture
        ? <div className="review-capture" data-capture={capture.id}><CaptureButtons capture={capture} /></div>
        : <p className="review-waiting" role="status">Opening the review…</p>}
    </section>
  );
}
