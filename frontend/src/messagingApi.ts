/**
 * The page's one way to JARVIS's messaging routes (jarvis/messaging_ports.py
 * lists them; messaging_api.py serves them). Everything the Queue reads or
 * asks for goes through `httpMessagingApi`, which calls the local routes
 * behind the existing web boundary (the page never holds the team server's
 * credential); no address or setting on the page changes that.
 *
 * A refusal is the team server's error body, `{"error": {"code", "message",
 * "latest"?}}`; it arrives here as a `MessagingError` whose `shown` is the
 * contract's own wording for its code.
 *
 * Approving is not here: the inline review's Send is the existing
 * `POST /api/capture/{n}/send` (capture.ts) on the capture the review opened.
 */

import {
  ERRORS, LOCAL_ERRORS, type Draft, type DraftSave, type HistoryPage, type LocalFile,
  type LocalItem, type LocalMessage, type QueueSnapshot, type RecipientList,
  type WorkCommand, type WorkCommandResult,
} from "./messagingContract";

export class MessagingError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
    message: string,
    /** The current record, on a `revision_conflict`. */
    readonly latest: unknown = null,
  ) {
    super(message);
  }

  /** The contract's words for this refusal. */
  get shown(): string {
    return shownFor(this.code, this.message);
  }
}

/** What the owner is told about a refusal with this code. */
export function shownFor(code: string, fallback: string): string {
  return (ERRORS as Record<string, { shown: string }>)[code]?.shown
    ?? (LOCAL_ERRORS as Record<string, { shown: string }>)[code]?.shown
    ?? fallback;
}

export interface MessagingApi {
  /** Null when this JARVIS has no messaging routes at all (an older
   *  backend): the Queue says so and does not keep asking. */
  snapshot(): Promise<QueueSnapshot | null>;
  history(continuation: string | null, limit: number): Promise<HistoryPage>;
  recipients(): Promise<RecipientList>;
  item(threadId: string): Promise<LocalItem>;
  workCommand(threadId: string, change: WorkCommand): Promise<WorkCommandResult>;
  createDraft(save: DraftSave): Promise<Draft>;
  saveDraft(messageId: string, save: DraftSave): Promise<Draft>;
  discardDraft(messageId: string, expectedDraftRevision: number): Promise<void>;
  review(messageId: string, expectedDraftRevision: number): Promise<Draft>;
  cancel(messageId: string, expectedDraftRevision: number): Promise<Draft>;
  retry(messageId: string, expectedDraftRevision: number): Promise<Draft>;
  addFile(messageId: string, file: File): Promise<LocalFile>;
  read(messageId: string): Promise<LocalMessage>;
  download(messageId: string, attachmentId: string): Promise<LocalFile>;
  reveal(messageId: string, attachmentId: string): Promise<void>;
}

const BASE = "/api/team/messages";

async function refused(res: Response): Promise<MessagingError> {
  const body = await res.json().catch(() => null);
  const error = body?.error;
  if (error && typeof error.code === "string") {
    return new MessagingError(error.code, res.status, String(error.message ?? error.code),
                              error.latest ?? null);
  }
  return new MessagingError("transport", res.status, `JARVIS answered ${res.status}`);
}

async function call<T>(method: string, path: string, body?: unknown,
                       headers: Record<string, string> = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body === undefined || body instanceof Blob
        ? headers : { "Content-Type": "application/json", ...headers },
      body: body === undefined ? undefined : body instanceof Blob ? body : JSON.stringify(body),
    });
  } catch {
    throw new MessagingError("transport", 0, "JARVIS is not answering");
  }
  if (!res.ok) throw await refused(res);
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const id = encodeURIComponent;

export const httpMessagingApi: MessagingApi = {
  async snapshot() {
    try {
      return await call<QueueSnapshot>("GET", "");
    } catch (e) {
      if (e instanceof MessagingError && e.status === 404 && e.code === "transport") return null;
      throw e;
    }
  },
  history: (continuation, limit) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (continuation) q.set("continuation", continuation);
    return call("GET", `/history?${q}`);
  },
  recipients: () => call("GET", "/recipients"),
  item: (threadId) => call("GET", `/threads/${id(threadId)}`),
  workCommand: (threadId, change) =>
    call("PUT", `/threads/${id(threadId)}/work-state`, { change }),
  createDraft: (save) => call("POST", "/drafts", save),
  saveDraft: (messageId, save) => call("PUT", `/drafts/${id(messageId)}`, save),
  discardDraft: (messageId, expected) =>
    call("DELETE", `/drafts/${id(messageId)}?expected_draft_revision=${expected}`),
  review: (messageId, expected) =>
    call("POST", `/drafts/${id(messageId)}/review`, { expected_draft_revision: expected }),
  cancel: (messageId, expected) =>
    call("POST", `/drafts/${id(messageId)}/cancel`, { expected_draft_revision: expected }),
  retry: (messageId, expected) =>
    call("POST", `/drafts/${id(messageId)}/retry`, { expected_draft_revision: expected }),
  // The file's bytes as they are; its name percent-encoded, since a header
  // carries only ASCII (the files route decodes it).
  addFile: (messageId, file) =>
    call("POST", `/drafts/${id(messageId)}/files`, file, {
      "X-Filename": encodeURIComponent(file.name),
      "Content-Type": file.type || "application/octet-stream",
    }),
  read: (messageId) => call("POST", `/${id(messageId)}/read`),
  download: (messageId, attachmentId) =>
    call("POST", `/${id(messageId)}/files/${id(attachmentId)}/download`),
  reveal: (messageId, attachmentId) =>
    call("POST", `/${id(messageId)}/files/${id(attachmentId)}/reveal`),
};

/** The adapter every read and command uses. */
export function messagingApi(): MessagingApi {
  return httpMessagingApi;
}
