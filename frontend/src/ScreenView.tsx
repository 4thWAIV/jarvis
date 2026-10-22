/**
 * The Screen tab: what the brain put on the screen instead of saying it,
 * newest first. Links open in a new tab and say which site they go to.
 * Everything is drawn as plain text.
 */

import { useEffect, useSyncExternalStore } from "react";
import { displays, keptDisplays, unseenDisplay } from "./screen";

export function ScreenView() {
  const shown = useSyncExternalStore(displays.subscribe, displays.get);
  const kept = useSyncExternalStore(keptDisplays.subscribe, keptDisplays.get);

  // Looking at the tab is seeing it.
  useEffect(() => { unseenDisplay.set(false); }, [shown]);

  return (
    <div className="right-view screen-view">
      {kept.error && <p className="read-failure">Couldn't read what was on the screen ({kept.error}); trying again.</p>}
      {[...shown].reverse().map((d) => (
        <section key={d.id} className={`screen-item ${d.kind}`}>
          <h3>{d.title}</h3>
          <ul>
            {d.items.map((item, i) => (
              <li key={i}>
                {item.url
                  ? <a href={item.url} target="_blank" rel="noopener noreferrer">{item.label}</a>
                  : <span>{item.label}</span>}
                {item.host && <span className="screen-host">{item.host}</span>}
                {item.note && <span className="screen-note">{item.note}</span>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
