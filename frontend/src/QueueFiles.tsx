/**
 * A file on a message or a draft, as the owner sees it: its name, size and
 * declared type (the type is what was declared, never guessed from the
 * name, and nothing is opened), and where it is. Sending: prepared,
 * uploading, uploaded. Receiving: on the team server (Download), being
 * downloaded, downloaded and checked (Reveal in Finder, and Open page once
 * JARVIS has opened it as a page work package, packages.ts). Either side:
 * failed, with the reason, or missing.
 */

import { useState, useSyncExternalStore } from "react";
import { BundleInventory } from "./BundleInventory";
import { openPackagePage, packageList, pagePackageFrom } from "./packages";
import { downloadFile, revealFile } from "./queue";
import { shownFor } from "./messagingApi";
import type { AttachmentRef, LocalFile, OpenabilityReport } from "./messagingContract";
import { bundleWords } from "./bundles";
import type { Reason } from "./bundleContract";
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

/** What the recipient's JARVIS reported when it opened a sent file: shown
 *  only once there is a report, with the first member that failed. */
function OpenedThere({ report }: { report: OpenabilityReport }) {
  const fine = report.status === "verified" || report.status === "ready";
  const why = [report.failed_member, bundleWords.reason(report.reason as Reason | null)].filter(Boolean)
    .join(": ");
  return (
    <span className={`file-state${fine ? "" : " bundle-problem"}`} title={why || undefined}>
      {bundleWords.openability[report.status]}
      {!fine && report.failed_member ? ` · ${report.failed_member.split("/").pop()}` : ""}
    </span>
  );
}

export function FileCard({ fileRef, file, messageId, incoming, report, onRemove }: {
  fileRef: AttachmentRef;
  file?: LocalFile;
  /** On a sent file: the recipient's JARVIS's report, once it opened it. */
  report?: OpenabilityReport;
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
      {!incoming && report && <OpenedThere report={report} />}
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
      {incoming && state === "verified" && <OpenPage attachmentId={fileRef.attachment_id} />}
      {incoming && messageId && state === "verified" && <BundleInventory file={{ messageId, attachmentId: fileRef.attachment_id }} filename={fileRef.filename} />}
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
