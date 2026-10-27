/**
 * One subsystem in full, in the centre area, opened from its row in the
 * Diagnostics tab (SubsystemList.tsx) the way a workflow row opens its
 * Workflow view: its state and why, each check that decided it, and for
 * agent communications the last messages JARVIS sent and whether each
 * reached its agent. Drawn from the same read as the list, so it moves with
 * every "subsystems" hint.
 */

import { useSubsystems } from "./SubsystemList";
import { openSubsystem, STATE_WORDS, type SentMessage } from "./subsystems";
import { useTick } from "./WorkflowsView";
import { ago } from "./workflows";

const OUTCOME_WORDS: Record<string, string> = { reached: "reached", lost: "did not reach" };

export function SubsystemView({ id }: { id: string }) {
  useTick(5000);
  const { list, error } = useSubsystems();
  const s = list.find((x) => x.id === id);
  return (
    <div id="subsystem-detail" data-subsystem={id}>
      <section className="subsystem-head">
        <span className={`subsystem-dot big state-${s?.state ?? "unknown"}`} aria-hidden="true" />
        <div className="subsystem-head-words">
          <h2>{s?.name ?? "Subsystem"}</h2>
          <p className={`subsystem-head-state state-${s?.state ?? "unknown"}`}>
            {s ? `${STATE_WORDS[s.state]}: ${s.summary}` : "Not on record."}
          </p>
        </div>
        <button type="button" className="workflow-close" title="Close" aria-label="Close"
                onClick={() => openSubsystem.set(null)}>×</button>
      </section>
      {error && <p className="read-failure">Couldn't read the subsystems ({error}); trying again.</p>}
      {s && (
        <section aria-label="Checks">
          <h3>Checks</h3>
          <ul className="subsystem-checks">
            {s.checks.map((c) => (
              <li key={c.id} className={`state-${c.state}`} data-check={c.id}>
                <span className={`subsystem-dot state-${c.state}`} aria-hidden="true" />
                <span className="subsystem-name">{c.name}</span>
                <span className="subsystem-state">{STATE_WORDS[c.state]}</span>
                <span className="subsystem-summary">{c.detail}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {s?.messages && <Messages messages={s.messages} />}
    </div>
  );
}

function Messages({ messages }: { messages: SentMessage[] }) {
  return (
    <section aria-label="Messages to agents">
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
