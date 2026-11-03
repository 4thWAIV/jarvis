/**
 * Work packages on the page (packages_api.py): the packages unpacked here,
 * and the one page package open in the centre area (PackageView.tsx).
 *
 * A received package is unpacked one way, whoever asks: the Queue's Open
 * package (on its file, QueueFiles.tsx) and the brain's `open_package` both
 * reach the server's `_open_received_package`.
 *
 * A page package opens one way, whoever asks: the page's Open page (on its
 * file in the Queue, QueueFiles.tsx) and the brain's `open_package_page`
 * both reach the server's `_open_package_page`, which tells every JARVIS
 * page to show it with a `package_page` frame on the voice socket
 * (jarvis.ts). The page's own request opens it here at once as well.
 *
 * A page package with a story is told one way too: the page's Tell the story
 * and Stop and the brain's `tell_package_story` both reach the server's
 * `_package_story` (story_teller.py). JARVIS's voice reads it, and each
 * beat's cue rides on its audio frames: `storyCue` holds the beat whose
 * words are playing now (jarvis.ts sets it as they start, and clears it as
 * the beat's last words end or the voice stops), and the package's view
 * hands it to its frame (packageBridge.ts). `storyState` is what the server
 * last said of the story (`package_story` frames).
 */

import { liveRead, type LiveStore } from "./liveRead";
import { createStore } from "./store";

export interface PackageInfo {
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
}

export interface StoryOutline {
  title: string;
  language: string;
  slides: string[];
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

/** What the server last said of a story (story_teller.py). */
export interface StoryState {
  package: string;
  state: "telling" | "finished" | "stopped";
  slide: number;
  slides: number;
}

/** The beat whose words are playing now, or none. */
export const storyCue = createStore<StoryCue | null>(null);

/** The story the server last spoke of, or none. */
export const storyState = createStore<StoryState | null>(null);

/** Moves on every "packages" hint on /ws/sessions: one was unpacked. */
export const packagesChanged = createStore(0);

/** The page package shown in the centre area, or none. */
export const openPackage = createStore<PackageInfo | null>(null);

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

/** Show it, unless it is already showing. */
export function showPackage(info: PackageInfo): void {
  if (openPackage.get()?.id !== info.id) openPackage.set(info);
}

/** The page's Open page: the server's one way to open it. Null once it
 *  is open, else why not. */
export async function openPackagePage(id: string): Promise<string | null> {
  try {
    showPackage((await answer<{ package: PackageInfo }>(
      await fetch(`${base(id)}/page`, { method: "POST" }))).package);
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

/** The Queue's Open package: unpack a received zip here, through the
 *  server's `_open_received_package` (the brain's `open_package` too), and
 *  show it at once when it runs as a page. Null once done, else why not; a
 *  package that runs another way says to ask JARVIS. */
export async function openReceivedPackage(messageId: string, attachmentId: string): Promise<string | null> {
  try {
    const { package: info } = await answer<{ package: PackageInfo }>(await fetch("/api/packages/received", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId, attachment_id: attachmentId }),
    }));
    packagesChanged.set(packagesChanged.get() + 1);
    if (info.runs === "page") return openPackagePage(info.id);
    return `Unpacked as ${info.id}. It runs as ${info.runs === "agent" ? "an agent" : "a conversation"}: ask JARVIS to run it.`;
  } catch (e) {
    return (e as Error).message;
  }
}

/** Tell its story from `slide` (from 1), or stop it: null once done, else
 *  why not. */
export async function tellStory(id: string, action: "tell" | "stop",
                                slide?: number): Promise<string | null> {
  try {
    await answer(await fetch(`${base(id)}/story`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(slide ? { action, slide } : { action }),
    }));
    return null;
  } catch (e) {
    return (e as Error).message;
  }
}

/** Where its entry is served, to its frame. */
export function entryUrl(info: PackageInfo): string {
  const path = (info.entry ?? "").split("/").map(encodeURIComponent).join("/");
  return `${base(info.id)}/files/${path}`;
}

/** Save `data` as results/<name> in the package's folder. */
export async function saveResult(id: string, name: string,
                                 data: Blob): Promise<{ saved: string; bytes: number }> {
  return answer(await fetch(`${base(id)}/results?name=${encodeURIComponent(name)}`, {
    method: "POST", body: data, headers: { "Content-Type": "application/octet-stream" },
  }));
}
