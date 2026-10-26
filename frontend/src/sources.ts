/**
 * What is shown and never said. The server sends a `display` message for
 * each thing the brain puts on the screen instead of saying it (the sources
 * of a web answer, links, a short list); it goes into the conversation as a
 * collapsed Sources item (SourcesChip.tsx), where it was said.
 */

export interface DisplayItem { label: string; url: string; host: string; note: string }
export interface Display {
  id: number;
  kind: "sources" | "links" | "list";
  title: string;
  items: DisplayItem[];
  /** The reply it goes with, or null outside one. */
  utt?: number | null;
}
