// The page's side of message files: the three routes JARVIS serves for them
// (jarvis/messaging_files.py) and how a file card words each state. The
// queue's composer and file cards call these; nothing here renders.
//
// A picked file goes to JARVIS as its own bytes (the browser streams the
// File; no path, no base64), with its name in X-Filename (percent-encoded,
// since a header is Latin-1) and its type as Content-Type. JARVIS answers
// with the attachment's LocalFile; saving the draft with that attachment id
// is what adds it to the message. Received files stay inert: Download
// fetches and verifies, and only a verified file can be revealed in Finder.
import { ERRORS, LIMITS, LOCAL_ERRORS } from "./messagingContract";
import type { FileState, LocalFile } from "./messagingContract";

const PREFIX = "/api/team/messages";

export class FileCallError extends Error {
  constructor(message: string, readonly code: string, readonly status: number) { super(message); }
}

function shown(code: string): string | undefined {
  return (ERRORS as Record<string, { shown: string }>)[code]?.shown
    ?? (LOCAL_ERRORS as Record<string, { shown: string }>)[code]?.shown;
}

async function post(url: string, init: RequestInit = {}): Promise<Response> {
  const res = await fetch(url, { method: "POST", ...init });
  if (!res.ok) {
    let code = "";
    try { code = (await res.json())?.error?.code ?? ""; } catch { /* not JSON */ }
    throw new FileCallError(shown(code) ?? `The request failed (${res.status}).`, code, res.status);
  }
  return res;
}

/** Copy one picked file privately on JARVIS for this draft. Refused here,
 * before any byte moves, when it is larger than one file may be. */
export async function addFile(messageId: string, file: File): Promise<LocalFile> {
  if (file.size > LIMITS.FILE_MAX_BYTES) {
    throw new FileCallError(ERRORS.too_large.shown, "too_large", ERRORS.too_large.status);
  }
  const res = await post(`${PREFIX}/drafts/${encodeURIComponent(messageId)}/files`, {
    body: file,
    headers: { "X-Filename": encodeURIComponent(file.name),
               "Content-Type": file.type || "application/octet-stream" },
  });
  return res.json();
}

const filePath = (messageId: string, attachmentId: string) =>
  `${PREFIX}/${encodeURIComponent(messageId)}/files/${encodeURIComponent(attachmentId)}`;

/** Fetch and verify a received file; resolves with its state afterwards. */
export async function downloadFile(messageId: string, attachmentId: string): Promise<LocalFile> {
  return (await post(`${filePath(messageId, attachmentId)}/download`)).json();
}

/** Show a verified download in Finder. JARVIS never opens it. */
export async function revealFile(messageId: string, attachmentId: string): Promise<void> {
  await post(`${filePath(messageId, attachmentId)}/reveal`);
}

/** What a file card says for each state; the send's own state is shown
 * separately, as is a failure's reason (`fileFailure`). */
export const FILE_STATE_SHOWN: Record<FileState, string> = {
  staged: "Ready to send",
  uploading: "Uploading",
  available: "On the team server",
  downloading: "Downloading",
  verified: "Downloaded and checked",
  failed: "Failed",
  missing: "Missing — attach it again",
};

export function fileFailure(file: LocalFile): string | null {
  return file.failure ? shown(file.failure) ?? "The transfer failed." : null;
}

export function fileProgress(file: LocalFile, sizeBytes: number): number | null {
  if (file.state !== "uploading" && file.state !== "downloading") return null;
  return sizeBytes > 0 ? Math.min(1, file.bytes_done / sizeBytes) : null;
}

/** The limits the picker shows. */
export function fileLimitsShown(): string {
  const mib = (n: number) => `${Math.round(n / (1024 * 1024))} MB`;
  return `Up to ${LIMITS.ATTACHMENTS_MAX} files, ${mib(LIMITS.FILE_MAX_BYTES)} each, ` +
    `${mib(LIMITS.PACKAGE_MAX_BYTES)} in all.`;
}
