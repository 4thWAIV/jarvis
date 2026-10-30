/**
 * A file on a message or a draft, as the owner sees it: its name, size and
 * declared type (the type is what was declared, never guessed from the
 * name, and nothing is opened), and where it is. Sending: prepared,
 * uploading, uploaded. Receiving: on the team server (Download), being
 * downloaded, downloaded and checked (Reveal in Finder, the one action on
 * it). Either side: failed, with the reason, or missing.
 */

import { downloadFile, revealFile } from "./queue";
import { shownFor } from "./messagingApi";
import type { AttachmentRef, LocalFile } from "./messagingContract";
import { QueueIcon } from "./QueueIcons";
import { sizeText } from "./queueStore";

function percent(file: LocalFile, ref: AttachmentRef): string {
  return ref.size_bytes ? ` ${Math.floor((100 * file.bytes_done) / ref.size_bytes)}%` : "";
}

function stateWords(file: LocalFile | undefined, ref: AttachmentRef, incoming: boolean): string {
  switch (file?.state) {
    case undefined: return incoming ? "On the team server" : "";
    case "staged": return "Prepared";
    case "uploading": return `Uploading${percent(file, ref)}`;
    case "available": return incoming ? "On the team server" : "Uploaded";
    case "downloading": return `Downloading${percent(file, ref)}`;
    case "verified": return "Downloaded and checked";
    case "failed": return `Failed: ${shownFor(file.failure ?? "transport", "it did not complete.")}`;
    case "missing": return incoming ? "Missing" : "Missing: attach it again";
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
  const state = file?.state ?? (incoming ? "available" : "staged");
  const canDownload = incoming && messageId && (state === "available" || state === "failed");
  return (
    <div className={`queue-file state-${state}`} data-attachment-id={fileRef.attachment_id}>
      <QueueIcon kind="files" size={16} />
      <span className="file-name">{fileRef.filename}</span>
      <span className="file-meta">{sizeText(fileRef.size_bytes)} · {fileRef.media_type}</span>
      <span className="file-state">{stateWords(file, fileRef, incoming)}</span>
      {canDownload && (
        <button type="button" className="file-action"
                onClick={() => void downloadFile(messageId!, fileRef.attachment_id)}>
          {state === "failed" ? "Try again" : "Download"}
        </button>
      )}
      {incoming && messageId && state === "verified" && (
        <button type="button" className="file-action"
                onClick={() => void revealFile(messageId, fileRef.attachment_id)}>
          Reveal in Finder
        </button>
      )}
      {onRemove && (
        <button type="button" className="file-action" onClick={onRemove}
                aria-label={`Remove ${fileRef.filename}`}>Remove</button>
      )}
    </div>
  );
}
