/**
 * A downloaded file's place on this Mac: its exact path, Copy and Reveal.
 * Its state shows only when it is not here and checked (missing, changed,
 * moved, failed), with one action to download it again. Read from the one
 * resolver (attachmentLocation.ts); nothing here opens the file.
 *
 * The path, Copy, Reveal and Download again are parts of the file's
 * `queueAttachment` target (QueueFiles.tsx), so they can be pointed at.
 */

import { useEffect, useState } from "react";
import { copyPath, locate, makeLocal, type AttachmentLocation } from "./attachmentLocation";
import { codeWords, SaidText, t as tNow, useT, type Said } from "./locale";
import { MessagingError } from "./messagingApi";
import { messagingSaid, revealFile } from "./queue";
import { QueueIcon } from "./QueueIcons";

/** A part's callback ref, from the file's registered target. */
export type PartRef = (part: string) => (el: Element | null) => void;

function IconButton({ icon, label, onClick, disabled, target }: {
  icon: "copy" | "reveal" | "download"; label: string; onClick: () => void; disabled?: boolean;
  target: (el: Element | null) => void;
}) {
  return (
    <button data-ui-action="file" type="button" className="file-icon-action" title={label} aria-label={label}
            onClick={onClick} disabled={disabled} ref={target}>
      <QueueIcon kind={icon} size={14} />
    </button>
  );
}

function problemOf(e: unknown): Said {
  return e instanceof MessagingError ? messagingSaid(e.code, { original: e.message })
    : { words: () => tNow("main.queue.location.noAnswer") };
}

/** A location that is not here and checked, in words: a failed download
 *  by its code, any other state by its own. */
function stateSaid(found: AttachmentLocation): Said {
  if (found.state === "failed" && found.reason) return messagingSaid(found.reason, { original: found.shown });
  return codeWords("main.queue.location", found.state) !== null
    ? { words: () => codeWords("main.queue.location", found.state)! } : { original: found.shown };
}

export function FileLocation({ messageId, attachmentId, part }: {
  messageId: string;
  attachmentId: string;
  part: PartRef;
}) {
  const t = useT();
  const [found, setFound] = useState<AttachmentLocation | null>(null);
  const [problem, setProblem] = useState<Said | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    locate(messageId, attachmentId)
      .then((got) => { if (live) { setFound(got); setProblem(null); } })
      .catch((e) => { if (live) setProblem(problemOf(e)); });
    return () => { live = false; };
  }, [messageId, attachmentId]);

  async function fetchAgain() {
    setBusy(true);
    try {
      setFound(await makeLocal(messageId, attachmentId));
      setProblem(null);
    } catch (e) {
      setProblem(problemOf(e));
    } finally {
      setBusy(false);
    }
  }

  async function copy(path: string) {
    setCopied(await copyPath(path));
    window.setTimeout(() => setCopied(false), 1500);
  }

  if (problem) return <SaidText said={problem} className="file-state" />;
  if (!found) return null;
  const path = found.local_path;
  if (path) {
    return (
      <span className="file-location">
        <code className="file-path" title={path} ref={part("location")}>{path}</code>
        <IconButton icon="copy" label={copied ? t("main.queue.location.copied") : t("main.queue.location.copy")}
                    onClick={() => void copy(path)} target={part("copy")} />
        <IconButton icon="reveal" label={t("main.queue.file.reveal")}
                    onClick={() => void revealFile(messageId, attachmentId)} target={part("reveal")} />
      </span>
    );
  }
  return (
    <span className="file-location" ref={part("location")}>
      <SaidText said={stateSaid(found)} className="file-state attention" />
      {found.actions.includes("make_local") && (
        <IconButton icon="download" label={t("main.queue.location.downloadAgain")} disabled={busy}
                    onClick={() => void fetchAgain()} target={part("download")} />
      )}
    </span>
  );
}
