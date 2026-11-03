/**
 * A downloaded file's place on this Mac: its exact path, Copy and Reveal.
 * Its state shows only when it is not here and checked (missing, changed,
 * moved, failed), with one action to download it again. Read from the one
 * resolver (attachmentLocation.ts); nothing here opens the file.
 */

import { useEffect, useState } from "react";
import { copyPath, locate, makeLocal, type AttachmentLocation } from "./attachmentLocation";
import { MessagingError, shownFor } from "./messagingApi";
import { revealFile } from "./queue";
import { QueueIcon } from "./QueueIcons";

function IconButton({ icon, label, onClick, disabled }: {
  icon: "copy" | "reveal" | "download"; label: string; onClick: () => void; disabled?: boolean;
}) {
  return (
    <button type="button" className="file-icon-action" title={label} aria-label={label}
            onClick={onClick} disabled={disabled}>
      <QueueIcon kind={icon} size={14} />
    </button>
  );
}

function problemOf(e: unknown): string {
  return e instanceof MessagingError ? e.shown : "JARVIS is not answering";
}

export function FileLocation({ messageId, attachmentId }: {
  messageId: string;
  attachmentId: string;
}) {
  const [found, setFound] = useState<AttachmentLocation | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
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

  if (problem) return <span className="file-state">{problem}</span>;
  if (!found) return null;
  const path = found.local_path;
  if (path) {
    return (
      <span className="file-location">
        <code className="file-path" title={path}>{path}</code>
        <IconButton icon="copy" label={copied ? "Copied" : "Copy path"}
                    onClick={() => void copy(path)} />
        <IconButton icon="reveal" label="Reveal in Finder"
                    onClick={() => void revealFile(messageId, attachmentId)} />
      </span>
    );
  }
  const said = found.state === "failed" && found.reason
    ? shownFor(found.reason, found.shown) : found.shown;
  return (
    <span className="file-location">
      <span className="file-state attention">{said}</span>
      {found.actions.includes("make_local") && (
        <IconButton icon="download" label="Download again" disabled={busy}
                    onClick={() => void fetchAgain()} />
      )}
    </span>
  );
}
