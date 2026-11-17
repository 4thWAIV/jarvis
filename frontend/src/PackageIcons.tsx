import type { ReactNode } from "react";

export type PackageIconName = "chapters" | "previous" | "next" | "tell" | "stop" | "more" | "done" | "takeover" | "close" | "ask" | "goBack";
const shapes: Record<PackageIconName, ReactNode> = {
  chapters: <><rect x="2" y="2" width="7" height="7" rx="1"/><rect x="13" y="2" width="7" height="7" rx="1"/><rect x="2" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/></>,
  previous: <path d="m14 5-7 7 7 7"/>, next: <path d="m8 5 7 7-7 7"/>,
  tell: <path d="m7 4 13 8-13 8Z"/>, stop: <rect x="5" y="5" width="14" height="14" rx="1"/>,
  more: <><circle cx="4" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="20" cy="12" r="1"/></>,
  done: <><circle cx="12" cy="12" r="9"/><path d="m7 12 3 3 7-7"/></>,
  takeover: <path d="M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5"/>,
  close: <path d="m6 6 12 12M18 6 6 18"/>,
  ask: <><path d="M4 5h16v11H9l-5 4Z"/><path d="M10 9a2 2 0 1 1 2 2v1"/><circle cx="12" cy="14" r=".5"/></>,
  goBack: <><path d="M9 7 4 12l5 5"/><path d="M4 12h11a5 5 0 0 1 0 10h-2"/></>,
};
export function PackageIcon({ name }: { name: PackageIconName }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name]}</svg>;
}
