/**
 * The takeover bar: while a workflow or a page package has taken over the
 * page (uiNavigation.ts `layout`), it stands in place of the centre tabs
 * and holds, left to right, the item's name, the conversation's state in
 * one line (the conversation control's one table of states, with its
 * buttons: Skip to end while JARVIS speaks), the notices as chips
 * (CentreNotices.tsx), the microphone with its health dot and Stop (moved
 * here from the message input or the corner by MicPortal, MicControl.tsx,
 * never copied), and Restore layout, which runs `layout.restore`, as the
 * brain's request does.
 *
 * While a message is being read back for his yes, a chip names who it is
 * for (`layout.sheet`); it opens the message boxes as a sheet under the bar
 * (TakeoverSheet), with their Accept and Send, and the view moves down by
 * its height, as it does for the sign-in notice on Conversation, so the
 * item's head, tabs and frame stay uncovered. His
 * spoken yes approves it as it always does; the layout stays.
 *
 * Registered for pointing at as `takeoverBar` (its name, its state and
 * Restore layout).
 */

import { captures, reading } from "./capture";
import { CaptureList } from "./CaptureBox";
import { CentreNotices } from "./CentreNotices";
import { CENTRE_SOCKET, readHistoryAgain, useControlView } from "./ConversationView";
import { ControlSummary } from "./conversation/ConversationControl";
import { useT } from "./locale";
import { MicHost } from "./MicControl";
import { bind, bindingProps, layout } from "./uiNavigation";
import { useLayoutEffect, useRef, useSyncExternalStore } from "react";
import { useUiTarget } from "./uiRegistry";
import "./conversation/conversation.css";

export function TakeoverBar({ name }: { name: string }) {
  const t = useT();
  const view = useControlView();
  const ui = useUiTarget({ ref: { region: "main", view: "main.takeover", kind: "takeoverBar" },
                           label: t("targets.takeoverBar"), socket: CENTRE_SOCKET });
  return (
    <div id="takeover-bar" role="toolbar" aria-label={t("main.takeover.label")} ref={ui.whole}>
      <h2 className="takeover-name" title={name} ref={ui.part("name")}>{name}</h2>
      <ControlSummary view={view} readAgain={readHistoryAgain} partRef={ui.part("state")} />
      <ReadbackChip partRef={ui.part("readback")} />
      <CentreNotices inBar />
      <div className="takeover-voice">
        <MicHost place="bar" which="health" />
        <MicHost place="bar" which="stop" />
        <MicHost place="bar" which="mic" />
      </div>
      <button type="button" className="folder-button takeover-restore" title={t("main.takeover.restoreTitle")}
              ref={ui.part("restore")} {...bindingProps(bind("layout.restore", {}))}>
        {t("main.takeover.restore")}
      </button>
    </div>
  );
}

/** Take over, beside × in a workflow's and a page package's head: runs
 *  `layout.takeover`, as Option+Shift+T and the brain's request do. Not
 *  drawn while something has taken over: Restore layout is in the bar.
 *  `lit` while it is offered: a package made for the wider layout, or one
 *  whose page asked for room. */
export function TakeoverButton({ partRef, lit = false }: { partRef: (el: Element | null) => void; lit?: boolean }) {
  const t = useT();
  const takenOver = useSyncExternalStore(layout.subscribe, layout.get).mode === "takeover";
  if (takenOver) return null;
  return (
    <button type="button" className={`folder-button takeover-enter${lit ? " lit" : ""}`} title={t("main.takeover.enterTitle")}
            ref={partRef} {...bindingProps(bind("layout.takeover", {}))}>
      {t("main.takeover.enter")}
    </button>
  );
}

/** The chip for the message boxes open for his yes: who the first is for,
 *  or how many there are; lit while one is being read back. */
function ReadbackChip({ partRef }: { partRef: (el: Element | null) => void }) {
  const t = useT();
  const open = useSyncExternalStore(captures.subscribe, captures.get);
  const now = useSyncExternalStore(reading.subscribe, reading.get);
  const arranged = useSyncExternalStore(layout.subscribe, layout.get);
  if (!open.length) return null;
  const sheet = arranged.mode === "takeover" && arranged.sheet;
  const first = open[0];
  const label = open.length > 1 ? t("main.takeover.readbacks", { count: String(open.length) })
    : first.to ? t("main.capture.messageTo", { to: first.to }) : t("main.capture.numbered", { id: String(first.id) });
  return (
    <button type="button" className={`readback-chip${now ? " reading" : ""}${sheet ? " open" : ""}`}
            aria-expanded={sheet} title={t("main.takeover.readbackTitle")} ref={partRef}
            {...bindingProps(bind("layout.sheet", { shown: !sheet }))}>
      {label}
    </button>
  );
}

/** The read-back's message boxes under the bar, while the chip has opened
 *  them and any is open. It sets --centre-notice-height to its own height,
 *  so every centre view starts below it and it covers nothing. Each box is
 *  registered here as a `capture` target in main.takeover. */
export function TakeoverSheet() {
  const t = useT();
  const open = useSyncExternalStore(captures.subscribe, captures.get);
  const arranged = useSyncExternalStore(layout.subscribe, layout.get);
  const shown = arranged.mode === "takeover" && arranged.sheet && open.length > 0;
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!shown || !el) return undefined;
    const root = document.documentElement.style;
    const fit = () => root.setProperty("--centre-notice-height", `${el.offsetHeight + SHEET_GAP_PX}px`);
    fit();
    const watch = new ResizeObserver(fit);
    watch.observe(el);
    return () => {
      watch.disconnect();
      root.removeProperty("--centre-notice-height");
    };
  }, [shown]);
  if (!shown) return null;
  return (
    <div id="takeover-sheet" role="region" aria-label={t("main.takeover.sheet")} ref={box}>
      <CaptureList view="main.takeover" />
    </div>
  );
}

// The space kept between the sheet and the view below it.
const SHEET_GAP_PX = 8;
