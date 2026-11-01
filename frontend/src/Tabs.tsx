/**
 * A row of icon tabs and the view of the selected one: the right panel's, and
 * the centre area's header.
 *
 * Each tab runs a registered navigation command (`binding`), the same
 * handler the brain's request runs; the row never selects a tab itself, so
 * what it shows selected is the shared route state it is given. Selecting
 * a tab, by pointer or key, leaves keyboard focus where the person put it.
 */

import type { ReactNode } from "react";
import { bindingProps, type Binding } from "./uiNavigation";

export interface Tab {
  id: string;
  title: string;
  icon: ReactNode;
  view: () => ReactNode;
  /** Kept and working, but not offered in the row. */
  hidden?: boolean;
}

/** What selecting a tab runs: a registered command (`binding`), or, until
 *  its family registers one, a view's own `onSelect`.
 *  @deprecated `onSelect` is for WorkflowDetail's subtabs only, until
 *  `workflow.tab.select` is registered (coverage-main). */
type Selects = { binding: (tab: Tab) => Binding; onSelect?: never }
  | { binding?: never; onSelect: (id: string) => void };

/** The row of tabs. `lit` marks a tab that has something new without
 *  selecting it; `alert` one whose view shows something down, in red.
 *  `words` shows each tab's title instead of its icon; `labelled` shows
 *  its icon with its title beneath. */
export function TabRow({ tabs, selected, className, lit, alert, words, labelled, ...selects }: {
  tabs: Tab[];
  selected: string;
  className: string;
  lit?: (tab: Tab) => boolean;
  alert?: (tab: Tab) => boolean;
  words?: boolean;
  labelled?: boolean;
} & Selects) {
  return (
    <div className={className} role="tablist">
      {tabs.filter((t) => !t.hidden).map((t) => (
        <button key={t.id} type="button" role="tab" title={t.title} aria-label={t.title}
                aria-selected={t.id === selected}
                className={`control-button right-tab${words ? " word-tab" : ""}${
                  labelled ? " labelled-tab" : ""}${
                  t.id === selected ? " selected" : ""}${lit?.(t) ? " lit" : ""}${
                  alert?.(t) ? " alert" : ""}`}
                {...(selects.binding ? bindingProps(selects.binding(t)) : { onClick: () => selects.onSelect!(t.id) })}>
          {words ? t.title : labelled ? <>{t.icon}<span className="tab-label">{t.title}</span></> : t.icon}
        </button>
      ))}
    </div>
  );
}
