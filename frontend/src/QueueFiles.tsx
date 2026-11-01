/**
 * A file on a message or a draft, as the owner sees it: its name, size and
 * declared type (the type is what was declared, never guessed from the
 * name, and nothing is opened), and where it is. Sending: prepared,
 * uploading, uploaded. Receiving: on the team server (Download), being
 * downloaded, downloaded and checked (Reveal in Finder, the one action on
 * it). Either side: failed, with the reason, or missing.
 */

import { downloadFile, messagingSaid, revealFile } from "./queue";
import { formatPercent, saidShown, t as tNow, useT } from "./locale";
import type { AttachmentRef, LocalFile } from "./messagingContract";
import { QueueIcon } from "./QueueIcons";
import { sizeText } from "./queueStore";
import { CENTRE_SOCKET } from "./ConversationView";
import { useUiTarget } from "./uiRegistry";

/** How far a transfer has got, or null before its size is known. */
function percent(file: LocalFile, ref: AttachmentRef): string | null {
  return ref.size_bytes ? formatPercent(Math.floor((100 * file.bytes_done) / ref.size_bytes) / 100) : null;
}

/** "Uploading 40%", or "Uploading" before its size is known. */
function moving(key: "uploading" | "downloading", file: LocalFile, ref: AttachmentRef): string {
  const done = percent(file, ref);
  return done === null ? tNow(`main.queue.file.${key}`) : tNow(`main.queue.file.${key}At`, { percent: done });
}

function stateWords(file: LocalFile | undefined, ref: AttachmentRef, incoming: boolean): string {
  switch (file?.state) {
    case undefined: return incoming ? tNow("main.queue.file.onServer") : "";
    case "staged": return tNow("main.queue.file.staged");
    case "uploading": return moving("uploading", file, ref);
    case "available": return incoming ? tNow("main.queue.file.onServer") : tNow("main.queue.file.uploaded");
    case "downloading": return moving("downloading", file, ref);
    case "verified": return tNow("main.queue.file.verified");
    case "failed": return tNow("main.queue.file.failed", { reason: saidShown(messagingSaid(file.failure ?? "transport",
      { words: () => tNow("main.queue.file.incomplete") })).text });
    case "missing": return incoming ? tNow("main.queue.file.missing") : tNow("main.queue.file.missingAttach");
  }
}

export function FileCard({ fileRef, file, messageId, incoming, onRemove }: {
  fileRef: AttachmentRef;
  file?: LocalFile;
  /** The accepted message it is on; absent on a draft. */
  messageId?: string;
  incoming: boolean;
  onRemove?: () => void;
}) {
  const t = useT();
  const state = file?.state ?? (incoming ? "available" : "staged");
  const canDownload = incoming && messageId && (state === "available" || state === "failed");
  // A file on an accepted message can be pointed at; a draft's is not yet one.
  const ui = useUiTarget(messageId ? {
    ref: { region: "main", view: "main.queue", kind: "queueAttachment", key: fileRef.attachment_id },
    label: t("main.queue.file.label"), socket: CENTRE_SOCKET } : null);
  return (
    <div className={`queue-file state-${state}`} data-attachment-id={fileRef.attachment_id}
         ref={(el) => { ui.whole(el); ui.part("entry")(el); }}>
      <QueueIcon kind="files" size={16} />
      <span className="file-name">{fileRef.filename}</span>
      <span className="file-meta">{sizeText(fileRef.size_bytes)} · {fileRef.media_type}</span>
      <span className="file-state">{stateWords(file, fileRef, incoming)}</span>
      {canDownload && (
        <button type="button" className="file-action"
                onClick={() => void downloadFile(messageId!, fileRef.attachment_id)}>
          {state === "failed" ? t("main.queue.file.tryAgain") : t("main.queue.file.download")}
        </button>
      )}
      {incoming && messageId && state === "verified" && (
        <button type="button" className="file-action"
                onClick={() => void revealFile(messageId, fileRef.attachment_id)}>
          {t("main.queue.file.reveal")}
        </button>
      )}
      {onRemove && (
        <button type="button" className="file-action" onClick={onRemove}
                aria-label={t("main.queue.file.removeNamed", { name: fileRef.filename })}>{t("main.queue.file.remove")}</button>
      )}
    </div>
  );
}
