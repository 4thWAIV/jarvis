/**
 * Work packages on the page (packages_api.py): the packages unpacked here,
 * and the one page package open in the centre area (PackageView.tsx).
 *
 * A received package is unpacked one way, whoever asks: the Queue's Open
 * package (on its file, QueueFiles.tsx) and the brain's `open_package` both
 * reach the server's `_open_received_package`. A package zip on this Mac
 * likewise: the Folders view's Open package (FolderDetail.tsx) and the
 * brain's `open_local_package` both reach `_open_local_package`.
 *
 * The server holds the page package shown, so a page that connects later (a
 * reload) is sent it too; `package.close` (the view's ×, or the brain's
 * navigation) tells the server to forget it.
 *
 * A page package opens one way, whoever asks: the page's Open page (on its
 * file in the Queue, QueueFiles.tsx) and the brain's `open_package_page`
 * both reach the server's `_open_package_page`, which tells every JARVIS
 * page to show it with a `package_page` frame on the voice socket
 * (jarvis.ts). The page's own request opens it here at once as well.
 *
 * Showing it is the centre's `package` detail (uiNavigation.ts): the
 * registered `package.open` and `package.close` commands are the only
 * way it opens or closes, run by Open page, by Close, by the frame (as the
 * brain's: the server sends it for the brain's tool, and a page that asked
 * itself already shows it) and by the brain's own navigation.
 *
 * A page package with a story is told one way too: the page's Tell the story
 * and Stop and the brain's `tell_package_story` both reach the server's
 * `_package_story` (story_teller.py). JARVIS's voice reads it, and each
 * beat's cue rides on its audio frames: `storyCue` holds the beat whose
 * words are playing now (jarvis.ts sets it as they start, and clears it as
 * the beat's last words end or the voice stops), and the package's view
 * hands it to its frame (packageBridge.ts). `storyState` is what the server
 * last said of the story (`package_story` frames), which the view also hands
 * to the frame, so a deck shows which question JARVIS is waiting on.
 */

import { liveRead, type LiveStore } from "./liveRead";
import { t, type Said } from "./locale";
import { createStore } from "./store";
import { bind, detailSlot, registerClose, runBinding } from "./uiNavigation";
import { dispatch, registerCommand } from "./uiRegistry";

/** What a page asks in the view's head (package_inbox.page_vote): the
 *  standard vote's answer (Got it, or I have questions with his words;
 *  `question` is null when it carries none), and the questions its story
 *  declares, each with whether it applies yet and his answer. */
export interface PageVote {
  question: string | null;
  answer: "got-it" | "questions" | "later" | "none" | null;
  note: string;
  asks?: HeaderQuestion[];
}

export interface HeaderQuestion {
  id: string;
  question: string;
  context: string;
  options: { id: string; name: string; says: string }[];
  recommended: string;
  /** Asked only once another question has one of its answers (asks_when). */
  applies: boolean;
  answer: string | null;
  note: string;
}

export interface PackageInfo {
  communication?: { kind: "general" | "architecture"; title?: string; document_id?: string; revision?: string; source_revision?: string; kit_digest?: string; readable?: boolean };
  id: string;
  name: string;
  version: string;
  purpose: string;
  runs: "conversation" | "agent" | "page";
  entry: string | null;
  /** The start of the attachment id it was first unpacked from. */
  source: string;
  /** Every attachment it was opened from (an id's letters and digits,
   *  lowercase), or only the start of the first for an older package. */
  sources: string[];
  /** Its story's outline, when it tells one. */
  story: StoryOutline | null;
  /** The standard vote a page (not a deck) carries, with his answer so far
   *  (package_inbox.page_vote), or null. */
  vote?: PageVote | null;
  /** The room its page is made for: "wide" offers to take over the page.
   *  Missing from an older JARVIS's frame: the centre. */
  layout?: "centre" | "wide";
}

export interface StoryOutline {
  title: string;
  language: string;
  slides: string[];
  /** The slides that ask a question and wait for his answer. */
  asks?: number[];
}

/** A beat of a story, as its audio frames carry it (package_story.py). */
export interface StoryCue {
  package: string;
  beat: string;
  slide: number;
  targets: string[];
  actions: Record<string, string>[];
  /** The beat's last chunk: the cue ends when it has played. */
  last?: boolean;
}

/** What the server last said of a story (story_teller.py): telling,
 *  waiting for his answer to `question`, or paused (he spoke over a slide)
 *  are all running; finished and stopped are not. */
export interface StoryState {
  package: string;
  state: "telling" | "waiting" | "paused" | "finished" | "stopped";
  slide: number;
  slides: number;
  question?: string;
}

/** Whether a story in this state is running: Stop ends it. */
export function storyRunning(state: StoryState | null, id: string): boolean {
  return !!state && state.package === id &&
    (state.state === "telling" || state.state === "waiting" || state.state === "paused");
}

/** The beat whose words are playing now, or none. */
export const storyCue = createStore<StoryCue | null>(null);

/** The story the server last spoke of, or none. */
export const storyState = createStore<StoryState | null>(null);

/** Moves on every "packages" hint on /ws/sessions: one was unpacked. */
export const packagesChanged = createStore(0);

const packageSlot = detailSlot<PackageInfo>("package", (a, b) => a.id === b.id, (info) => info.id);
/** The page package shown in the centre area, or none. Read-only: the
 *  controls run `package.open` and `package.close`. */
export const openPackage = packageSlot.value;

/** Packages the server has described in a `package_page` frame, by id,
 *  for one that is not in the list read yet. */
const described = new Map<string, PackageInfo>();

const base = (id: string) => `/api/packages/${encodeURIComponent(id)}`;

async function answer<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(body.error ?? `the server answered ${response.status}`));
  return body as T;
}

let packages: LiveStore<PackageInfo[]> | null = null;

/** The packages unpacked here, read once something asks and again on
 *  every "packages" hint. */
export function packageList(): LiveStore<PackageInfo[]> {
  packages ??= liveRead(async () =>
    (await answer<{ packages: PackageInfo[] }>(await fetch("/api/packages"))).packages,
  [packagesChanged]);
  return packages;
}

/** The package unpacked from this attachment, if there is one. */
export function packageFrom(list: PackageInfo[] | null, attachmentId: string) {
  const key = attachmentId.replace(/[^A-Za-z0-9]/g, "").toLowerCase();
  return list?.find((p) => p.sources.some((s) => s && key.startsWith(s)));
}

/** A page package this page knows by id. */
function pagePackage(id: string): PackageInfo | undefined {
  const found = described.get(id) ?? packages?.get().value?.find((p) => p.id === id);
  return found?.runs === "page" ? found : undefined;
}

registerCommand("package.open", {
  availability: ({ packageId }) => pagePackage(packageId) ? { available: true } : { available: false, reason: "no-entity" },
  execute: ({ packageId }) => ({ status: "completed", effects: packageSlot.show(pagePackage(packageId)!) }),
});

registerClose("package.close", packageSlot, (shown) => {
  void fetch(`${base(shown.id)}/page`, { method: "DELETE" }).catch(() => undefined);
});

/** The server's words, kept as they came. */
const original = (e: unknown): Said => ({ original: (e as Error).message });

/** Unpacked, but it runs another way than as a page: ask JARVIS. */
function unpackedElsewhere(info: PackageInfo): Said {
  return { words: () => t(info.runs === "agent" ? "main.package.unpackedAgent" : "main.package.unpackedConversation",
                          { id: info.id }) };
}

/** The server's `package_closed` frame: he signed it off, so every page
 *  showing it closes it. */
export function packageClosed(id: string): void {
  if (openPackage.get()?.id === id) void dispatch("package.close", {}, { actor: "brain" });
}

/** The server's `package_page` frame: show it, unless it is already
 *  showing. */
export function showPackage(info: PackageInfo): void {
  described.set(info.id, info);
  if (openPackage.get()?.id !== info.id) void dispatch("package.open", { packageId: info.id }, { actor: "brain" });
}

/** A page package the server has just opened on his click: shown here at
 *  once, as his own navigation (the inbox's Open, and Open page). */
export function showOpenedPackage(info: PackageInfo): void {
  described.set(info.id, info);
  runBinding(bind("package.open", { packageId: info.id }));
}

/** The page's Open page: the server's one way to open it, then shown here
 *  at once. Null once it is open, else why not. */
export async function openPackagePage(id: string): Promise<Said | null> {
  try {
    const info = (await answer<{ package: PackageInfo }>(
      await fetch(`${base(id)}/page`, { method: "POST" }))).package;
    showOpenedPackage(info);
    return null;
  } catch (e) {
    return original(e);
  }
}

/** The Queue's Open package: unpack a received zip here, through the
 *  server's `_open_received_package` (the brain's `open_package` too), and
 *  show it at once when it runs as a page. Null once done, else why not; a
 *  package that runs another way says to ask JARVIS. */
export async function openReceivedPackage(messageId: string, attachmentId: string): Promise<Said | null> {
  try {
    const { package: info } = await answer<{ package: PackageInfo }>(await fetch("/api/packages/received", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId, attachment_id: attachmentId }),
    }));
    packagesChanged.set(packagesChanged.get() + 1);
    if (info.runs === "page") return openPackagePage(info.id);
    return unpackedElsewhere(info);
  } catch (e) {
    return original(e);
  }
}

/** The Folders view's Open package: a package zip on this Mac, by its
 *  path, unpacked here; a page package then shows at once (the server's
 *  `package_page` frame, and here). Null once done, else why not. */
export async function openLocalPackage(path: string): Promise<Said | null> {
  try {
    const got = await answer<{ package: PackageInfo; problem: string | null }>(await fetch("/api/packages/local", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }),
    }));
    packagesChanged.set(packagesChanged.get() + 1);
    if (got.package.runs === "page") {
      if (!got.problem) showPackage(got.package);
      return got.problem ? { original: got.problem } : null;
    }
    return unpackedElsewhere(got.package);
  } catch (e) {
    return original(e);
  }
}

/** Tell its story from `slide` (from 1), resume it from the first question
 *  with no answer, or stop it: null once done, else why not. */
export async function tellStory(id: string, action: "tell" | "resume" | "stop",
                                slide?: number): Promise<Said | null> {
  try {
    await answer(await fetch(`${base(id)}/story`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(slide ? { action, slide } : { action }),
    }));
    return null;
  } catch (e) {
    return original(e);
  }
}

/** Where its entry is served, to its frame. */
export function entryUrl(info: PackageInfo): string {
  return packageFileUrl(info, info.entry ?? "");
}

/** What a package asks in its head now, as the server reads it again after
 *  an answer (a follow-up may apply once its parent is answered). */
export async function packageVote(id: string): Promise<PageVote | null> {
  const list = (await answer<{ packages: PackageInfo[] }>(await fetch("/api/packages"))).packages;
  return list.find((p) => p.id === id)?.vote ?? null;
}

/** A package's Markdown member, read through the same route as its frame's
 *  files: for an architecture package, through the canonical gate at its
 *  exact revision (language_projection.checked_member). The text, or an
 *  Error saying why not. */
export async function readPackageText(info: PackageInfo, relative: string,
                                      signal?: AbortSignal): Promise<string> {
  const response = await fetch(packageFileUrl(info, relative), { signal });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(String(body.error ?? `the server answered ${response.status}`));
  }
  return response.text();
}

export function packageFileUrl(info: PackageInfo, relative: string): string {
  const path = relative.split("/").map(encodeURIComponent).join("/");
  const revision = info.communication?.kind === "architecture" ? info.communication.revision : undefined;
  return `${base(info.id)}/files/${path}${revision ? `?canonical_revision=${encodeURIComponent(revision)}` : ""}`;
}

/** Save `data` as results/<name> in the package's folder. */
export async function saveResult(id: string, name: string,
                                 data: Blob): Promise<{ saved: string; bytes: number }> {
  return answer(await fetch(`${base(id)}/results?name=${encodeURIComponent(name)}`, {
    method: "POST", body: data, headers: { "Content-Type": "application/octet-stream" },
  }));
}
