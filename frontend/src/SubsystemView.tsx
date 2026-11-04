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
import { statusWords } from "./conversation";
import { useT } from "./locale";
import { useSubsystems } from "./SubsystemList";
import {
  checkDetail, checkName, openSubsystem, STATE_WORDS, subsystemName, subsystemSummary, type Check, type SentMessage,
} from "./subsystems";
import { bind, bindingProps } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { useUiTarget } from "./uiRegistry";
import { kindDecl, splitKey } from "./uiValidate";
import { useTick } from "./WorkflowsView";
import { ago } from "./workflows";

// A check not drawn: its subsystem (the key's first part) is opened by its
// registered command, run as the caller. Nothing else.
registerRevealer("main.subsystem", "subsystemCheck", async (ref, { navigate }) => {
  const [subsystemId] = splitKey(kindDecl("subsystemCheck")!, ref.key!);
  if (openSubsystem.get() === subsystemId) return true;
  return await navigate("subsystem.open", { subsystemId }) ? true : "no-entity";
});

export function SubsystemView({ id }: { id: string }) {
  useTick(5000);
  const t = useT();
  const { list, error } = useSubsystems();
  const s = list.find((x) => x.id === id);
  const ui = useUiTarget({ ref: { region: "main", view: "main.subsystem", kind: "subsystem", key: id },
                           label: s ? subsystemName(s) : t("tabs.main.subsystem"), socket: CENTRE_SOCKET });
  return (
    <div id="subsystem-detail" data-subsystem={id} ref={ui.whole}>
      <section className="subsystem-head" ref={ui.part("header")}>
        <span className={`subsystem-dot big state-${s?.state ?? "unknown"}`} aria-hidden="true" />
        <div className="subsystem-head-words">
          <h2>{s ? subsystemName(s) : t("tabs.main.subsystem")}</h2>
          <p className={`subsystem-head-state state-${s?.state ?? "unknown"}`}>
            {s ? `${STATE_WORDS[s.state]}: ${subsystemSummary(s)}`
               : t("main.subsystem.notOnRecord")}
          </p>
        </div>
        <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")}
                ref={ui.part("close")}
                {...bindingProps(bind("subsystem.close", {}))}>×</button>
      </section>
      {error && <p className="read-failure">{t("main.subsystem.unreadable", { error })}</p>}
      {s && (
        <section aria-label={t("main.subsystem.checks")} ref={ui.part("checks")}>
          <h3>{t("main.subsystem.checks")}</h3>
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
  useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.subsystem", kind: "subsystemCheck", key: `${subsystem}/${c.id}` },
                           label: checkName(c), socket: CENTRE_SOCKET });
  return (
    <li className={`state-${c.state}`} data-check={c.id} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <span className={`subsystem-dot state-${c.state}`} aria-hidden="true" />
      <span className="subsystem-name">{checkName(c)}</span>
      <span className="subsystem-state">{STATE_WORDS[c.state]}</span>
      <span className="subsystem-summary">{checkDetail(c)}</span>
    </li>
  );
}

function Messages({ messages, partRef }: { messages: SentMessage[]; partRef: (el: Element | null) => void }) {
  const t = useT();
  return (
    <section aria-label={t("main.subsystem.messages")} ref={partRef}>
      <h3>{t("main.subsystem.messages")}</h3>
      {!messages.length && <p className="voice-empty">{t("main.subsystem.noMessages")}</p>}
      <ol className="subsystem-messages">
        {messages.map((m) => (
          <li key={m.id} className={`outcome-${m.outcome ?? "none"}`}>
            <time>{ago(m.at)}</time>
            <span className="subsystem-name">{m.agentName}</span>
            <span className="subsystem-state">{statusWords(m.status)}</span>
            <span className="subsystem-summary">
              {m.outcome ? t(`main.subsystem.outcome.${m.outcome}`) : ""}
              {m.failure ? <> (<span data-original-detail="">{m.failure}</span>)</> : null}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
