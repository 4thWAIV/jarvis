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
 * Showing it is the centre's `package` detail (uiNavigation.ts), taking
 * over the page as it opens (`takeOverShown`): the
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

import { registerRevealer } from "./uiNavigationReveal";
import { liveRead, type LiveStore } from "./liveRead";
import { t, type Said } from "./locale";
import { createStore } from "./store";
import { bind, detailSlot, registerClose, runBinding, takeOverShown } from "./uiNavigation";
import { dispatch, registerCommand } from "./uiRegistry";

/** What a page asks in the view's head (package_inbox.page_vote): the
 *  standard vote's answer (Approve, or Reject with his words saying what to
 *  change; `question` is null when it carries none), and the questions its
 *  story declares, each with whether it applies yet and his answer. An
 *  answer given before Got it was retired comes as any other id, read back
 *  by `answer_name` and never asked again. */
export interface PageVote {
  question: string | null;
  /** The vote's own question and why it is his, heading its card. */
  words?: string;
  reason?: string;
  answer: "approve" | "reject" | "later" | "none" | string | null;
  /** His answer by its name, as recorded ("" while unanswered). */
  answer_name?: string;
  note: string;
  asks?: HeaderQuestion[];
}

export interface HeaderQuestion {
  id: string;
  question: string;
  context: string;
  /** Why it is his to answer (the story's `reason`). */
  reason?: string;
  options: { id: string; name: string; says: string }[];
  recommended: string;
  /** Asked only once another question has one of its answers (asks_when). */
  applies: boolean;
  answer: string | null;
  note: string;
  /** "approval": his approval of an architecture design (never the vote),
   *  with the revision it puts to him, the options that need his words, and
   *  whether his answer was given to a revision or kit no longer in force. */
  kind?: "approval";
  revision?: string | null;
  note_required?: string[];
  stale?: boolean;
  answered_revision?: string | null;
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
  /** A deck's own slides in its order and numbering (a decision's lead-in
   *  and its question each one), which the border moves through; null for
   *  a page that is not a deck, whose border moves through its story. */
  pages?: DeckPage[] | null;
  /** The standard vote a page (not a deck) carries, with his answer so far
   *  (package_inbox.page_vote), or null. */
  vote?: PageVote | null;
  /** The room its page is made for: "wide" offers to take over the page.
   *  Missing from an older JARVIS's frame: the centre. */
  layout?: "centre" | "wide";
}

export interface DeckPage {
  /** The control that brings the slide up in the deck ("#go-3"). */
  opener: string;
  title: string;
  /** The decision the slide asks, or null. */
  asks: string | null;
}

/** What the border moves through: a deck's own slides, else its story's
 *  slides, each with the opener that brings it up. */
export function navigation(info: PackageInfo): DeckPage[] {
  if (info.pages?.length) return info.pages;
  const story = info.story;
  if (!story) return [];
  return story.slides.map((title, i) => ({ opener: story.openers?.[i]?.[0] ?? "", title,
                                           asks: story.asks?.includes(i + 1) ? title : null }));
}

/** The story slide that tells navigation slide `page` (1-based): the one
 *  whose openers hold its opener, else the same number. */
export function storySlideOf(info: PackageInfo, page: number): number {
  if (!info.pages?.length) return page;
  const opener = info.pages[page - 1]?.opener;
  const found = (info.story?.openers ?? []).findIndex((list) => opener !== undefined && list.includes(opener)) + 1;
  return found || 1;
}

export interface StoryOutline {
  title: string;
  language: string;
  slides: string[];
  /** The slides that ask a question and wait for his answer. */
  asks?: number[];
  /** For each question a slide waits on, the part of the page it is about
   *  (that slide's targets; empty: the whole page). PackageFocus.tsx lights
   *  it while the question is his. */
  focus?: Record<string, string[]>;
  /** What each slide's beats click, in order (a deck's #go-N): the border
   *  opens a slide with its first, and counts itself on the slide whose
   *  openers include the one the page says it shows (`slide.shown`). */
  openers?: string[][];
}

/** A beat of a story, as its audio frames carry it (package_story.py). */
export interface StoryCue {
  package: string;
  beat: string;
  slide: number;
  /** The beat's words, as JARVIS speaks them (shown beside its targets). */
  say?: string;
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
  /** The beat of the slide being heard, or to go on from. */
  beat?: number;
  question?: string;
}

/** What a package's frame shows (package_place.View): the deck slide on
 *  screen (its opener), where each scrolled part is, the part he selected
 *  with its words and his selection in them, and what had keyboard focus.
 *  The frame's helper reports it (package_story.js `place`). */
export interface FrameView {
  page: string | null;
  scroll: { at: string; x: number; y: number }[];
  part: string | null;
  words: string;
  selection: [number, number] | null;
  selected: string;
  focused: string | null;
}

/** A place he asked from (story_teller.py's Ask), as the server tells the
 *  page: the last is the one Return goes back to. */
export interface AskedPlace {
  package: string;
  state: string;
  slide: number | null;
  beat: number | null;
  speaking: boolean;
  page: string | null;
}

/** Going back to a place (the server's `package_return`): the view to bring
 *  up in its frame, and what the story had revealed there first. */
export interface ReturnTo {
  package: string;
  view: FrameView;
  revealed: Record<string, string>[];
  seq: number;
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

/** The places he can go back to, the last on top (`package_asks`). */
export const packageAsks = createStore<AskedPlace[]>([]);

/** A place being gone back to, until its frame has brought it up. */
export const returnTo = createStore<ReturnTo | null>(null);
let returns = 0;

/** The server's `package_return` frame. */
export function returnToPlace(msg: Omit<ReturnTo, "seq">): void {
  returnTo.set({ ...msg, seq: ++returns });
}

/** Moves on every "packages" hint on /ws/sessions: one was unpacked. */
export const packagesChanged = createStore(0);

const packageSlot = detailSlot<PackageInfo>("package", (a, b) => a.id === b.id, (info) => info.id);
/** The page package shown in the centre area, or none. Read-only: the
 *  controls run `package.open` and `package.close`. */
export const openPackage = packageSlot.value;

/** Mounted border actions are the same handlers for clicks and brain navigation.
 * Nothing here starts narration or records an answer. Unmounting removes the
 * adapter so a request cannot act on an old package. */
export interface PackageControls {
  packageId: string;
  slides: number;
  presentation: boolean;
  menu(show: "chapters" | "more" | "closed"): void;
  select(slide: number, closeMenu: boolean): void;
  inspect(): void;
  document(enabled: boolean): void;
}
let controls: PackageControls | null = null;
export function mountPackageControls(value: PackageControls): () => void {
  controls = value;
  return () => { if (controls === value) controls = null; };
}
const currentControls = () => controls?.packageId === openPackage.get()?.id ? controls : null;
const availableControls = () => ({ available: !!currentControls(), reason: "not-mounted" as const });
registerCommand("package.menu", {
  availability: ({ show }) => show === "chapters" && !currentControls()?.slides
    ? { available: false, reason: "no-entity" } : availableControls(),
  execute: ({ show }) => { currentControls()!.menu(show); return { status: "completed", effects: [] }; },
});
registerCommand("package.slide", {
  availability: ({ slide }) => currentControls() && slide <= currentControls()!.slides
    ? { available: true } : { available: false, reason: "no-entity" },
  execute: ({ slide, closeMenu = false }) => { currentControls()!.select(slide, closeMenu); return { status: "completed", effects: [] }; },
});
registerCommand("package.inspect", {
  availability: () => currentControls()?.presentation ? { available: true } : { available: false, reason: "no-entity" },
  execute: () => { currentControls()!.inspect(); return { status: "completed", effects: [] }; },
});
registerCommand("package.document", {
  availability: () => currentControls()?.presentation ? { available: true } : { available: false, reason: "no-entity" },
  execute: ({ enabled }) => { currentControls()!.document(enabled); return { status: "completed", effects: [] }; },
});

registerRevealer("main.package", "packageView", async (ref, { navigate }) => {
  if (ref.key !== openPackage.get()?.id) return "no-entity";
  const menu = ref.part === "fromSlide" ? "chapters"
    : ["resume", "inspect", "document", "takeover", "return"].includes(ref.part ?? "") ? "more" : null;
  if (!menu) return "no-entity";
  return await navigate("package.menu", { show: menu }) ? true : "no-entity";
});

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
  // Shown, it takes over the page: the document uses the whole display.
  execute: ({ packageId }) => ({ status: "completed",
                                 effects: [...packageSlot.show(pagePackage(packageId)!), ...takeOverShown()] }),
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

/** Tell its story from `slide` (from 1), resume it from the place kept (else
 *  the first question with no answer), stop it, Ask (keep where he is, with
 *  what its frame shows) or Return (to the last place kept): null once done,
 *  else why not. */
export async function tellStory(id: string, action: "tell" | "resume" | "stop" | "ask" | "return",
                                slide?: number, view?: FrameView | null): Promise<Said | null> {
  try {
    await answer(await fetch(`${base(id)}/story`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, ...(slide ? { slide } : {}), ...(view ? { view } : {}) }),
    }));
    return null;
  } catch (e) {
    return original(e);
  }
}

// What a frame shows, sent to the server once it has stayed the same this
// long (an Ask sends it at once).
const VIEW_SETTLES_MS = 300;
let viewTimer: ReturnType<typeof setTimeout> | undefined;

/** Tell the server what package `id`'s frame shows now, so an Ask made by
 *  voice keeps it too; sent once it settles. */
export function reportView(id: string, view: FrameView): void {
  clearTimeout(viewTimer);
  viewTimer = setTimeout(() => {
    void fetch(`${base(id)}/view`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ view }),
    }).catch(() => undefined);
  }, VIEW_SETTLES_MS);
}

/** His Ask: keep where he is in package `id` (what its frame shows now). */
export function askAbout(id: string, view: FrameView | null): Promise<Said | null> {
  clearTimeout(viewTimer);
  return tellStory(id, "ask", undefined, view);
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
