/**
 * Updates waiting, at the top of the centre area: one grouped count of the
 * attention record's pending items and Review updates, never one notice per
 * event (attention.ts). Shown while anything is pending, or while spoken
 * updates are held; gone otherwise.
 *
 * On Conversation it is a line under the tabs, and Review updates opens the
 * list in place: each update in words, by its kind and event, with the name
 * it is about as its source wrote it (text, never markup), and Seen; Mark
 * all seen marks every pending one. Hold spoken updates says he is away:
 * nothing routine is spoken until Resume. None of these speak, send or
 * approve anything.
 *
 * A package an agent sent (kind `package`, package_inbox.py) is one reduced
 * line: a small package mark, who sent it, its title and how many decisions
 * wait, then Open and Seen. Nothing else: no preview, no picture, no sound;
 * it never expands, plays or takes the centre by itself. Open is his click
 * (the inbox's one Open); Seen leaves the package in the backlog.
 *
 * On every other view (`compact`, CentreNotices.tsx) it is one small chip in
 * the tab row ("3 updates") that never opens by itself: clicking it opens
 * the same list as a small dropdown over the view, with Mark all seen and
 * Hold in it, closed by Escape, a click outside it, or the chip again. With
 * nothing pending but updates held, the chip says so with Resume beside it.
 *
 * The list is closed whenever the centre's view changes, so a list left open
 * on one view never comes back open on the next.
 *
 * The notice is a target hung from the main rail, its count, Review, Mark
 * all seen, Hold and the list as parts. Review (the chip, when compact) is
 * the registered command `updates.review`, the same for his click and the
 * brain's request; Seen and Hold are the brain's mark_updates_seen and
 * set_attention_settings. A part inside the closed dropdown is revealed by
 * opening it with `updates.review`.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import { attention, itemName, itemWords, markSeen, setAway, type AttentionItem } from "./attention";
import { SaidText, useT, type Said } from "./locale";
import { openInbox } from "./packageInbox";
import { PackageMark } from "./QueuePackages";
import { createStore } from "./store";
import { bind, bindingProps, mainView } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { byHand, registerCommand, useUiTarget } from "./uiRegistry";

/** @routeState Whether Review updates' list is open. */
const reviewOpen = createStore(false);

const anyPending = () => attention.get().value?.items.some((i) => i.state === "pending") ?? false;

registerCommand("updates.review", {
  availability: () => anyPending() ? { available: true } : { available: false, reason: "no-entity" },
  execute(args) {
    reviewOpen.set(args.open);
    return { status: "completed", effects: [] };
  },
});

// Any change of the centre's view closes the list.
mainView.subscribe(() => reviewOpen.set(false));

// Mark all seen, Hold and the list sit in the chip's dropdown on a reading
// view: closed, they are brought out by opening it.
registerRevealer("main.notice", "updatesNotice", async (ref, { navigate }) => {
  if (ref.part !== "list" && ref.part !== "markSeen" && ref.part !== "away") return "no-entity";
  if (!anyPending()) return "no-entity";
  return (await navigate("updates.review", { open: true })) ? true : "no-entity";
});

export function UpdatesNotice({ compact = false }: { compact?: boolean }) {
  const t = useT();
  const live = useSyncExternalStore(attention.subscribe, attention.get);
  const open = useSyncExternalStore(reviewOpen.subscribe, reviewOpen.get);
  const now = live.value;
  const pending = now?.items.filter((i) => i.state === "pending") ?? [];
  const away = now?.settings.away ?? false;
  const shown = pending.length > 0 || away;
  const listed = open && pending.length > 0;
  const ui = useUiTarget(shown ? { ref: { region: "main", view: "main.notice", kind: "updatesNotice" },
                                   label: t("targets.updatesNotice"), socket: { rail: "rail.main", side: "right" } }
                               : null);

  // The dropdown closes on Escape or a click outside it (the chip's own
  // click toggles it). Escape is taken first, so it does not also skip
  // what JARVIS is reading.
  const dropdown = compact && listed;
  useEffect(() => {
    if (!dropdown) return;
    const close = () => byHand("updates.review", { open: false });
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      e.preventDefault();
      close();
    };
    const press = (e: PointerEvent) => {
      if (e.target instanceof Element && e.target.closest("#updates-notice")) return;
      close();
    };
    document.addEventListener("keydown", key, true);
    document.addEventListener("pointerdown", press, true);
    return () => {
      document.removeEventListener("keydown", key, true);
      document.removeEventListener("pointerdown", press, true);
    };
  }, [dropdown]);

  if (!shown) return null;
  const markAll = pending.length > 0 && (
    <button type="button" className="settings-btn" ref={ui.part("markSeen")}
            data-ui-action="domain" onClick={() => void markSeen()}>{t("main.updates.markAllSeen")}</button>
  );
  const hold = (
    <button type="button" className="settings-btn" ref={ui.part("away")} aria-pressed={away}
            data-ui-action="domain" onClick={() => void setAway(!away)}>
      {away ? t("main.updates.resume") : t("main.updates.hold")}</button>
  );
  const list = listed && (
    <ul className="updates-list" ref={ui.part("list")}>
      {pending.map((item) => item.kind === "package" ? <PackageLine key={item.id} item={item} /> : (
        <li key={item.id}>
          <span className="updates-words" title={itemName(item) || undefined}>{itemWords(item)}</span>
          <button type="button" className="settings-btn" data-ui-action="domain"
                  onClick={() => void markSeen([item.id])}>
            {t("main.updates.markSeen")}</button>
        </li>
      ))}
    </ul>
  );

  if (compact) {
    return (
      <div id="updates-notice" className="centre-chip" role="status" ref={ui.whole}>
        {pending.length > 0 ? (
          <button type="button" className="centre-chip-button" ref={ui.part("review")} aria-expanded={open}
                  aria-haspopup="true" {...bindingProps(bind("updates.review", { open: !open }))}>
            <span ref={ui.part("text")}>{t("main.updates.chip", { count: pending.length })}</span>
          </button>
        ) : (
          <>
            <span ref={ui.part("text")}>{t("main.updates.chipHeld")}</span>
            {hold}
          </>
        )}
        {listed && (
          <div className="updates-dropdown">
            {list}
            <div className="updates-dropdown-actions">{markAll}{hold}</div>
          </div>
        )}
      </div>
    );
  }

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
      {markAll}
      {hold}
      {list}
    </div>
  );
}

/** A package's reduced line: who sent it, its title and how many decisions
 *  wait, with Open and Seen. The words are the sender's, drawn as text. */
function PackageLine({ item }: { item: AttentionItem }) {
  const t = useT();
  const [said, setSaid] = useState<Said | null>(null);
  const waiting = Number(item.facts.waiting ?? 0);
  const words = t("main.updates.package.line", {
    sender: item.untrusted.agent || t("main.updates.someone"), title: item.untrusted.package ?? "",
  }) + (waiting > 0 ? `, ${t("main.updates.package.waiting", { count: waiting })}` : "");
  return (
    <li className="updates-package" data-inbox-id={item.source_id}>
      <PackageMark />
      <span className="updates-words">{words}</span>
      <button type="button" className="settings-btn" data-ui-action="domain"
              onClick={() => void openInbox(item.source_id).then(setSaid)}>
        {t("main.updates.package.open")}</button>
      <button type="button" className="settings-btn" data-ui-action="domain"
              onClick={() => void markSeen([item.id])}>
        {t("main.updates.markSeen")}</button>
      {said && <SaidText said={said} className="file-state" />}
    </li>
  );
}
