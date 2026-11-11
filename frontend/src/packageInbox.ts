/**
 * The package inbox (server: package_inbox.py, /api/packages/inbox): every
 * package an agent has sent him, from its arrival until it is closed. The
 * page keeps no account of its own: it reads the record again on every
 * `packages` hint (packagesChanged, sessionsSocket.ts).
 *
 * A package arrives silently, as a message that carries it: in its Work
 * item's Messages tab, or in the Inbox when it belongs to no item
 * (MessageCard.tsx), never shown and never spoken. Every action here is
 * his own act on it, never navigation, and reaches the server's one
 * function for it, which the brain's tool calls too:
 *   Open        → `_open_inbox_package` (his click: the route checks it);
 *                 a page package then shows in the centre (`package.open`)
 *   an answer   → `_answer_inbox_decision` (the package's own decide.py)
 *   Done        → `_sign_off_package`, for every package (the view's head
 *                 and the brain's sign_off_package): his answers, or that he reviewed it,
 *                 to the session that sent it as a capture read back and
 *                 sent only on his yes or Send; its Work item told; closed,
 *                 and its view closed on every page
 *
 * Everything a package says (its title, its questions, its sender's name)
 * is the sender's words, drawn as plain text.
 */

import { type Said } from "./locale";
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

const base = (id: string) => `/api/packages/inbox/${encodeURIComponent(id)}`;

async function answer<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(body.error ?? `the server answered ${response.status}`));
  return body as T;
}

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
