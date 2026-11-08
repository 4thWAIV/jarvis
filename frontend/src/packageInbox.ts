/**
 * The package inbox (server: package_inbox.py, /api/packages/inbox): every
 * package an agent has sent him, from its arrival until it is closed. The
 * page keeps no account of its own: it reads the record again on every
 * `packages` hint (packagesChanged, sessionsSocket.ts).
 *
 * A package arrives silently: listed in the Queue's Packages section
 * (QueuePackages.tsx) and as one reduced line in Updates waiting
 * (UpdatesNotice.tsx), never shown and never spoken. Every action here is
 * his own act on it, never navigation, and reaches the server's one
 * function for it, which the brain's tool calls too:
 *   Open        → `_open_inbox_package` (his click: the route checks it);
 *                 a page package then shows in the centre (`package.open`)
 *   an answer   → `_answer_inbox_decision` (the package's own decide.py)
 *   Done        → `_sign_off_package`, for every package (the view's head
 *                 and the Queue row): his answers, or that he reviewed it,
 *                 to the session that sent it as a capture read back and
 *                 sent only on his yes or Send; its Work item told; closed,
 *                 and its view closed on every page
 *   Dismiss     → `_close_inbox_package`
 *
 * Everything a package says (its title, its questions, its sender's name)
 * is the sender's words, drawn as plain text.
 */

import { liveRead } from "./liveRead";
import { t, type Said } from "./locale";
import { packagesChanged, showOpenedPackage, type PackageInfo } from "./packages";
import { workChanged } from "./work";

export type InboxState = "arrived" | "opened" | "answering" | "answered" | "closed";

export interface InboxEntry {
  id: string;
  package_id: string;
  name: string;
  version: string;
  /** The sender's words. */
  title: string;
  purpose: string;
  runs: "conversation" | "agent" | "page";
  decisions_total: number;
  decisions_answered: number;
  /** How many of the questions that apply have no answer yet. */
  waiting: number;
  /** Who sent it: the agent's one name, else its project. */
  sender: string;
  /** It named a session nothing proved sent it: his answers go nowhere. */
  unproven_claim?: boolean;
  workflow_id: string | null;
  work_id: string | null;
  state: InboxState;
  closed_why: "signed_off" | "dismissed" | "superseded" | null;
  signed_off_at: number | null;
  arrived_at: number;
  closed_at: number | null;
}

export interface InboxOption {
  id: string;
  name: string;
  says: string;
}

/** One decision a package's deck asks, with what is recorded for it. */
export interface InboxQuestion {
  id: string;
  question: string;
  context: string;
  options: InboxOption[];
  recommended: string;
  reason: string;
  /** False for a follow-up not asked yet (its parent's answer opens it). */
  applies: boolean;
  answer: { answer: string; choice: string; note: string; at: string; recommended: boolean } | null;
}

/** A package in full: its row, the package, and its questions. */
export interface InboxDetail {
  inbox: InboxEntry;
  package: PackageInfo | null;
  questions: InboxQuestion[];
}

interface InboxList {
  packages: InboxEntry[];
  closed_count: number;
}

const base = (id: string) => `/api/packages/inbox/${encodeURIComponent(id)}`;

async function answer<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(body.error ?? `the server answered ${response.status}`));
  return body as T;
}

async function read(url: string): Promise<InboxList> {
  return answer<InboxList>(await fetch(url));
}

/** The open packages, newest first, and how many are closed. */
export const inbox = liveRead(() => read("/api/packages/inbox"), [packagesChanged]);

/** The closed ones, most recently closed first, read when shown. */
export const closedInbox = liveRead(() => read("/api/packages/inbox?closed=1"), [packagesChanged]);

/** A change was made: every view of the inbox, and a work item showing its
 *  packages, read again (the server's hint will say so too). */
function changed(): void {
  packagesChanged.set(packagesChanged.get() + 1);
  workChanged.set(workChanged.get() + 1);
}

const original = (e: unknown): Said => ({ original: (e as Error).message });

async function post<T>(url: string, body?: unknown): Promise<T> {
  return answer<T>(await fetch(url, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }));
}

/** His Open: a page package shown in the centre at once. Null once shown,
 *  else why not (a package that is not a page says to ask JARVIS). */
export async function openInbox(id: string): Promise<Said | null> {
  try {
    const got = await post<{ package: PackageInfo; problem: string | null }>(`${base(id)}/open`);
    changed();
    if (got.problem) return { original: got.problem };
    showOpenedPackage(got.package);
    return null;
  } catch (e) {
    return original(e);
  }
}

/** Record his answer to one of its decisions. The refusal's words, or null. */
export async function answerInbox(id: string, decision: string, option: string,
                                  words = ""): Promise<string | null> {
  try {
    await post(`${base(id)}/answer`, { decision, option, words });
    changed();
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

/** His Done: the package, by its own id, signed off. Null once done (the
 *  view closes and a capture to read back appears in Conversation), else
 *  why not. */
export async function signOff(packageId: string): Promise<Said | null> {
  try {
    await post(`/api/packages/${encodeURIComponent(packageId)}/signoff`);
    changed();
    return null;
  } catch (e) {
    return original(e);
  }
}

/** Dismiss it, or close it once its answers have gone back. */
export async function closeInbox(id: string): Promise<Said | null> {
  try {
    await post(`${base(id)}/close`);
    changed();
    return null;
  } catch (e) {
    return original(e);
  }
}

/** Where it stands, in words: "2 of 3 answered" while it asks, else its state. */
export function inboxProgress(entry: InboxEntry): string {
  if (entry.state === "closed") return t(`right.packages.closed.${entry.closed_why ?? "dismissed"}`);
  if (entry.decisions_total > 0) {
    return t("right.packages.answered", { answered: entry.decisions_answered, total: entry.decisions_total });
  }
  return t(`right.packages.state.${entry.state}`);
}

/** The answer a decision card gives, as decide.py takes it: an option's id
 *  by its name, else "None of these" with his words. */
export function optionFor(question: InboxQuestion, value: string): { option: string; words: string } {
  const found = question.options.find((o) => o.name === value || o.id === value);
  return found ? { option: found.id, words: "" } : { option: "none", words: value };
}
