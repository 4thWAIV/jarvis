/**
 * The pieces every Settings section is built from (Settings.tsx,
 * BrainsSection.tsx, TeamSection.tsx, LanguageControl.tsx): one labelled
 * row, and the registration that makes each control and reading a target
 * the brain can point at, hung from the main rail like every other centre
 * view.
 *
 * Each target is named by its registered key, never by its place on the
 * page: one `settingsItem` per control or reading (its whole is the
 * control, its `label` part the words naming it), a brain's card and its
 * Add button by the brain, a team member by GitHub id, a version on offer
 * by its commit.
 */

import type { ReactNode } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import type { UI_CONTRACT } from "./uiContract";
import { useUiTarget } from "./uiRegistry";

type Kinds = (typeof UI_CONTRACT)["kinds"][number];
/** A control or reading in Settings, by its registered key. */
export type SettingsItem = Extract<Kinds, { kind: "settingsItem" }>["keys"][number];

/** Register one Settings control or reading while it is on show (`shown`
 *  false: not drawn now, so not a target). Its whole is the control; its
 *  `label` part the words that name it. */
export function useSettingsItem(key: SettingsItem, label: string, shown = true) {
  return useUiTarget(shown ? { ref: { region: "main", view: "main.settings", kind: "settingsItem", key },
                               label, socket: CENTRE_SOCKET } : null);
}

/** Register one of Settings' keyed rows: a brain's card or its Add
 *  button (by the brain), a team member (by GitHub id), a version on offer
 *  (by its commit). */
export function useSettingsRow(kind: "brainCard" | "brainAdd" | "teamMember" | "updateVersion", key: string,
                               label: string) {
  return useUiTarget({ ref: { region: "main", view: "main.settings", kind, key }, label, socket: CENTRE_SOCKET });
}

/** One row of a section: the words naming it on the left, its controls
 *  (or reading) on the right. */
export function SettingsRow({ label, labelRef, htmlFor, children }: {
  label: string;
  labelRef?: (el: Element | null) => void;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div className="settings-row">
      <label ref={labelRef} htmlFor={htmlFor}>{label}</label>
      <div className="settings-controls">{children}</div>
    </div>
  );
}
