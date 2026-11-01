/**
 * One subsystem in full, in the centre area, opened from its row in the
 * Diagnostics tab (SubsystemList.tsx) the way a workflow row opens its
 * Workflow view: its state and why, each check that decided it, and for
 * agent communications the last messages JARVIS sent and whether each
 * reached its agent. Drawn from the same read as the list, so it moves with
 * every "subsystems" hint.
 *
 * Registered for pointing at: the subsystem (its head, Close, checks and
 * messages) and each check (`<subsystem>/<check>`). Close is
 * `subsystem.close`, as the brain runs it.
 */

import { CENTRE_SOCKET } from "./ConversationView";
import { useSubsystems } from "./SubsystemList";
import { STATE_WORDS, type Check, type SentMessage } from "./subsystems";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { useTick } from "./WorkflowsView";
import { ago } from "./workflows";

const OUTCOME_WORDS: Record<string, string> = { reached: "reached", lost: "did not reach" };

export function SubsystemView({ id }: { id: string }) {
  useTick(5000);
  const { list, error } = useSubsystems();
  const s = list.find((x) => x.id === id);
  const ui = useUiTarget({ ref: { region: "main", view: "main.subsystem", kind: "subsystem", key: id },
                           label: s?.name ?? "Subsystem", socket: CENTRE_SOCKET });
  return (
    <div id="subsystem-detail" data-subsystem={id} ref={ui.whole}>
      <section className="subsystem-head" ref={ui.part("header")}>
        <span className={`subsystem-dot big state-${s?.state ?? "unknown"}`} aria-hidden="true" />
        <div className="subsystem-head-words">
          <h2>{s?.name ?? "Subsystem"}</h2>
          <p className={`subsystem-head-state state-${s?.state ?? "unknown"}`}>
            {s ? `${STATE_WORDS[s.state]}: ${s.summary}` : "Not on record."}
          </p>
        </div>
        <button type="button" className="workflow-close" title="Close" aria-label="Close" ref={ui.part("close")}
                {...bindingProps(bind("subsystem.close", {}))}>×</button>
      </section>
      {error && <p className="read-failure">Couldn't read the subsystems ({error}); trying again.</p>}
      {s && (
        <section aria-label="Checks" ref={ui.part("checks")}>
          <h3>Checks</h3>
          <ul className="subsystem-checks">
            {s.checks.map((c) => <CheckRow key={c.id} subsystem={id} c={c} />)}
          </ul>
        </section>
      )}
      {s?.messages && <Messages messages={s.messages} partRef={ui.part("messages")} />}
    </div>
  );
}

function CheckRow({ subsystem, c }: { subsystem: string; c: Check }) {
  const ui = useUiTarget({ ref: { region: "main", view: "main.subsystem", kind: "subsystemCheck", key: `${subsystem}/${c.id}` },
                           label: c.name, socket: CENTRE_SOCKET });
  return (
    <li className={`state-${c.state}`} data-check={c.id} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <span className={`subsystem-dot state-${c.state}`} aria-hidden="true" />
      <span className="subsystem-name">{c.name}</span>
      <span className="subsystem-state">{STATE_WORDS[c.state]}</span>
      <span className="subsystem-summary">{c.detail}</span>
    </li>
  );
}

function Messages({ messages, partRef }: { messages: SentMessage[]; partRef: (el: Element | null) => void }) {
  return (
    <section aria-label="Messages to agents" ref={partRef}>
      <h3>Messages to agents</h3>
      {!messages.length && <p className="voice-empty">No messages on record.</p>}
      <ol className="subsystem-messages">
        {messages.map((m) => (
          <li key={m.id} className={`outcome-${m.outcome ?? "none"}`}>
            <time>{ago(m.at)}</time>
            <span className="subsystem-name">{m.agentName}</span>
            <span className="subsystem-state">{m.status}</span>
            <span className="subsystem-summary">
              {m.outcome ? OUTCOME_WORDS[m.outcome] : ""}{m.failure ? ` (${m.failure})` : ""}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
