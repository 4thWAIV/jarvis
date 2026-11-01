/**
 * What the brain showed instead of saying it (sources, links, a short list),
 * in the conversation where it was said: a collapsed yellow item, like a
 * transmission's, named by its title and how many it holds. Opening it lists
 * them; links open in a new tab and say which site they go to. Everything is
 * drawn as plain text.
 *
 * Opening and closing it is `conversation.record.expand` on its turn's
 * record key (`recordId`), for a click and the brain alike; `partRef`, its
 * target's, registers the head as `chip` and the list as `items`.
 */

import { useRecordOpen } from "./captions";
import type { Display } from "./sources";
import { bind, bindingProps } from "./uiNavigation";

export function SourcesChip({ display, recordId, partRef }: {
  display: Display; recordId: string; partRef?: (part: string) => (el: Element | null) => void }) {
  const open = useRecordOpen(recordId, true);
  const n = display.items.length;
  return (
    <span className={`sources-chip kind-${display.kind}${open ? " open" : ""}`}
          data-display={display.id}>
      <button type="button" className="sources-chip-head" aria-expanded={open} ref={partRef?.("chip")}
              {...bindingProps(bind("conversation.record.expand", { recordId, open: !open }))}>
        {display.title} ({n})
      </button>
      {open && (
        <ul className="sources-chip-body" ref={partRef?.("items")}>
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
