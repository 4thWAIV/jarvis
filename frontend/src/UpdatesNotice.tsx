/**
 * Updates waiting, at the top of the centre area: one grouped count of the
 * attention record's pending items and Review updates, never one notice per
 * event (attention.ts). Shown while anything is pending, or while spoken
 * updates are held; gone otherwise.
 *
 * Review updates opens the list in place: each update in words, by its
 * kind and event, with the name it is about as its source wrote it (text,
 * never markup), and Seen; Mark all seen marks every pending one. Hold
 * spoken updates says he is away: nothing routine is spoken until Resume.
 * None of these speak, send or approve anything.
 *
 * The notice is a target hung from the main rail, its count, Review, Mark
 * all seen, Hold and the list as parts. Review is the registered command
 * `updates.review`, the same for his click and the brain's request; Seen and
 * Hold are the brain's mark_updates_seen and set_attention_settings.
 */

import { useSyncExternalStore } from "react";
import { attention, itemName, itemWords, markSeen, setAway } from "./attention";
import { useT } from "./locale";
import { createStore } from "./store";
import { bind, bindingProps } from "./uiNavigation";
import { registerCommand, useUiTarget } from "./uiRegistry";

/** @routeState Whether Review updates' list is open. */
const reviewOpen = createStore(false);

registerCommand("updates.review", {
  availability: () => (attention.get().value?.items.some((i) => i.state === "pending") ?? false)
    ? { available: true } : { available: false, reason: "no-entity" },
  execute(args) {
    reviewOpen.set(args.open);
    return { status: "completed", effects: [] };
  },
});

export function UpdatesNotice() {
  const t = useT();
  const live = useSyncExternalStore(attention.subscribe, attention.get);
  const open = useSyncExternalStore(reviewOpen.subscribe, reviewOpen.get);
  const now = live.value;
  const pending = now?.items.filter((i) => i.state === "pending") ?? [];
  const away = now?.settings.away ?? false;
  const shown = pending.length > 0 || away;
  const ui = useUiTarget(shown ? { ref: { region: "main", view: "main.notice", kind: "updatesNotice" },
                                   label: t("targets.updatesNotice"), socket: { rail: "rail.main", side: "right" } }
                               : null);
  if (!shown) return null;
  return (
    <div id="updates-notice" role="status" ref={ui.whole}>
      <p ref={ui.part("text")}>
        {pending.length > 0 ? t("main.updates.count", { count: pending.length }) : t("main.updates.held")}
      </p>
      {pending.length > 0 && (
        <button type="button" className="settings-btn primary" ref={ui.part("review")}
                aria-expanded={open} {...bindingProps(bind("updates.review", { open: !open }))}>
          {open ? t("main.updates.hide") : t("main.updates.review")}</button>
      )}
      {pending.length > 0 && (
        <button type="button" className="settings-btn" ref={ui.part("markSeen")}
                data-ui-action="domain" onClick={() => void markSeen()}>{t("main.updates.markAllSeen")}</button>
      )}
      <button type="button" className="settings-btn" ref={ui.part("away")} aria-pressed={away}
              data-ui-action="domain" onClick={() => void setAway(!away)}>
        {away ? t("main.updates.resume") : t("main.updates.hold")}</button>
      {open && pending.length > 0 && (
        <ul className="updates-list" ref={ui.part("list")}>
          {pending.map((item) => (
            <li key={item.id}>
              <span className="updates-words" title={itemName(item) || undefined}>{itemWords(item)}</span>
              <button type="button" className="settings-btn" data-ui-action="domain"
                      onClick={() => void markSeen([item.id])}>
                {t("main.updates.markSeen")}</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
