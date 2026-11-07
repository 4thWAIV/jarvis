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
 * Registered for pointing at as `takeoverBar` (its name, its state and
 * Restore layout).
 */

import { CentreNotices } from "./CentreNotices";
import { CENTRE_SOCKET, readHistoryAgain, useControlView } from "./ConversationView";
import { ControlSummary } from "./conversation/ConversationControl";
import { useT } from "./locale";
import { MicHost } from "./MicControl";
import { bind, bindingProps, layout } from "./uiNavigation";
import { useSyncExternalStore } from "react";
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
 *  drawn while something has taken over: Restore layout is in the bar. */
export function TakeoverButton({ partRef }: { partRef: (el: Element | null) => void }) {
  const t = useT();
  const takenOver = useSyncExternalStore(layout.subscribe, layout.get).mode === "takeover";
  if (takenOver) return null;
  return (
    <button type="button" className="folder-button takeover-enter" title={t("main.takeover.enterTitle")}
            ref={partRef} {...bindingProps(bind("layout.takeover", {}))}>
      {t("main.takeover.enter")}
    </button>
  );
}
