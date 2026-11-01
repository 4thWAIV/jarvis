/**
 * The top of the Diagnostics tab: one row per subsystem (subsystems.ts),
 * agent communications first, each with its health dot, its name and one
 * line saying why. A row that is down is red at full strength, the dot
 * glowing, so communications being down is seen at a glance; the Diagnostics
 * tab itself glows red while anything is down (RightPanel.tsx).
 *
 * Clicking voice input opens the voice Diagnostics view; clicking any other
 * row opens that subsystem's detail in the centre area (SubsystemView.tsx).
 * Both run `subsystem.open`, the command the brain's request runs. Each row
 * is a target the brain can point at (`subsystem`: row, status), this page's
 * own row too, which has no detail.
 */

import { useSyncExternalStore } from "react";
import { RIGHT_SOCKET, row } from "./uiRight";
import { useT } from "./locale";
import { graphicsTrouble } from "./orb";
import {
  sessionsConnected, STATE_WORDS, subsystemName, subsystems, subsystemSummary, withPage, type Subsystem,
} from "./subsystems";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";

export function useSubsystems(): { list: Subsystem[]; error: string | null; read: boolean } {
  const live = useSyncExternalStore(subsystems.subscribe, subsystems.get);
  const socket = useSyncExternalStore(sessionsConnected.subscribe, sessionsConnected.get);
  const graphics = useSyncExternalStore(graphicsTrouble.subscribe, graphicsTrouble.get);
  return { list: withPage(live.value?.subsystems, live.error, socket, graphics), error: live.error,
           read: live.value !== null };
}

/** Whether any subsystem is down: the Diagnostics tab glows red. */
export function useAnyDown(): boolean {
  return useSubsystems().list.some((s) => s.state === "down");
}

export function SubsystemList() {
  const t = useT();
  const { list, read } = useSubsystems();
  return (
    <section className="subsystems" aria-label={t("right.subsystems.label")}>
      <ul>
        {list.map((s) => <SubsystemRow key={s.id} subsystem={s} />)}
      </ul>
      {!read && <p className="subsystem-waiting">{t("right.subsystems.reading")}</p>}
    </section>
  );
}

function SubsystemRow({ subsystem: s }: { subsystem: Subsystem }) {
  useT();
  const name = subsystemName(s);
  const summary = subsystemSummary(s);
  const ui = useUiTarget({ ref: { region: "right", view: "right.diagnostics", kind: "subsystem", key: s.id },
                           label: name, socket: RIGHT_SOCKET });
  return (
    <li>
      <button type="button" className={`subsystem-row state-${s.state}`} data-subsystem={s.id}
              title={`${name}: ${STATE_WORDS[s.state]}. ${summary}`} ref={row(ui)}
              disabled={s.id === "page"} {...bindingProps(bind("subsystem.open", { subsystemId: s.id }))}>
        <span className={`subsystem-dot state-${s.state}`} aria-hidden="true" />
        <span className="subsystem-name">{name}</span>
        <span className="subsystem-state" ref={ui.part("status")}>{STATE_WORDS[s.state]}</span>
        <span className="subsystem-summary">{summary}</span>
      </button>
    </li>
  );
}
