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
import { SaidText } from "./ConversationView";
import { formatNumber, useT } from "./locale";
import { LIMITS, type Draft } from "./messagingContract";
import {
  addFiles, directory, discard, edit, editors, keepMine, queue, removeFile, review, roster, takeTheirs,
} from "./queue";
import { FileCard } from "./QueueFiles";
import { personName, previewOf, recipientLabel } from "./queueStore";

const MB = 1024 * 1024;
const LOCKED = new Set(["approved", "submitting", "accept_unknown", "accepted"]);

export function MessageComposer({ editorKey }: { editorKey: string }) {
  const t = useT();
  const ed = useSyncExternalStore(editors.subscribe, editors.get)[editorKey];
  const state = useSyncExternalStore(queue.subscribe, queue.get);
  const dir = useSyncExternalStore(directory.subscribe, directory.get);
  const members = useSyncExternalStore(roster.subscribe, roster.get)?.members ?? [];
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  if (!ed) return null;
  const draft: Draft | undefined = ed.messageId ? state.drafts[ed.messageId] : undefined;
  const locked = !!draft && LOCKED.has(draft.send.state);
  const parent = ed.parent ? state.messages[ed.parent]?.message : undefined;
  const status = ed.saving ? t("main.queue.saving") : ed.dirty ? t("main.queue.composer.notSaved")
    : ed.messageId ? t("main.queue.composer.saved") : "";
  const canReview = !locked && !ed.saving && ed.adding === 0 && ed.recipient !== null
    && (ed.text.trim() !== "" || ed.attachmentIds.length > 0);
  const id = `composer-${editorKey}`;
  return (
    <section className="message-composer" aria-label={ed.thread ? t("main.queue.composer.response") : t("main.queue.newMessage")}
             data-editor={editorKey}>
      {draft?.agent_drafted && (
        <p className="composer-agent">{t("main.queue.composer.agentDrafted")}</p>
      )}
      <div className="composer-to">
        {ed.thread
          ? <span>{t("main.queue.toLead")} <strong>{recipientLabel(dir, ed.recipient!)}</strong></span>
          : (
            <label>
              {t("main.queue.toLead")}{" "}
              <select value={ed.recipient ?? ""} disabled={locked}
                      onChange={(e) => edit(editorKey, { recipient: e.target.value ? Number(e.target.value) : null })}>
                <option value="">{t("main.queue.composer.choose")}</option>
                {[...members].sort((a, b) => Number(b.github_id === dir.owner) - Number(a.github_id === dir.owner))
                  .map((m) => (
                    <option key={m.github_id} value={m.github_id}>{recipientLabel(dir, m.github_id)}</option>
                  ))}
              </select>
            </label>
          )}
        <span className="composer-inbox">{t("main.queue.composer.inbox")}</span>
      </div>
      {parent && (
        <p className="composer-parent">
          {t("main.queue.inReplyToQuote", { person: personName(dir, parent.sender_github_id),
                                            quote: previewOf(parent) || parent.subject })}
        </p>
      )}
      <label className="composer-field">
        <span>{t("main.queue.subject")}</span>
        <input type="text" value={ed.subject} maxLength={LIMITS.SUBJECT_MAX_CHARS} disabled={locked}
               onChange={(e) => edit(editorKey, { subject: e.target.value })} />
      </label>
      <label className="composer-field" htmlFor={id}>
        <span>{t("main.queue.composer.message")}</span>
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
        {ed.adding > 0 && <p className="composer-adding" role="status">{t("main.queue.composer.adding", { count: ed.adding })}</p>}
        <label className="composer-attach">
          {t("main.queue.composer.attach")}
          <input type="file" multiple disabled={locked || ed.recipient === null}
                 onChange={(e) => { void addFiles(editorKey, [...(e.target.files ?? [])]); e.target.value = ""; }} />
        </label>
        <span className="composer-limits">
          {t("main.queue.composer.limits", { count: LIMITS.ATTACHMENTS_MAX, each: formatNumber(LIMITS.FILE_MAX_BYTES / MB),
                                             all: formatNumber(LIMITS.PACKAGE_MAX_BYTES / MB) })}
        </span>
      </div>
      {ed.error && <SaidText said={ed.error} as="p" className="composer-error" role="alert" />}
      {ed.conflict && (
        <div className="composer-conflict">
          <button type="button" onClick={() => keepMine(editorKey)}>{t("main.queue.composer.keepMine")}</button>
          <button type="button" onClick={() => takeTheirs(editorKey)}>{t("main.queue.composer.takeTheirs")}</button>
        </div>
      )}
      <div className="composer-actions">
        <span className="composer-status" role="status">{status}</span>
        {confirmDiscard ? (
          <>
            <span className="composer-confirm">{t("main.queue.composer.discardQuestion")}</span>
            <button type="button" className="composer-discard" onClick={() => { setConfirmDiscard(false); void discard(editorKey); }}>
              {t("main.queue.composer.discardDraft")}
            </button>
            <button type="button" onClick={() => setConfirmDiscard(false)}>{t("main.common.keepIt")}</button>
          </>
        ) : (
          <button type="button" className="composer-discard" disabled={locked} onClick={() => setConfirmDiscard(true)}>
            {t("main.queue.composer.discard")}
          </button>
        )}
        <button type="button" className="composer-review" disabled={!canReview}
                onClick={() => void review(editorKey)}>{t("main.queue.composer.review")}</button>
      </div>
    </section>
  );
}
