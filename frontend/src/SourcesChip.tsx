/**
 * What the brain showed instead of saying it (sources, links, a short list),
 * in the conversation where it was said: a collapsed yellow item, like a
 * transmission's, named by its title and how many it holds. Opening it lists
 * them; links open in a new tab and say which site they go to. Everything is
 * drawn as plain text.
 */

import { useState } from "react";
import type { Display } from "./sources";

export function SourcesChip({ display }: { display: Display }) {
  const [open, setOpen] = useState(false);
  const n = display.items.length;
  return (
    <span className={`sources-chip kind-${display.kind}${open ? " open" : ""}`}
          data-display={display.id}>
      <button type="button" className="sources-chip-head" aria-expanded={open}
              onClick={() => setOpen(!open)}>
        {display.title} ({n})
      </button>
      {open && (
        <ul className="sources-chip-body">
          {display.items.map((item, i) => (
            <li key={i}>
              {item.url
                ? <a href={item.url} target="_blank" rel="noopener noreferrer">{item.label}</a>
                : <span>{item.label}</span>}
              {item.host && <span className="sources-host">{item.host}</span>}
              {item.note && <span className="sources-note">{item.note}</span>}
            </li>
          ))}
        </ul>
      )}
    </span>
  );
}
