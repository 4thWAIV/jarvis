/**
 * The top of the Diagnostics tab: one row per subsystem (subsystems.ts),
 * agent communications first, each with its health dot, its name and one
 * line saying why. A row that is down is red at full strength, the dot
 * glowing, so communications being down is seen at a glance; the Diagnostics
 * tab itself glows red while anything is down (RightPanel.tsx).
 *
 * Clicking voice input opens the voice Diagnostics view; clicking any other
 * row opens that subsystem's detail in the centre area (SubsystemView.tsx).
 */

import { useSyncExternalStore } from "react";
import { openSubsystem, sessionsConnected, STATE_WORDS, subsystems, withPage, type Subsystem } from "./subsystems";
import { openDiagnostics } from "./voiceHealth";

export function useSubsystems(): { list: Subsystem[]; error: string | null; read: boolean } {
  const live = useSyncExternalStore(subsystems.subscribe, subsystems.get);
  const socket = useSyncExternalStore(sessionsConnected.subscribe, sessionsConnected.get);
  return { list: withPage(live.value?.subsystems, live.error, socket), error: live.error,
           read: live.value !== null };
}

/** Whether any subsystem is down: the Diagnostics tab glows red. */
export function useAnyDown(): boolean {
  return useSubsystems().list.some((s) => s.state === "down");
}

/** Open one subsystem's detail: voice input has its own view. */
export function openSubsystemRow(id: string) {
  if (id === "voice_input") {
    openSubsystem.set(null);
    openDiagnostics.set(true);
  } else {
    openDiagnostics.set(false);
    openSubsystem.set(id);
  }
}

export function SubsystemList() {
  const { list, read } = useSubsystems();
  return (
    <section className="subsystems" aria-label="Subsystems">
      <ul>
        {list.map((s) => (
          <li key={s.id}>
            <button type="button" className={`subsystem-row state-${s.state}`} data-subsystem={s.id}
                    title={`${s.name}: ${STATE_WORDS[s.state]}. ${s.summary}`}
                    disabled={s.id === "page"} onClick={() => openSubsystemRow(s.id)}>
              <span className={`subsystem-dot state-${s.state}`} aria-hidden="true" />
              <span className="subsystem-name">{s.name}</span>
              <span className="subsystem-state">{STATE_WORDS[s.state]}</span>
              <span className="subsystem-summary">{s.summary}</span>
            </button>
          </li>
        ))}
      </ul>
      {!read && <p className="subsystem-waiting">Reading JARVIS's subsystems…</p>}
    </section>
  );
}
