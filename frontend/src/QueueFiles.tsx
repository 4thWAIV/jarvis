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
 *
 * A file on an accepted message is a `queueAttachment` target; its
 * Download, Retry, location, Open page and archive contents are parts of it.
 */

import { useState, useSyncExternalStore } from "react";
import { BundleInventory } from "./BundleInventory";
import { openPackagePage, openReceivedPackage, packageFrom, packageList } from "./packages";
import { downloadFile, messagingSaid } from "./queue";
import { FileLocation } from "./QueueFileLocation";
import { formatPercent, SaidText, saidShown, t as tNow, useT, type Said } from "./locale";
import type { AttachmentRef, LocalFile, OpenabilityReport } from "./messagingContract";
import { bundleWords } from "./bundles";
import type { Reason } from "./bundleContract";
import { QueueIcon } from "./QueueIcons";
import { sizeText } from "./queueStore";
import { CENTRE_SOCKET } from "./ConversationView";
import type { PartRef } from "./QueueFileLocation";
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
    case undefined: return "";
    case "staged": return "";
    case "uploading": return moving("uploading", file, ref);
    case "available": return "";
    case "downloading": return moving("downloading", file, ref);
    case "verified": return "";
    case "failed": return tNow("main.queue.file.failed", { reason: saidShown(messagingSaid(file.failure ?? "transport",
      { words: () => tNow("main.queue.file.incomplete") })).text });
    case "missing": return incoming ? tNow("main.queue.file.missing") : tNow("main.queue.file.missingAttach");
  }
}

/** What the recipient's JARVIS reported when it opened a sent file: shown
 *  only once there is a report, with the first member that failed. */
function OpenedThere({ report }: { report: OpenabilityReport }) {
  useT();
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
  const t = useT();
  const state = file?.state ?? (incoming ? "available" : "staged");
  const words = stateWords(file, fileRef, incoming);
  const canDownload = incoming && messageId && (state === "available" || state === "failed");
  // A file on an accepted message can be pointed at; a draft's is not yet one.
  const ui = useUiTarget(messageId ? {
    ref: { region: "main", view: "main.queue", kind: "queueAttachment", key: fileRef.attachment_id },
    label: t("main.queue.file.label"), socket: CENTRE_SOCKET } : null);
  return (
    <div className={`queue-file state-${state}`} data-attachment-id={fileRef.attachment_id}
         ref={(el) => { ui.whole(el); ui.part("entry")(el); }}>
      <QueueIcon kind="files" size={16} />
      <span className="file-name" title={fileRef.media_type}>{fileRef.filename}</span>
      <span className="file-meta">
        {sizeText(fileRef.size_bytes)}{showType && ` · ${fileRef.media_type}`}
      </span>
      {words && <span className="file-state">{words}</span>}
      {!incoming && report && <OpenedThere report={report} />}
      {canDownload && (
        <button data-ui-action="file" type="button" className="file-action" ref={ui.part("download")}
                onClick={() => void downloadFile(messageId!, fileRef.attachment_id)}>
          {state === "failed" ? t("main.queue.file.tryAgain") : t("main.queue.file.download")}
        </button>
      )}
      {incoming && messageId && state === "verified" && (
        <FileLocation messageId={messageId} attachmentId={fileRef.attachment_id} part={ui.part} />
      )}
      {incoming && messageId && state === "verified" && <OpenPackage messageId={messageId} fileRef={fileRef} part={ui.part} />}
      {incoming && messageId && state === "verified" && (
        <BundleInventory file={{ messageId, attachmentId: fileRef.attachment_id }} filename={fileRef.filename}
                         part={ui.part} />
      )}
      {onRemove && (
        <button data-ui-action="file" type="button" className="file-action" onClick={onRemove}
                aria-label={t("main.queue.file.removeNamed", { name: fileRef.filename })}>{t("main.queue.file.remove")}</button>
      )}
    </div>
  );
}

/** A zip, by its declared type or its name: what a work package comes as. */
function isZip(ref: AttachmentRef): boolean {
  return ref.media_type === "application/zip" || ref.filename.toLowerCase().endsWith(".zip");
}

/** Open page, for a file JARVIS has unpacked as a page work package (it
 *  runs `package.open`); Open package, for a zip not unpacked yet (a page
 *  package then opens at once). */
function OpenPackage({ messageId, fileRef, part }: { messageId: string; fileRef: AttachmentRef; part: PartRef }) {
  const t = useT();
  const list = packageList();
  const packages = useSyncExternalStore(list.subscribe, list.get).value;
  const [problem, setProblem] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const unpacked = packageFrom(packages, fileRef.attachment_id);
  const act = async (open: () => Promise<Said | null>) => {
    setBusy(true);
    setProblem(await open());
    setBusy(false);
  };
  return (
    <>
      {unpacked?.runs === "page" ? (
        <button type="button" className="file-action" disabled={busy} ref={part("openPage")}
                data-ui-command="package.open" data-ui-args={JSON.stringify({ packageId: unpacked.id })}
                onClick={() => void act(() => openPackagePage(unpacked.id))}>
          {t("main.queue.file.openPage")}
        </button>
      ) : packages && !unpacked && isZip(fileRef) && (
        <button data-ui-action="domain" type="button" className="file-action" disabled={busy} ref={part("openPackage")}
                onClick={() => void act(() => openReceivedPackage(messageId, fileRef.attachment_id))}>
          {t("main.package.open")}
        </button>
      )}
      {problem && <SaidText said={problem} className="file-state" />}
    </>
  );
}
