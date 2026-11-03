/**
 * A file on a message or a draft, as the owner sees it: its name and size,
 * its declared type (what was declared, never guessed from the name, and
 * nothing is opened) shown in the review and otherwise the name's tooltip,
 * and its state only when that is not the ordinary one. Sending: uploading.
 * Receiving: Download while it is on the team server, downloading, then its
 * exact path with Copy and Reveal in Finder (QueueFileLocation.tsx; and
 * Open package on a zip not unpacked yet, and Open page once it is unpacked
 * as a page work package, packages.ts).
 * Either side: failed, with the reason and Retry, or missing. A sent file:
 * the recipient's JARVIS's report once it opened it.
 */

import { useState, useSyncExternalStore } from "react";
import { BundleInventory } from "./BundleInventory";
import { openPackagePage, openReceivedPackage, packageFrom, packageList } from "./packages";
import { downloadFile } from "./queue";
import { FileLocation } from "./QueueFileLocation";
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

export function FileCard({ fileRef, file, messageId, incoming, report, onRemove, showType }: {
  fileRef: AttachmentRef;
  file?: LocalFile;
  /** On a sent file: the recipient's JARVIS's report, once it opened it. */
  report?: OpenabilityReport;
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
      {!incoming && report && <OpenedThere report={report} />}
      {canDownload && (
        <button type="button" className="file-action"
                onClick={() => void downloadFile(messageId!, fileRef.attachment_id)}>
          {state === "failed" ? "Retry" : "Download"}
        </button>
      )}
      {incoming && messageId && state === "verified" && (
        <FileLocation messageId={messageId} attachmentId={fileRef.attachment_id} />
      )}
      {incoming && messageId && state === "verified" && <OpenPackage messageId={messageId} fileRef={fileRef} />}
      {incoming && messageId && state === "verified" && <BundleInventory file={{ messageId, attachmentId: fileRef.attachment_id }} filename={fileRef.filename} />}
      {onRemove && (
        <button type="button" className="file-action" onClick={onRemove}
                aria-label={`Remove ${fileRef.filename}`}>Remove</button>
      )}
    </div>
  );
}

/** A zip, by its declared type or its name: what a work package comes as. */
function isZip(ref: AttachmentRef): boolean {
  return ref.media_type === "application/zip" || ref.filename.toLowerCase().endsWith(".zip");
}

/** Open page, for a file JARVIS has unpacked as a page work package; Open
 *  package, for a zip not unpacked yet (a page package then opens at once). */
function OpenPackage({ messageId, fileRef }: { messageId: string; fileRef: AttachmentRef }) {
  const list = packageList();
  const packages = useSyncExternalStore(list.subscribe, list.get).value;
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const unpacked = packageFrom(packages, fileRef.attachment_id);
  const act = async (open: () => Promise<string | null>) => {
    setBusy(true);
    setProblem(await open());
    setBusy(false);
  };
  const button = unpacked?.runs === "page"
    ? { label: "Open page", open: () => openPackagePage(unpacked.id) }
    : packages && !unpacked && isZip(fileRef)
      ? { label: "Open package", open: () => openReceivedPackage(messageId, fileRef.attachment_id) }
      : null;
  return (
    <>
      {button && (
        <button type="button" className="file-action" disabled={busy} onClick={() => void act(button.open)}>
          {button.label}
        </button>
      )}
      {problem && <span className="file-state">{problem}</span>}
    </>
  );
}
