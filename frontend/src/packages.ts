/**
 * Work packages on the page (packages_api.py): the packages unpacked here,
 * and the one page package open in the centre area (PackageView.tsx).
 *
 * A page package opens one way, whoever asks: the page's Open page (on its
 * file in the Queue, QueueFiles.tsx) and the brain's `open_package_page`
 * both reach the server's `_open_package_page`, which tells every JARVIS
 * page to show it with a `package_page` frame on the voice socket
 * (jarvis.ts). The page's own request opens it here at once as well.
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
  /** The start of the attachment id it was unpacked from. */
  source: string;
}

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

/** The page package unpacked from this attachment, if there is one. */
export function pagePackageFrom(list: PackageInfo[] | null, attachmentId: string) {
  const source = attachmentId.replace(/-/g, "").slice(0, 8);
  return list?.find((p) => p.runs === "page" && p.source === source);
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
