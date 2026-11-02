/**
 * Files a teammate sent, read as files (jarvis/bundle_api.py): what one is,
 * what an archive holds, a member's text a page at a time, its preview and
 * its rendered pages. The brain's bundle tools make the same calls and get
 * the same JSON.
 *
 * A member opens in the centre area one way, whoever asks: the page's Open
 * (BundleInventory.tsx) and the brain's `show_bundle_member` both reach the
 * show route, which sends every JARVIS page a `bundle_view` frame on the
 * voice socket (jarvis.ts); the page's own request opens it here at once as
 * well. `bundleWords` holds every string the bundle views show, so moving
 * them into locale catalogs later is one change.
 */

import {
  ATTACHMENT_PREFIX, BUNDLE_LIMITS, REASONS_SHOWN, type Classification, type FormatId,
  type MemberPage, type OutcomeKind, type PreflightResult, type Preview, type Readiness,
  type Reason, type ReviewProgress, type ReviewState, type TextPage,
} from "./bundleContract";
import type { Draft, Openability, TaskContext } from "./messagingContract";
import { createStore } from "./store";

/** A received file, by its message and attachment ids. */
export interface FileKey {
  messageId: string;
  attachmentId: string;
}

/** A member of one, by its opaque id. */
export interface MemberKey extends FileKey {
  memberId: string;
}

/** The member shown in the centre area, or none. */
export const openBundleMember = createStore<MemberKey | null>(null);

/** Moves on every "bundles" hint on /ws/sessions: a thread's bundle state
 *  (openability, review progress) changed. */
export const bundlesChanged = createStore(0);

export const bundleWords = {
  open: "Open",
  contents: "Contents",
  close: "Close",
  more: "More",
  previous: "Previous",
  next: "Next",
  top: "Top",
  text: "Text",
  pages: "Pages",
  preview: "Preview",
  package: "Work package",
  files: (n: number) => `${n} file${n === 1 ? "" : "s"}`,
  pageOf: (n: number, of: number | null | undefined) => (of ? `${n} / ${of}` : String(n)),
  blocked: (n: number) => `${n} link${n === 1 ? "" : "s"} not loaded`,
  reading: "Reading…",
  nothing: "Nothing to read.",
  notShown: "No JARVIS page is open.",
  progress: {
    file_ready: "File ready",
    evidence_unavailable: "Some evidence unreadable",
    review_in_progress: "Review in progress",
    notes_ready: "Notes ready",
  } satisfies Record<ReviewProgress, string>,
  readiness: {
    ready: "Ready",
    partial: "Partly readable there",
    unknown: "Readability unknown",
    blocked: "Unreadable",
  } satisfies Record<Readiness, string>,
  outcome: {
    notes: "Notes",
    revised_copy: "Revised copy",
    data: "Data",
    answer: "Answer",
    other: "Request",
  } satisfies Record<OutcomeKind, string>,
  primary: "Primary",
  request: "Request",
  noRequest: "No request",
  /** The recipient's JARVIS's report on a file it was told to open. */
  openability: {
    verified: "Opens there",
    ready: "Opens there",
    partial: "Partly readable there",
    unsupported: "Can't be read there",
    corrupt: "Damaged there",
    unavailable: "Not opened there",
  } satisfies Record<Openability, string>,
  reason: (reason: Reason | null | undefined) => (reason ? REASONS_SHOWN[reason] : ""),
} as const;

/** A refused bundle call: the contract's code, its words and the member's
 *  own reason where one applies. */
export class BundleError extends Error {
  constructor(readonly code: string, readonly status: number, message: string,
              readonly reason: Reason | null = null) {
    super(message);
  }
}

const enc = encodeURIComponent;

function fileBase(f: FileKey): string {
  return ATTACHMENT_PREFIX.replace("{message_id}", enc(f.messageId))
    .replace("{attachment_id}", enc(f.attachmentId));
}

const memberBase = (m: MemberKey) => `${fileBase(m)}/bundle/members/${enc(m.memberId)}`;

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function answer<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const e = body?.error ?? {};
    throw new BundleError(String(e.code ?? "transport"), response.status,
                          String(e.message ?? `the server answered ${response.status}`),
                          (e.reason ?? null) as Reason | null);
  }
  return body as T;
}

/** What it is (it must be downloaded and checked already). */
export async function classify(f: FileKey): Promise<Classification> {
  return answer(await fetch(`${fileBase(f)}/bundle`));
}

/** One folder of an archive, a page at a time. */
export async function listMembers(f: FileKey, folder = "",
                                  continuation: string | null = null): Promise<MemberPage> {
  return answer(await fetch(`${fileBase(f)}/bundle/members${query({ folder, continuation })}`));
}

/** One page of a member's text. */
export async function readText(m: MemberKey, continuation: string | null = null): Promise<TextPage> {
  return answer(await fetch(`${memberBase(m)}/text${query({ continuation })}`));
}

export async function readPreview(m: MemberKey): Promise<Preview> {
  return answer(await fetch(`${memberBase(m)}/preview`));
}

/** Where a rendered page is served (a PDF page as PNG, an image as sent). */
export function pageUrl(m: MemberKey, page: number,
                        maxEdge: number = BUNDLE_LIMITS.RENDER_EDGE_DEFAULT): string {
  return `${memberBase(m)}/pages/${page}${query({ max_edge: maxEdge })}`;
}

/** Show it, unless it is already showing. */
export function showMember(m: MemberKey): void {
  const now = openBundleMember.get();
  if (!now || now.memberId !== m.memberId || now.attachmentId !== m.attachmentId
      || now.messageId !== m.messageId) openBundleMember.set(m);
}

/** The page's Open: the server's one way to open it, on every page. Shown
 *  here at once; null, or why not. */
export async function openMember(m: MemberKey): Promise<string | null> {
  try {
    const r = await fetch(`${memberBase(m)}/show`, { method: "POST" });
    if (!r.ok) await answer(r);
    showMember(m);
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

/** The sender's prediction for a draft's files. */
export async function readReadiness(messageId: string): Promise<PreflightResult> {
  return answer(await fetch(`/api/team/messages/drafts/${enc(messageId)}/readiness`));
}

/** The draft's one-off request (null clears it); the draft as saved. */
export async function setTask(messageId: string, expectedRevision: number,
                              task: TaskContext | null): Promise<Draft> {
  return answer(await fetch(`/api/team/messages/drafts/${enc(messageId)}/task`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expected_draft_revision: expectedRevision, task }),
  }));
}

/** The owner's own record of a review asked of him on a thread. */
export async function readReview(threadId: string): Promise<ReviewState> {
  return answer(await fetch(`/api/team/messages/threads/${enc(threadId)}/review`));
}

/** The formats shown as a table of rows. */
export const TABLE_FORMATS: ReadonlySet<FormatId> = new Set(["csv", "tsv", "xlsx"]);
/** The formats whose pages are rendered images. */
export const PAGED_FORMATS: ReadonlySet<FormatId> = new Set(["pdf"]);

/** One CSV line's cells: commas outside quotes split, doubled quotes kept
 *  as one. A TSV or a sheet's row splits at tabs. */
export function cells(line: string, format: FormatId): string[] {
  if (format !== "csv") return line.split("\t");
  const out: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ",") { out.push(cell); cell = ""; }
    else cell += ch;
  }
  out.push(cell);
  return out;
}

