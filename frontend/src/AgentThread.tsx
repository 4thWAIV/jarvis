/**
 * One agent's thread: everything sent to it and from it, on record, oldest
 * at the top (conversation.ts). The centre area's Thread view, opened by
 * clicking an agent's name in the Conversation history; closing it goes back
 * to Conversation. Each message shows its status as it is now and opens to
 * the agent's answer, so he can see where things stand and pick it up.
 *
 * Registered for pointing at: the thread (`agentThread`, keyed by its
 * session: its header, state, Close and Earlier) and each record in it
 * (`threadRecord`, keyed `<session>/r<id>`). Close is `thread.close`,
 * Earlier `thread.earlier` and an answer's toggle the record's own
 * `conversation.record.expand`, the same commands the brain runs; a
 * record further back is looked for a page at a time, by its id.
 *
 * Under the header, the agent's own key (agent_key_store.py): its
 * fingerprint and where it stands, when and how he onboarded it, its
 * rights, and Revoke (the `key` and `revoke` parts).
 */

import { useLayoutEffect, useRef, useState } from "react";
import { agentsChanged, getAgentKey, revokeAgentKey, type AgentKey } from "./agents";
import { CENTRE_SOCKET, earlierPages, historyRows, RecordItem } from "./ConversationView";
import { agentStateWords, conversationChanged, getThread, openThread } from "./conversation";
import { useLiveRead } from "./liveRead";
import { formatDateTime, useT, type Translate } from "./locale";
import { bind, bindingProps } from "./uiNavigation";
import { registerRevealer } from "./uiNavigationReveal";
import { registerCommand, useUiTarget } from "./uiRegistry";

/** Each open thread's earlier pages, by session. */
const threadPages = earlierPages((sessionId, before) => getThread(sessionId, before));

/** The thread the centre holds now, if any. */
const openSession = () => openThread.get()?.sessionId ?? null;

registerCommand("thread.earlier", {
  availability: () => {
    const session = openSession();
    return session ? threadPages.availability(session) : { available: false, reason: "no-entity" };
  },
  execute: (_, { signal }) => threadPages.load(openSession()!, signal),
});

// A record of the open thread not on the page yet is looked for further
// back, a page at a time, by its id. Another session's record is not
// opened in its place: its thread must be the one open.
registerRevealer("main.thread", "threadRecord", async (ref, { signal }) => {
  const [session, record] = (ref.key ?? "").split("/");
  if (session !== openSession()) return "no-entity";
  return threadPages.seek(session, (i) => `r${i.id}` === record, signal);
});

export function AgentThread({ sessionId, name }: { sessionId: string; name: string | null }) {
  const t = useT();
  const live = useLiveRead(() => getThread(sessionId), [conversationChanged]);
  const { older, earlier } = threadPages.use(sessionId, live.value ? live.value.earlier : undefined);
  const ref = useRef<HTMLDivElement>(null);
  const latest = live.value?.items ?? [];
  const ids = new Set(latest.map((i) => i.id));
  // The whole thread is the record's, live or not: every item is shown.
  const items = [...older.filter((i) => !ids.has(i.id)), ...latest];
  const rows = historyRows([], items, true);
  const agent = live.value?.agent;
  const title = agent?.name ?? name ?? t("main.thread.agent");
  const ui = useUiTarget({ ref: { region: "main", view: "main.thread", kind: "agentThread", key: sessionId },
                           label: title, socket: CENTRE_SOCKET });
  useLayoutEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [latest.length]);
  return (
    <div id="agent-thread" ref={ui.whole}>
      <div className="thread-header" ref={ui.part("header")}>
        <h3>{title}</h3>
        {agent && <span className={`thread-state state-${agent.state}`} ref={ui.part("state")}>
          {agentStateWords(agent.state)}</span>}
        <button type="button" className="thread-close" aria-label={t("main.common.close")} ref={ui.part("close")}
                {...bindingProps(bind("thread.close", {}))}>×</button>
      </div>
      <AgentKeyLine sessionId={sessionId} part={ui.part} />
      {live.error && <p className="read-failure">{t("main.thread.unreadable", { error: live.error })}</p>}
      <div className="thread-items" ref={ref}>
        {earlier != null && <button type="button" className="history-earlier" ref={ui.part("earlier")}
                                    {...bindingProps(bind("thread.earlier", {}))}>{t("main.conversation.earlier")}</button>}
        {live.value && rows.length === 0 && <p className="workflow-empty">{t("main.thread.empty")}</p>}
        {rows.map((r) => <RecordItem key={r.key} item={r.item!} answer={r.answer} restorable={r.restorable}
                                     place={{ region: "main", view: "main.thread", kind: "threadRecord",
                                              key: `${sessionId}/${r.key}` }} />)}
      </div>
    </div>
  );
}

function keyWords(t: Translate, key: AgentKey): string {
  if (key.state === "waiting") return t("main.thread.keyWaiting", { fingerprint: key.fingerprint });
  if (key.state === "pending") return t("main.thread.keyPending", { fingerprint: key.fingerprint });
  return t("main.thread.key", { fingerprint: key.fingerprint });
}

/** The agent's key, read again on every "agents" hint (which every key
 *  change sends), with Revoke while it is live. */
function AgentKeyLine({ sessionId, part }: {
  sessionId: string; part: (name: string) => (el: HTMLElement | null) => void;
}) {
  const t = useT();
  const live = useLiveRead(() => getAgentKey(sessionId), [agentsChanged]);
  const [failed, setFailed] = useState<string | null>(null);
  const key = live.value;
  if (!key) return null;
  const by = key.onboarded_by === "voice" ? t("main.thread.keyBy.voice") : t("main.thread.keyBy.page");
  const revoke = () => {
    setFailed(null);
    revokeAgentKey(key.fingerprint).then(
      () => agentsChanged.set(agentsChanged.get() + 1),
      (e: Error) => setFailed(e.message));
  };
  return (
    <div className="thread-key" ref={part("key")}>
      <span className="thread-key-id">{keyWords(t, key)}</span>
      {key.state === "onboarded" && key.onboarded_at != null && (
        <span>{t("main.thread.keyOnboarded", {
          when: formatDateTime(key.onboarded_at * 1000, { dateStyle: "medium", timeStyle: "short" }),
          by })}</span>
      )}
      {key.state === "onboarded" && key.rights.length > 0 && (
        <span>{t("main.thread.keyRights", { rights: key.rights.map((r) => r.right).join(", ") })}</span>
      )}
      <button data-ui-action="domain" type="button" className="thread-key-revoke" ref={part("revoke")}
              onClick={revoke}>
        {t("main.thread.keyRevoke")}</button>
      {failed && <span className="read-failure">{t("main.thread.keyFailed", { error: failed })}</span>}
    </div>
  );
}
