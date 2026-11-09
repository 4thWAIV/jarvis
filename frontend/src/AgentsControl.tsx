/**
 * Every running agent, with whether the user has activated it. A switch at
 * the top shows only active agents or all of them, and is remembered; in the
 * All view each entry has its own switch to activate or deactivate it, and in
 * the Active view there is none and the agent's box takes the whole row. The name itself is
 * disabled for now. While any of an agent's subagents are working, its box
 * says how many ("2 working") in its lower edge, inside the row's size.
 *
 * The list shows as many whole rows as fit, with no scroll bar. The layout
 * gives the list the space left below everything above it (`.agents-space`,
 * which ends at the bottom of the page and clips anything past it), and
 * `rowsThatFit` divides that space by the REAL size of a rendered row. Both
 * are watched with a ResizeObserver, so a resized window, a control above
 * changing height, or a new row size all re-measure; nothing is computed
 * once and kept. When there are more agents than fit, the up and down chevrons
 * move through them in their sorted order, stopping at the top and the
 * bottom (it does not wrap around). Each chevron shows only when there is
 * something further in its direction; their space is always kept, so nothing
 * moves when they appear or disappear. An up or down gesture on the list does the same: a
 * mouse wheel or trackpad swipe, or a finger swiped on a touch screen.
 *
 * The Active/All switch, the chevrons and the gestures are navigation, so
 * they run registered commands (`agents.filter`, `agents.page`; uiRight.ts
 * holds the list's state and their handlers), as the brain's requests do.
 * Each row is a target the brain can point at (`agent`, keyed by session),
 * and so are the list's switch and chevrons (`agentsList`). A row's
 * activation switch chooses who a message goes to: it is not navigation
 * and stays its own action.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { WorkerFamily } from "./WorkerStatus";
import { AgentOrb } from "./AgentOrb";
import { agentName } from "./agentName";
import { useT } from "./locale";
import { PROVIDERS } from "./providers";
import { agentMessage, agentsChanged, agentsLaidOut, listAgents, RAY_MS, setAgentActive,
         type AgentRow } from "./agents";
import { bind, bindingProps, runBinding } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import {
  agentRows, agentsFirst, agentsFit, agentsListLeft, agentsShow, bringAgentIntoView, firstShown, lastFirst,
  listedAgents, RIGHT_SOCKET, row,
} from "./uiRight";

// How far a gesture travels to move the list one agent. Trackpads send many
// small wheel steps, so movement is added up until it reaches this.
const GESTURE_STEP_PX = 40;

/** How many whole rows fit in `space` above the down chevron. The row size
 *  is a rendered row's real height when there is one, else the CSS
 *  `--agent-row` it is drawn from; the gap is the list's real row gap. */
function rowsThatFit(space: HTMLElement, list: HTMLElement, downChevron: HTMLElement): number {
  const css = getComputedStyle(list);
  const firstRow = list.firstElementChild as HTMLElement | null;
  const row = firstRow ? firstRow.offsetHeight : parseFloat(css.getPropertyValue("--agent-row"));
  const gap = parseFloat(css.rowGap) || 0;
  const room = space.clientHeight - downChevron.offsetHeight;
  return Math.max(1, Math.floor((room + gap) / (row + gap)));
}

export function AgentsControl() {
  const t = useT();
  const agents = useSyncExternalStore(agentRows.subscribe, agentRows.get);
  const showing = useSyncExternalStore(agentsShow.subscribe, agentsShow.get);
  useSyncExternalStore(agentsFirst.subscribe, agentsFirst.get);
  // The agent JARVIS last sent a message to, kept in view while its ray
  // shows.
  const [messaged, setMessaged] = useState<string | null>(null);
  const visible = Math.max(1, useSyncExternalStore(agentsFit.subscribe, agentsFit.get));
  const spaceRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const gesture = useRef({ travel: 0, touchY: 0 });
  const downRef = useRef<HTMLButtonElement | null>(null);
  const ui = useUiTarget({ ref: { region: "right", view: "right.agents", kind: "agentsList" },
                           label: t("targets.agentsList"), socket: RIGHT_SOCKET });

  // The space changes with the window and with anything above it; the list
  // changes when its rows change size. Either re-measures. Leaving the page,
  // the list goes back to its top.
  useLayoutEffect(() => {
    const space = spaceRef.current, list = listRef.current, down = downRef.current;
    if (!space || !list || !down) return;
    const measure = () => agentsFit.set(rowsThatFit(space, list, down));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(space);
    observer.observe(list);
    return () => {
      observer.disconnect();
      agentsListLeft();
    };
  }, []);

  const reconcile = useCallback(() => {
    listAgents().then(agentRows.set).catch(() => {});
  }, []);

  // Read when the list opens, and again on every "agents" hint and
  // reconnect (sessionsSocket.ts).
  useEffect(() => {
    reconcile();
    return agentsChanged.subscribe(reconcile);
  }, [reconcile]);

  // JARVIS has sent an agent a message: bring it into view while its ray
  // (RayLane.tsx) shows.
  useEffect(() => agentMessage.subscribe(() => {
    const sessionId = agentMessage.get()?.sessionId;
    if (!sessionId) return;
    setMessaged(sessionId);
    setTimeout(() => setMessaged((now) => (now === sessionId ? null : now)), RAY_MS);
  }), []);

  const toggle = (agent: AgentRow) => {
    // Refused or not, the list is read again, so the switch shows what is
    // true rather than what was asked for.
    setAgentActive(agent.session_id, !agent.active).then(reconcile, (e: Error) => {
      console.warn("[agents] could not switch", agent.session_id, e.message);
      reconcile();
    });
  };

  const listed = listedAgents();
  const overflows = listed.length > visible;
  // The first row showing, held between the top and the last place that
  // still fills every row, as the list changes size.
  const last = lastFirst();
  const start = firstShown();
  // Down (wheel down, or a finger swiped up) shows the next agents, as
  // scrolling would; up shows the previous ones.
  const gestureMoved = (dy: number) => {
    gesture.current.travel += dy;
    while (Math.abs(gesture.current.travel) >= GESTURE_STEP_PX) {
      const step = Math.sign(gesture.current.travel);
      gesture.current.travel -= step * GESTURE_STEP_PX;
      cycle(step > 0 ? "next" : "previous");
    }
  };

  const shown = overflows ? listed.slice(start, start + visible) : listed;
  // The agent JARVIS just messaged is brought into view (to the top row) if
  // it is in this view but not among the rows showing, and again whenever
  // the list changes while its ray shows: the message itself moves the agent
  // up the list, which can take it out of the rows showing. Not on the
  // user's own up and down, so he can still move the list. Before the page
  // paints, so the agent is never seen out of view.
  useLayoutEffect(() => {
    if (messaged) bringAgentIntoView(messaged);
  }, [messaged, agents, showing, visible]);

  // Before the page paints this commit, so the ray is never seen ending at
  // a row that now shows another agent.
  useLayoutEffect(() => {
    agentsLaidOut.set(agentsLaidOut.get() + 1);
  });

  // From the latest position (the command reads it as it runs), so two quick
  // presses move two places; it stops at either end.
  const cycle = (direction: "previous" | "next") => {
    if (overflows) runBinding(bind("agents.page", { direction }));
  };

  return (
    <div id="agents-control" ref={ui.whole}>
      <div className="agents-show" role="group" aria-label={t("right.agents.show")} ref={ui.part("filter")}>
        {(["active", "all"] as const).map((option) => (
          <button key={option} type="button"
                  className={showing === option ? "selected" : ""}
                  aria-pressed={showing === option}
                  {...bindingProps(bind("agents.filter", { show: option }))}>
            {option === "active" ? t("right.agents.active") : t("right.agents.all")}
          </button>
        ))}
      </div>
      <Chevron direction="up" hidden={!overflows || start === 0} onClick={() => cycle("previous")}
               ref={ui.part("previous")} />
      <div className="agents-space" ref={spaceRef}>
        <ul className="agents-list" ref={listRef}
            onWheel={(e) => gestureMoved(e.deltaY)}
            onTouchStart={(e) => {
              gesture.current.travel = 0;
              gesture.current.touchY = e.touches[0].clientY;
            }}
            onTouchMove={(e) => {
              const y = e.touches[0].clientY;
              gestureMoved(gesture.current.touchY - y);
              gesture.current.touchY = y;
            }}>
          {/* Keyed by row, not by agent: cycling then changes what a row
              shows instead of rebuilding it, so each row's orb (a WebGL
              canvas) is made once rather than on every press. */}
          {shown.map((agent, row) => (
            <AgentItem key={row} agent={agent} switchable={showing === "all"} onToggle={toggle} />
          ))}
        </ul>
        <Chevron direction="down" hidden={!overflows || start >= last} onClick={() => cycle("next")}
                 ref={(el) => { downRef.current = el; ui.part("next")(el); }} />
      </div>
    </div>
  );
}

/** One agent's row, and its presentation for the brain to point at: the
 *  row, its name, and its orb, which draws its state. A row that shows
 *  another agent after the list moves registers as that agent. */
function AgentItem({ agent, switchable, onToggle }: {
  agent: AgentRow; switchable: boolean; onToggle: (agent: AgentRow) => void;
}) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "right", view: "right.agents", kind: "agent", key: agent.session_id },
                           label: agentName(agent), socket: RIGHT_SOCKET });
  // The agent's box is the whole; its orb is AgentOrb's own canvas, looked
  // for in this row only.
  const entry = (el: HTMLButtonElement | null) => {
    row(ui)(el);
    const canvas = el?.querySelector("canvas.agent-orb") ?? null;
    ui.part("orb")(canvas);
    ui.part("state")(canvas);
  };
  return (
    <li className={agent.active ? "active" : ""} data-session-id={agent.session_id}>
      <button type="button" className="agent-entry" disabled ref={entry}>
        <AgentOrb agent={agent} />
        <span className="agent-name" ref={ui.part("name")}>{agentName(agent)}</span>
        {/* Its needs-board count, in the upper right, zero included. */}
        <span className={agent.needs > 0 ? "agent-needs" : "agent-needs none"}>{agent.needs}</span>
        <AgentProvider provider={agent.provider} />
        <WorkerFamily sessionId={agent.session_id} />
      </button>
      {/* Only in the All view; in Active the agent's box takes the
          whole row. */}
      {switchable && (
        <button data-ui-action="domain" type="button" role="switch" aria-checked={agent.active}
                className="agent-switch"
                title={agent.active ? t("right.agents.deactivate") : t("right.agents.activate")}
                onClick={() => onToggle(agent)} />
      )}
    </li>
  );
}

function Chevron({ direction, hidden, onClick, ref }: {
  direction: "up" | "down";
  hidden: boolean;
  onClick: () => void;
  ref?: React.Ref<HTMLButtonElement>;
}) {
  const t = useT();
  return (
    <button type="button" className="agents-chevron" ref={ref}
            title={direction === "up" ? t("right.agents.previous") : t("right.agents.more")}
            aria-hidden={hidden} tabIndex={hidden ? -1 : 0}
            style={{ visibility: hidden ? "hidden" : "visible" }}
            data-ui-command="agents.page"
            data-ui-args={JSON.stringify({ direction: direction === "up" ? "previous" : "next" })}
            onClick={onClick}>
      <svg width="32" height="14" viewBox="0 0 32 14" fill="none" stroke="currentColor"
           strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points={direction === "up" ? "2,12 16,2 30,12" : "2,2 16,12 30,2"} />
      </svg>
    </button>
  );
}

/** The logo of the provider that runs this agent, in the slot's lower left,
 *  from the same providers table as the brain's logo on the orb. Nothing
 *  when the provider is unknown or has no logo. */
function AgentProvider({ provider }: { provider: string | null }) {
  const [missing, setMissing] = useState(false);
  const info = provider ? PROVIDERS[provider] : undefined;
  if (!info?.logo || missing) return null;
  return (
    <img className="agent-provider" src={info.logo} alt={info.name} title={info.name}
         data-provider={provider ?? undefined} onError={() => setMissing(true)} />
  );
}
