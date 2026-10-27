/**
 * A row of icon tabs and the view of the selected one: the right panel's, and
 * the centre area's header.
 */

import type { ReactNode } from "react";

export interface Tab {
  id: string;
  title: string;
  icon: ReactNode;
  view: () => ReactNode;
  /** Kept and working, but not offered in the row. */
  hidden?: boolean;
}

/** The row of tabs. `lit` marks a tab that has something new without
 *  selecting it; `alert` one whose view shows something down, in red.
 *  `words` shows each tab's title instead of its icon. */
export function TabRow({ tabs, selected, onSelect, className, lit, alert, words }: {
  tabs: Tab[];
  selected: string;
  onSelect: (id: string) => void;
  className: string;
  lit?: (tab: Tab) => boolean;
  alert?: (tab: Tab) => boolean;
  words?: boolean;
}) {
  return (
    <div className={className} role="tablist">
      {tabs.filter((t) => !t.hidden).map((t) => (
        <button key={t.id} type="button" role="tab" title={t.title} aria-label={t.title}
                aria-selected={t.id === selected}
                className={`control-button right-tab${words ? " word-tab" : ""}${
                  t.id === selected ? " selected" : ""}${lit?.(t) ? " lit" : ""}${
                  alert?.(t) ? " alert" : ""}`}
                onClick={() => onSelect(t.id)}>
          {words ? t.title : t.icon}
        </button>
      ))}
    </div>
  );
}
