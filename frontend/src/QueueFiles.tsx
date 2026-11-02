/**
 * A file on a message or a draft, as the owner sees it: its name and size,
 * its declared type (what was declared, never guessed from the name, and
 * nothing is opened) shown in the review and otherwise the name's tooltip,
 * and its state only when that is not the ordinary one. Sending: uploading.
 * Receiving: Download while it is on the team server, downloading, then its
 * exact path with Copy and Reveal in Finder (QueueFileLocation.tsx; and
 * Open page once JARVIS has opened it as a page work package, packages.ts).
 * Either side: failed, with the reason and Retry, or missing.
 */

import { useState, useSyncExternalStore } from "react";
import { openPackagePage, packageList, pagePackageFrom } from "./packages";
import { downloadFile } from "./queue";
import { FileLocation } from "./QueueFileLocation";
import { shownFor } from "./messagingApi";
import type { AttachmentRef, LocalFile } from "./messagingContract";
import { QueueIcon } from "./QueueIcons";
import { sizeText } from "./queueStore";

function percent(file: LocalFile, ref: AttachmentRef): string {
  return ref.size_bytes ? ` ${Math.floor((100 * file.bytes_done) / ref.size_bytes)}%` : "";
}

function stateWords(file: LocalFile | undefined, ref: AttachmentRef, incoming: boolean): string {
  switch (file?.state) {
    case undefined: return "";
    case "staged": return "";
    case "uploading": return `Uploading${percent(file, ref)}`;
    case "available": return "";
    case "downloading": return `Downloading${percent(file, ref)}`;
    case "verified": return "";
    case "failed": return `Failed: ${shownFor(file.failure ?? "transport", "it did not complete.")}`;
    case "missing": return incoming ? "Missing" : "Missing: attach it again";
  }
}

export function FileCard({ fileRef, file, messageId, incoming, onRemove, showType }: {
  fileRef: AttachmentRef;
  file?: LocalFile;
  /** The accepted message it is on; absent on a draft. */
  messageId?: string;
  incoming: boolean;
  onRemove?: () => void;
  /** Show the declared type beside the size (the review), not only as the tooltip. */
  showType?: boolean;
}) {
  const state = file?.state ?? (incoming ? "available" : "staged");
  const words = stateWords(file, fileRef, incoming);
  const canDownload = incoming && messageId && (state === "available" || state === "failed");
  return (
    <div className={`queue-file state-${state}`} data-attachment-id={fileRef.attachment_id}>
      <QueueIcon kind="files" size={16} />
      <span className="file-name" title={fileRef.media_type}>{fileRef.filename}</span>
      <span className="file-meta">
        {sizeText(fileRef.size_bytes)}{showType && ` · ${fileRef.media_type}`}
      </span>
      {words && <span className="file-state">{words}</span>}
      {canDownload && (
        <button type="button" className="file-action"
                onClick={() => void downloadFile(messageId!, fileRef.attachment_id)}>
          {state === "failed" ? "Retry" : "Download"}
        </button>
      )}
      {incoming && messageId && state === "verified" && (
        <FileLocation messageId={messageId} attachmentId={fileRef.attachment_id} />
      )}
      {incoming && state === "verified" && <OpenPage attachmentId={fileRef.attachment_id} />}
      {onRemove && (
        <button type="button" className="file-action" onClick={onRemove}
                aria-label={`Remove ${fileRef.filename}`}>Remove</button>
      )}
    </div>
  );
}

/** Open page, for a file JARVIS has unpacked as a page work package. */
function OpenPage({ attachmentId }: { attachmentId: string }) {
  const list = packageList();
  const packages = useSyncExternalStore(list.subscribe, list.get).value;
  const [problem, setProblem] = useState<string | null>(null);
  const found = pagePackageFrom(packages, attachmentId);
  if (!found) return null;
  return (
    <>
      <button type="button" className="file-action"
              onClick={async () => setProblem(await openPackagePage(found.id))}>
        Open page
      </button>
      {problem && <span className="file-state">{problem}</span>}
    </>
  );
}
