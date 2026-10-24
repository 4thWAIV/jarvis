/**
 * Every running agent, with whether the user has activated it. A switch at
 * the top shows only active agents or all of them, and is remembered; in the
 * All view each entry has its own switch to activate or deactivate it, and in
 * the Active view there is none and the agent's box takes the whole row. The name itself is
 * disabled for now.
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
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AgentOrb } from "./AgentOrb";
import { agentName } from "./agentName";
import { PROVIDERS } from "./providers";
import { agentMessage, agentsLaidOut, listAgents, RAY_MS, setAgentActive, type AgentRow } from "./agents";
import { workflowsChanged } from "./workflows";
import { createSocket } from "./ws";

type Show = "active" | "all";
const SHOW_KEY = "jarvis.agents.show";
// How far a gesture travels to move the list one agent. Trackpads send many
// small wheel steps, so movement is added up until it reaches this.
const GESTURE_STEP_PX = 40;

/** How many whole rows fit in `space` above the down chevron. The row size
 *  is a rendered row's real height when there is one, else the CSS
 *  `--agent-row` it is drawn from; the gap is the list's real row gap. */
function rowsThatFit(space: HTMLElement, list: HTMLElement, downChevron: HTMLElement,
                     givenAway: number): { rows: number; spare: number } {
  const css = getComputedStyle(list);
  const firstRow = list.firstElementChild as HTMLElement | null;
  const row = firstRow ? firstRow.offsetHeight : parseFloat(css.getPropertyValue("--agent-row"));
  const gap = parseFloat(css.rowGap) || 0;
  // The room the rows would have if the orb had not taken any spare.
  const room = space.clientHeight - downChevron.offsetHeight + givenAway;
  const rows = Math.max(1, Math.floor((room + gap) / (row + gap)));
  return { rows, spare: Math.max(0, room - (rows * (row + gap) - gap)) };
}

/** The space under the last whole row, too little for another, goes to the
 *  orb (--orb-spare on the left column, read by #orb-control's height), so
 *  the list and its chevrons end exactly at the column's bottom. */
function giveSpareToOrb(column: HTMLElement | null, spare: number): void {
  column?.style.setProperty("--orb-spare", `${spare}px`);
}

function readShow(): Show {
  try {
    return localStorage.getItem(SHOW_KEY) === "all" ? "all" : "active";
  } catch {
    return "active";
  }
}

function writeShow(show: Show): void {
  try {
    localStorage.setItem(SHOW_KEY, show);
  } catch {
    // No storage here: the choice lasts until the page closes.
  }
}

export function AgentsControl() {
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [show, setShow] = useState<Show>(readShow);
  const [first, setFirst] = useState(0);
  // The agent JARVIS last sent a message to, kept in view while its ray
  // shows.
  const [messaged, setMessaged] = useState<string | null>(null);
  const [visible, setVisible] = useState(1);
  const spaceRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const gesture = useRef({ travel: 0, touchY: 0 });
  const downRef = useRef<HTMLButtonElement>(null);

  // The space changes with the window and with anything above it; the list
  // changes when its rows change size. Either re-measures.
  useLayoutEffect(() => {
    const space = spaceRef.current, list = listRef.current, down = downRef.current;
    if (!space || !list || !down) return;
    const column = space.closest<HTMLElement>("#left-column");
    let givenAway = 0;
    const measure = () => {
      const { rows, spare } = rowsThatFit(space, list, down, givenAway);
      setVisible(rows);
      if (spare !== givenAway) {
        givenAway = spare;
        giveSpareToOrb(column, spare);
      }
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(space);
    observer.observe(list);
    return () => observer.disconnect();
  }, []);

  const reconcile = useCallback(() => {
    listAgents().then(setAgents).catch(() => {});
  }, []);

  useEffect(() => {
    reconcile();
    // /ws/sessions says "agents" when the list changes, and "snapshot" on
    // every (re)connect; either way the list is fetched again.
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const socket = createSocket(`${proto}://${location.host}/ws/sessions`);
    socket.onMessage((msg) => {
      if (msg.type === "agents" || msg.type === "snapshot") reconcile();
      // The same socket says "workflows" when a workflow's record changes
      // (workflows.ts); a (re)connect may have missed one, so it too.
      if (msg.type === "workflows" || msg.type === "snapshot") {
        workflowsChanged.set(workflowsChanged.get() + 1);
      }
      // JARVIS has sent a message to an agent: bring it into view, and the
      // ray (RayLane.tsx) is drawn to its row.
      if (msg.type === "agent_message" && typeof msg.session_id === "string") {
        const sessionId = msg.session_id;
        setMessaged(sessionId);
        setTimeout(() => setMessaged((now) => (now === sessionId ? null : now)), RAY_MS);
        agentMessage.set({ sessionId: msg.session_id, n: (agentMessage.get()?.n ?? 0) + 1 });
      }
    });
    return () => socket.close();
  }, [reconcile]);

  const choose = (next: Show) => {
    setShow(next);
    setFirst(0);
    writeShow(next);
  };

  const toggle = (agent: AgentRow) => {
    // Refused or not, the list is read again, so the switch shows what is
    // true rather than what was asked for.
    setAgentActive(agent.session_id, !agent.active).then(reconcile, (e: Error) => {
      console.warn("[agents] could not switch", agent.session_id, e.message);
      reconcile();
    });
  };

  const listed = show === "all" ? agents : agents.filter((a) => a.active);
  const overflows = listed.length > visible;
  // The first row showing, held between the top and the last place that
  // still fills every row, as the list changes size.
  const last = Math.max(0, listed.length - visible);
  const start = Math.min(Math.max(first, 0), last);
  // Down (wheel down, or a finger swiped up) shows the next agents, as
  // scrolling would; up shows the previous ones.
  const gestureMoved = (dy: number) => {
    gesture.current.travel += dy;
    while (Math.abs(gesture.current.travel) >= GESTURE_STEP_PX) {
      const step = Math.sign(gesture.current.travel);
      gesture.current.travel -= step * GESTURE_STEP_PX;
      cycle(step);
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
    if (!messaged) return;
    const at = listed.findIndex((a) => a.session_id === messaged);
    if (at >= 0 && overflows && !shown.some((a) => a.session_id === messaged)) {
      setFirst(Math.min(at, last));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messaged, agents, show, visible]);

  // Before the page paints this commit, so the ray is never seen ending at
  // a row that now shows another agent.
  useLayoutEffect(() => {
    agentsLaidOut.set(agentsLaidOut.get() + 1);
  });

  // From the latest position, so two quick presses move two places; it stops
  // at either end.
  const cycle = (step: number) => {
    if (!overflows) return;
    setFirst((f) => Math.min(Math.max(Math.min(Math.max(f, 0), last) + step, 0), last));
  };

  return (
    <div id="agents-control">
      <div className="agents-show" role="group" aria-label="Show agents">
        {(["active", "all"] as const).map((option) => (
          <button key={option} type="button"
                  className={show === option ? "selected" : ""}
                  aria-pressed={show === option}
                  onClick={() => choose(option)}>
            {option === "active" ? "Active" : "All"}
          </button>
        ))}
      </div>
      <Chevron direction="up" hidden={!overflows || start === 0} onClick={() => cycle(-1)} />
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
            <li key={row} className={agent.active ? "active" : ""}
                data-session-id={agent.session_id}>
              <button type="button" className="agent-entry" disabled>
                <AgentOrb agent={agent} />
                <span className="agent-name">{agentName(agent)}</span>
                {/* Its needs-board count, in the upper right, zero included. */}
                <span className={agent.needs > 0 ? "agent-needs" : "agent-needs none"}>{agent.needs}</span>
                <AgentProvider provider={agent.provider} />
              </button>
              {/* Only in the All view; in Active the agent's box takes the
                  whole row. */}
              {show === "all" && (
                <button type="button" role="switch" aria-checked={agent.active}
                        className="agent-switch"
                        title={agent.active ? "Deactivate" : "Activate"}
                        onClick={() => toggle(agent)} />
              )}
            </li>
          ))}
        </ul>
        <Chevron direction="down" hidden={!overflows || start >= last} onClick={() => cycle(1)}
                 ref={downRef} />
      </div>
    </div>
  );
}

function Chevron({ direction, hidden, onClick, ref }: {
  direction: "up" | "down";
  hidden: boolean;
  onClick: () => void;
  ref?: React.Ref<HTMLButtonElement>;
}) {
  return (
    <button type="button" className="agents-chevron" ref={ref}
            title={direction === "up" ? "Previous agents" : "More agents"}
            aria-hidden={hidden} tabIndex={hidden ? -1 : 0}
            style={{ visibility: hidden ? "hidden" : "visible" }}
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
