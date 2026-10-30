/**
 * The owner's response, or a new conversation, written in place on the
 * item: who it is for (chosen from the existing team roster, Me included,
 * for a new conversation; the correspondent and the exact message answered
 * for a response), the subject, the text and the files. It never sends:
 * Review freezes it and opens the inline review (MessageReview.tsx), whose
 * Send is the owner's approval. A draft JARVIS wrote is marked as one.
 *
 * The text is the editor's own (queue.ts keeps it), saved after a pause and
 * before the item is left; a failed or conflicting save keeps it and says
 * so, with the ways out.
 */

import { useState, useSyncExternalStore } from "react";
import { LIMITS, type Draft } from "./messagingContract";
import {
  addFiles, directory, discard, edit, editors, keepMine, queue, removeFile, review, roster, takeTheirs,
} from "./queue";
import { FileCard } from "./QueueFiles";
import { personName, previewOf, recipientLabel } from "./queueStore";

const MB = 1024 * 1024;
const LOCKED = new Set(["approved", "submitting", "accept_unknown", "accepted"]);

export function MessageComposer({ editorKey }: { editorKey: string }) {
  const ed = useSyncExternalStore(editors.subscribe, editors.get)[editorKey];
  const state = useSyncExternalStore(queue.subscribe, queue.get);
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const members = useSyncExternalStore(roster.subscribe, roster.get)?.members ?? [];
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  if (!ed) return null;
  const draft: Draft | undefined = ed.messageId ? state.drafts[ed.messageId] : undefined;
  const locked = !!draft && LOCKED.has(draft.send.state);
  const parent = ed.parent ? state.messages[ed.parent]?.message : undefined;
  const status = ed.saving ? "Saving…" : ed.dirty ? "Not saved yet" : ed.messageId ? "Draft saved, not sent" : "";
  const canReview = !locked && !ed.saving && ed.adding === 0 && ed.recipient !== null
    && (ed.text.trim() !== "" || ed.attachmentIds.length > 0);
  const id = `composer-${editorKey}`;
  return (
    <section className="message-composer" aria-label={ed.thread ? "Your response" : "New message"}
             data-editor={editorKey}>
      {draft?.agent_drafted && (
        <p className="composer-agent">Drafted by JARVIS. Nothing is sent until you review and approve it.</p>
      )}
      <div className="composer-to">
        {ed.thread
          ? <span>To <strong>{recipientLabel(dir, ed.recipient!)}</strong></span>
          : (
            <label>
              To{" "}
              <select value={ed.recipient ?? ""} disabled={locked}
                      onChange={(e) => edit(editorKey, { recipient: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Choose who it is for</option>
                {[...members].sort((a, b) => Number(b.github_id === dir.owner) - Number(a.github_id === dir.owner))
                  .map((m) => (
                    <option key={m.github_id} value={m.github_id}>{recipientLabel(dir, m.github_id)}</option>
                  ))}
              </select>
            </label>
          )}
        <span className="composer-inbox">The message goes to the person; their JARVIS holds their inbox.</span>
      </div>
      {parent && (
        <p className="composer-parent">
          In reply to {personName(dir, parent.sender_github_id)}: “{previewOf(parent) || parent.subject}”
        </p>
      )}
      <label className="composer-field">
        <span>Subject</span>
        <input type="text" value={ed.subject} maxLength={LIMITS.SUBJECT_MAX_CHARS} disabled={locked}
               onChange={(e) => edit(editorKey, { subject: e.target.value })} />
      </label>
      <label className="composer-field" htmlFor={id}>
        <span>Message</span>
      </label>
      <textarea id={id} className="composer-text" value={ed.text} rows={6} disabled={locked}
                maxLength={LIMITS.TEXT_MAX_CHARS}
                onChange={(e) => edit(editorKey, { text: e.target.value })} />
      <div className="composer-files">
        {(draft?.attachments ?? []).filter((a) => ed.attachmentIds.includes(a.attachment_id)).map((a) => (
          <FileCard key={a.attachment_id} fileRef={a} incoming={false}
                    file={draft!.files.find((f) => f.attachment_id === a.attachment_id)}
                    onRemove={locked ? undefined : () => removeFile(editorKey, a.attachment_id)} />
        ))}
        {ed.adding > 0 && <p className="composer-adding" role="status">Adding {ed.adding} file{ed.adding === 1 ? "" : "s"}…</p>}
        <label className="composer-attach">
          Attach files
          <input type="file" multiple disabled={locked || ed.recipient === null}
                 onChange={(e) => { void addFiles(editorKey, [...(e.target.files ?? [])]); e.target.value = ""; }} />
        </label>
        <span className="composer-limits">
          Up to {LIMITS.ATTACHMENTS_MAX} files, {LIMITS.FILE_MAX_BYTES / MB} MB each, {LIMITS.PACKAGE_MAX_BYTES / MB} MB in all.
        </span>
      </div>
      {ed.error && <p className="composer-error" role="alert">{ed.error}</p>}
      {ed.conflict && (
        <div className="composer-conflict">
          <button type="button" onClick={() => keepMine(editorKey)}>Keep my text</button>
          <button type="button" onClick={() => takeTheirs(editorKey)}>Use the saved draft</button>
        </div>
      )}
      <div className="composer-actions">
        <span className="composer-status" role="status">{status}</span>
        {confirmDiscard ? (
          <>
            <span className="composer-confirm">Throw this draft away? It cannot be brought back.</span>
            <button type="button" className="composer-discard" onClick={() => { setConfirmDiscard(false); void discard(editorKey); }}>
              Discard draft
            </button>
            <button type="button" onClick={() => setConfirmDiscard(false)}>Keep it</button>
          </>
        ) : (
          <button type="button" className="composer-discard" disabled={locked} onClick={() => setConfirmDiscard(true)}>
            Discard…
          </button>
        )}
        <button type="button" className="composer-review" disabled={!canReview}
                onClick={() => void review(editorKey)}>Review</button>
      </div>
    </section>
  );
}
