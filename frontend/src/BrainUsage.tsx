/**
 * Every brain JARVIS has, each with its own account box (WeeklyUsage.tsx):
 * how much of the week's limit is used, how far through the week it is, and
 * the reset dates. In the left column under the microphone. When there is
 * more than one, the one in use is marked BRAIN and the other STANDBY. Until
 * the list is read, the running brain's box alone, and a line saying the list
 * could not be read for as long as that is true (it is read again until it
 * is: brains.ts).
 */

import { useSyncExternalStore } from "react";
import { brains } from "./brains";
import { WeeklyUsage } from "./WeeklyUsage";

export function BrainUsage() {
  const live = useSyncExternalStore(brains.subscribe, brains.get);
  const added = live.value?.filter((b) => b.added) ?? null;
  const several = added !== null && added.length > 1;
  return (
    <div id="brain-usage" aria-label="Brains">
      {live.error && <p className="read-failure">Couldn't read the brains ({live.error}); trying again.</p>}
      {several
        ? added!.map((b) => <WeeklyUsage key={b.name} provider={b.name}
                                         role={b.active ? "brain" : "standby"} />)
        : <WeeklyUsage />}
    </div>
  );
}
