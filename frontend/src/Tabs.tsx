/**
 * A row of icon tabs and the view of the selected one: the right panel's, and
 * the centre area's header.
 *
 * Each tab runs a registered navigation command (`binding`), the same
 * handler the brain's request runs; the row never selects a tab itself, so
 * what it shows selected is the shared route state it is given. Selecting
 * a tab, by pointer or key, leaves keyboard focus where the person put it.
 *
 * A tab's `title` is the words shown, in the interface's language; its
 * identity is its registered `id`, carried as `data-ui-tab` for anything
 * that has to find the tab on the page (transferEndpoints.ts), never its
 * words.
 */

import type { ReactNode } from "react";
import { bindingProps, type Binding } from "./uiNavigation";
import { useUiBox, type BoxId } from "./uiRegistry";

export interface Tab {
  /** The registered tab id (uiContract.ts): never translated. */
  id: string;
  /** What the tab says, in the interface's language. */
  title: string;
  icon: ReactNode;
  view: () => ReactNode;
  /** Kept and working, but not offered in the row. */
  hidden?: boolean;
}

/** The row of tabs. `lit` marks a tab that has something new without
 *  selecting it; `alert` one whose view shows something down, in red.
 *  `words` shows each tab's title instead of its icon; `labelled` shows
 *  its icon with its title beneath. */
export function TabRow({ tabs, selected, className, lit, alert, words, labelled, binding, box }: {
  tabs: Tab[];
  selected: string;
  className: string;
  lit?: (tab: Tab) => boolean;
  alert?: (tab: Tab) => boolean;
  words?: boolean;
  labelled?: boolean;
  /** What selecting a tab runs: a registered command. */
  binding: (tab: Tab) => Binding;
  /** The row's box id, which a route to one of its tabs marks on its way
   *  in (uiRegistry.ts BOX_IDS). */
  box: BoxId;
}) {
  return (
    <div className={className} role="tablist" ref={useUiBox(box)}>
      {tabs.filter((t) => !t.hidden).map((t) => (
        <button key={t.id} type="button" role="tab" title={t.title} aria-label={t.title} data-ui-tab={t.id}
                aria-selected={t.id === selected}
                className={`control-button right-tab${words ? " word-tab" : ""}${
                  labelled ? " labelled-tab" : ""}${
                  t.id === selected ? " selected" : ""}${lit?.(t) ? " lit" : ""}${
                  alert?.(t) ? " alert" : ""}`}
                {...bindingProps(binding(t))}>
          {words ? t.title : labelled ? <>{t.icon}<span className="tab-label">{t.title}</span></> : t.icon}
        </button>
      ))}
    </div>
  );
}
