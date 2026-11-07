/**
 * The conversation control: the message input's heading row, one grid in
 * every state (conversation.css, .conversation-control). Row one: the
 * activity light, the state's title and "Message JARVIS"; row two: the
 * detail (an overlap's yellow lead, JARVIS's or an error's sentence, his
 * words with their newest end in view) and the buttons, at the right end
 * when wide and on a row of their own under 560 px. No track depends on
 * the state, so the row never changes height. What it shows is
 * controlState.ts's `ControlView`; each button is the same call as before
 * (his turn's frames, the brain's voice_turn; skip_to_end, the brain's
 * skip_reading; Start listening, his alone; reading the history again).
 */

import { useSyncExternalStore } from "react";
import { useT } from "../locale";
import { startListening, voiceInputSettings } from "../mic";
import { skipReading, turnAction } from "../voiceStatus";
import { ACTIONS, GROUP_LABEL, type Action, type ControlView, type Group } from "./controlState";

type PartRef = (el: Element | null) => void;

/** What each of the control's buttons calls: the same wherever the
 *  state is shown (the message input, or the takeover bar). */
function runs(readAgain: () => void): Record<Action, () => void> {
  return {
    send: () => turnAction.current("send_now"),
    sendHeard: () => turnAction.current("send_now"),
    retry: () => turnAction.current("retry"),
    draft: () => turnAction.current("keep_as_draft"),
    skip: () => skipReading.current(),
    listenHere: () => startListening.current(),
    startListening: () => startListening.current(),
    readAgain,
  };
}

/** The activity light, by the state's light and pause stage. */
function ControlLight({ view }: { view: ControlView }) {
  return <span className="control-light" data-light={view.light} data-stage={view.stage} aria-hidden="true"><i /><i /><i /></span>;
}

/** The state's buttons. `ids` gives each its id (the message input's own,
 *  which only one place may carry). */
function ControlActions({ view, readAgain, ids }: { view: ControlView; readAgain: () => void; ids: boolean }) {
  const t = useT();
  const settings = useSyncExternalStore(voiceInputSettings.subscribe, voiceInputSettings.get);
  const run = runs(readAgain);
  return <div className="control-actions">
    {view.actions.map(action => {
      const spec = ACTIONS[action];
      const listen = spec.kind === "microphone";
      return <button key={action} type="button" id={ids ? spec.id : undefined} data-control-action={action}
                     data-ui-action={spec.kind}
                     className={spec.primary ? "primary" : undefined} title={t(spec.title)}
                     disabled={listen && !settings}
                     onClick={e => { if (listen) e.stopPropagation(); run[action](); }}>
        {t(spec.label)}{spec.more && <> <span aria-hidden="true">»</span></>}
      </button>;
    })}
  </div>;
}

/** The conversation's state in one line, for the takeover bar: the light,
 *  the state's short title and its buttons, from the same table. */
export function ControlSummary({ view, readAgain, partRef }: {
  view: ControlView; readAgain: () => void; partRef: PartRef;
}) {
  return <div className={`control-summary tone-${view.tone}`} data-state={view.id} ref={partRef}>
    <ControlLight view={view} />
    <span className="control-summary-title" role={view.title ? "status" : undefined}
          title={[view.title, view.note].filter(Boolean).join(" ") || undefined}>
      {view.short ?? view.title}
    </span>
    <ControlActions view={view} readAgain={readAgain} ids={false} />
  </div>;
}

export function ConversationControl({ view, groupRef, readAgain }: {
  view: ControlView; groupRef: (group: Group) => PartRef; readAgain: () => void;
}) {
  const t = useT();
  const both = view.heard !== undefined;
  const said = both ? `${view.words} ${view.heard}` : view.words;
  return <div id="conversation-control" className={`conversation-control tone-${view.tone}`}
              data-state={view.id} data-pause={view.pause} data-light={view.light}
              role={view.group ? "group" : undefined}
              aria-label={view.group ? t(GROUP_LABEL[view.group]) : undefined}
              ref={view.group ? groupRef(view.group) : undefined}>
    <ControlLight view={view} />
    <span className="control-title" title={view.title ?? undefined}
          role={view.title ? (view.tone === "error" && !view.words ? "alert" : "status") : undefined}>
      {view.title && <span className={`control-title-long${view.short ? " has-short" : ""}`}>{view.title}</span>}
      {view.short && <span className="control-title-short">{view.short}</span>}
    </span>
    <label className="control-label" htmlFor="conversation-draft">{t("main.conversation.messageJarvis")}</label>
    <span className={`control-detail${view.quiet ? " quiet" : ""}`}>
      {view.lead && <span className="control-lead">{view.lead}</span>}
      {view.note && <span className="control-note" title={view.note}>{view.note}</span>}
      {said !== undefined && <span className="control-words heard-words" title={said}>
        <span>{both ? <><span className="earlier">{view.words}</span> <span className="now">{view.heard}</span></>
                    : view.words}</span>
      </span>}
    </span>
    <ControlActions view={view} readAgain={readAgain} ids />
  </div>;
}
