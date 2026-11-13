/**
 * Knowledge, in the centre area (design section 14): what the knowledge
 * engine holds, a search over everything JARVIS has read with each passage's
 * source and trust, the link proposals waiting for his yes, and his topics
 * with their status. Opened by `knowledge.open` (the Knowledge row in
 * Diagnostics, or the brain); every read is the same call the brain's
 * knowledge tools make (knowledge.ts).
 *
 * Other people's words are shown as text only (React never renders them as
 * markup) and marked as theirs. A proposal is never acted on but by his own
 * Accept or Reject here, or his word to the brain.
 *
 * Vector search: the embedding model it runs on, its download size, Install
 * (his own click; the brain's install_embedding_model on his word), the
 * install's progress and how much is embedded. Each hit says which of the
 * keyword index, the vectors and the graph found it.
 *
 * Registered for pointing at: the view (header, close, status, search,
 * results, proposals, topics, indexes, vectors), each proposal (row, accept,
 * reject), each suggested set of topics (row, accept, decline) and each
 * extra index (row, pin, forbid, clear). His pins and
 * forbids are kept in his settings note (knowledge.md).
 */

import { useState, useSyncExternalStore } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import {
  answerError, embedder, extraIndexes, knowledgeStatus, seeds, type Seed, proposals, search, topics, type ExtraIndex,
  type Build, type Hit, type Proposal,
} from "./knowledge";
import { formatNumber, useT } from "./locale";
import { shortAge } from "./timeFormat";
import { bind, bindingProps, runBinding } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";

export function KnowledgeView() {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.knowledge", kind: "knowledge" },
                           label: t("tabs.main.knowledge"), socket: CENTRE_SOCKET });
  const status = useSyncExternalStore(knowledgeStatus.subscribe, knowledgeStatus.get);
  const s = status.value;
  return (
    <div id="knowledge-view" ref={ui.whole}>
      <section className="subsystem-head" ref={ui.part("header")}>
        <div className="subsystem-head-words">
          <h2>{t("tabs.main.knowledge")}</h2>
          <p className="subsystem-head-state" ref={ui.part("status")} data-knowledge-state={s?.state ?? "unknown"}>
            {status.error ? t("main.knowledge.unreadable", { error: status.error })
              : !s ? t("main.knowledge.reading")
              : !s.enabled ? t("main.knowledge.off")
              : t("main.knowledge.status", { chunks: s.chunks ?? 0, proposals: s.proposals ?? 0 })}
            {s?.enabled && !s.embedder ? ` ${t("main.knowledge.noVectors")}` : ""}
          </p>
        </div>
        <button type="button" className="workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")}
                ref={ui.part("close")} {...bindingProps(bind("knowledge.close", {}))}>×</button>
      </section>
      <SearchBox partRef={ui.part("search")} resultsRef={ui.part("results")} />
      <Vectors partRef={ui.part("vectors")} />
      <Seeds partRef={ui.part("seeds")} />
      <Proposals partRef={ui.part("proposals")} />
      <Topics partRef={ui.part("topics")} />
      <Indexes partRef={ui.part("indexes")} />
    </div>
  );
}

type PartRef = (el: Element | null) => void;

const STATUS_WORDS = {
  implemented: "main.knowledge.topicStatus.implemented",
  active: "main.knowledge.topicStatus.active",
  planned: "main.knowledge.topicStatus.planned",
  retired: "main.knowledge.topicStatus.retired",
  unknown: "main.knowledge.topicStatus.unknown",
} as const satisfies Record<string, string>;

function SearchBox({ partRef, resultsRef }: { partRef: PartRef; resultsRef: PartRef }) {
  const t = useT();
  const last = useSyncExternalStore(search.subscribe, search.get);
  const [words, setWords] = useState(last.query);
  return (
    <section aria-label={t("main.knowledge.searchTitle")}>
      <h3>{t("main.knowledge.searchTitle")}</h3>
      <form className="knowledge-search" ref={partRef}
            onSubmit={(e) => { e.preventDefault(); runBinding(bind("knowledge.search", { query: words })); }}>
        <input data-ui-action="input" type="search" value={words} placeholder={t("main.knowledge.searchPlaceholder")}
               aria-label={t("main.knowledge.searchTitle")} onChange={(e) => setWords(e.target.value)} />
        <button type="submit" disabled={last.busy}>{t("main.knowledge.searchButton")}</button>
      </form>
      <div ref={resultsRef}>
        {last.error && <p className="read-failure">{t("main.knowledge.searchFailed", { error: last.error })}</p>}
        {last.hits && last.building && (
          <p className="knowledge-note" data-partial="building">
            {t("main.knowledge.searchPartial", {
              done: formatNumber(last.building.items_embedded),
              total: formatNumber(last.building.items_embedded + last.building.items_waiting) })}
          </p>
        )}
        {last.hits && !last.hits.length && <p className="voice-empty">{t("main.knowledge.noHits")}</p>}
        {last.hits && (
          <ol className="knowledge-hits">
            {last.hits.map((h) => <HitRow key={h.key} h={h} />)}
          </ol>
        )}
      </div>
    </section>
  );
}

function HitRow({ h }: { h: Hit }) {
  const t = useT();
  const theirs = h.trust !== "own";
  return (
    <li className={theirs ? "knowledge-hit untrusted" : "knowledge-hit"} data-trust={h.trust ?? "unknown"}>
      <div className="knowledge-hit-head">
        <span className="knowledge-label">{h.label ?? h.key}</span>
        <span className="knowledge-kind">{t(`main.knowledge.kind.${h.kind === "chunk" ? "passage" : "other"}`)}</span>
        <span className={`knowledge-trust${theirs ? " theirs" : ""}`}>
          {t(theirs ? "main.knowledge.theirWords" : "main.knowledge.ownWords")}
        </span>
        {h.status && <span className="knowledge-status">{h.status}</span>}
        {h.why?.match && <span className="knowledge-why" data-why="match">{t("main.knowledge.why.match", { rank: h.why.match.rank })}</span>}
        {h.why?.near && <span className="knowledge-why" data-why="near">{t("main.knowledge.why.near", { rank: h.why.near.rank })}</span>}
        {h.why?.about && <span className="knowledge-why" data-why="about">{t("main.knowledge.why.about", { rank: h.why.about.rank })}</span>}
      </div>
      {h.text && <p className="knowledge-text" data-original-detail={theirs ? "" : undefined}>{h.text}</p>}
    </li>
  );
}

function Proposals({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(proposals.subscribe, proposals.get);
  const failed = useSyncExternalStore(answerError.subscribe, answerError.get);
  const list = live.value ?? [];
  return (
    <section aria-label={t("main.knowledge.proposalsTitle")} ref={partRef}>
      <h3>{t("main.knowledge.proposalsTitle")}</h3>
      <p className="knowledge-note">{t("main.knowledge.proposalsNote")}</p>
      {live.error && <p className="read-failure">{t("main.knowledge.unreadable", { error: live.error })}</p>}
      {failed && <p className="read-failure">{t("main.knowledge.answerFailed", { error: failed })}</p>}
      {!live.error && !list.length && <p className="voice-empty">{t("main.knowledge.noProposals")}</p>}
      <ul className="knowledge-proposals">
        {list.map((p) => <ProposalRow key={p.id} p={p} />)}
      </ul>
    </section>
  );
}

function ProposalRow({ p }: { p: Proposal }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.knowledge", kind: "knowledgeProposal", key: p.id },
                           label: p.topic_name ?? p.topic, socket: CENTRE_SOCKET });
  return (
    <li className="knowledge-proposal" data-proposal={p.id} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <span className="knowledge-proposal-words">
        {p.kind === "same_as"
          ? t("main.knowledge.proposeSame", { topic: p.topic_name ?? p.topic, other: p.other_name ?? "" })
          : t("main.knowledge.proposeAbout", { topic: p.topic_name ?? p.topic, document: p.label ?? "" })}
      </span>
      <button type="button" ref={ui.part("accept")}
              {...bindingProps(bind("knowledge.proposal.answer", { proposalId: p.id, answer: "accept" }))}>
        {t("main.knowledge.accept")}
      </button>
      <button type="button" ref={ui.part("reject")}
              {...bindingProps(bind("knowledge.proposal.answer", { proposalId: p.id, answer: "reject" }))}>
        {t("main.knowledge.reject")}
      </button>
    </li>
  );
}

function Topics({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(topics.subscribe, topics.get);
  const list = live.value ?? [];
  return (
    <section aria-label={t("main.knowledge.topicsTitle")} ref={partRef}>
      <h3>{t("main.knowledge.topicsTitle")}</h3>
      {!live.error && !list.length && <p className="voice-empty">{t("main.knowledge.noTopics")}</p>}
      <ul className="knowledge-topics">
        {list.map((topic) => (
          <li key={topic.slug} data-topic={topic.slug}>
            <span className="knowledge-label">{topic.name}</span>
            <span className={`knowledge-status status-${topic.status}`}>
              {t(STATUS_WORDS[topic.status as keyof typeof STATUS_WORDS] ?? STATUS_WORDS.unknown)}
            </span>
            {topic.pinned && <span className="knowledge-pinned">{t("main.knowledge.pinned")}</span>}
            {topic.stage_of.length > 0 && (
              <span className="knowledge-links">{t("main.knowledge.stageOf", { topics: topic.stage_of.join(", ") })}</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

const INDEX_STATE = {
  made: "main.knowledge.index.made",
  proposed: "main.knowledge.index.proposed",
  chosen: "main.knowledge.index.chosen",
} as const;

function Indexes({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(extraIndexes.subscribe, extraIndexes.get);
  const list = live.value ?? [];
  return (
    <section aria-label={t("main.knowledge.indexesTitle")} ref={partRef}>
      <h3>{t("main.knowledge.indexesTitle")}</h3>
      <p className="knowledge-note">{t("main.knowledge.indexesNote")}</p>
      {!live.error && !list.length && <p className="voice-empty">{t("main.knowledge.noIndexes")}</p>}
      <ul className="knowledge-proposals">
        {list.map((x) => <IndexRow key={x.key} x={x} />)}
      </ul>
    </section>
  );
}

function IndexRow({ x }: { x: ExtraIndex }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.knowledge", kind: "knowledgeIndex", key: x.key },
                           label: x.paths.join(", "), socket: CENTRE_SOCKET });
  const state = INDEX_STATE[x.state as keyof typeof INDEX_STATE] ?? INDEX_STATE.chosen;
  return (
    <li className="knowledge-proposal" data-index={x.key} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <span className="knowledge-proposal-words">
        {t("main.knowledge.index.serves", { kinds: x.kinds.join(", "), paths: x.paths.join(", ") })}
        {" "}<span className="knowledge-status">{t(state)}</span>
        {x.uses > 0 && <span className="knowledge-status"> {t("main.knowledge.index.uses", { count: x.uses })}</span>}
        {x.pinned && <span className="knowledge-pinned"> {t("main.knowledge.index.pinned")}</span>}
        {x.forbidden && <span className="knowledge-pinned"> {t("main.knowledge.index.forbidden")}</span>}
      </span>
      <button type="button" ref={ui.part("pin")} disabled={x.pinned}
              {...bindingProps(bind("knowledge.index.choose", { indexKey: x.key, choice: "pin" }))}>
        {t("main.knowledge.index.pin")}
      </button>
      <button type="button" ref={ui.part("forbid")} disabled={x.forbidden}
              {...bindingProps(bind("knowledge.index.choose", { indexKey: x.key, choice: "forbid" }))}>
        {t("main.knowledge.index.forbid")}
      </button>
      <button type="button" ref={ui.part("clear")} disabled={!x.pinned && !x.forbidden}
              {...bindingProps(bind("knowledge.index.choose", { indexKey: x.key, choice: "clear" }))}>
        {t("main.knowledge.index.clear")}
      </button>
    </li>
  );
}

function Seeds({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(seeds.subscribe, seeds.get);
  const open = (live.value ?? []).filter((x) => x.status === "open");
  if (!open.length) return <div ref={partRef} hidden />;
  return (
    <section aria-label={t("main.knowledge.seedsTitle")} ref={partRef}>
      <h3>{t("main.knowledge.seedsTitle")}</h3>
      <p className="knowledge-note">{t("main.knowledge.seedsNote")}</p>
      <ul className="knowledge-proposals">
        {open.map((x) => <SeedRow key={x.id} seed={x} />)}
      </ul>
    </section>
  );
}

function SeedRow({ seed }: { seed: Seed }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view: "main.knowledge", kind: "knowledgeSeed", key: seed.id },
                           label: seed.title, socket: CENTRE_SOCKET });
  return (
    <li className="knowledge-proposal knowledge-seed" data-seed={seed.id} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      <div className="knowledge-proposal-words">
        <span className="knowledge-label">{seed.title}</span>
        <ul className="knowledge-seed-topics">
          {seed.topics.map((topic) => (
            <li key={topic.slug}>
              <span className="knowledge-label">{topic.name}</span>{" "}
              <span className="knowledge-status">
                {t(STATUS_WORDS[topic.status as keyof typeof STATUS_WORDS] ?? STATUS_WORDS.unknown)}
              </span>
              {seed.exists.includes(topic.slug) && <span className="knowledge-pinned"> {t("main.knowledge.seedKept")}</span>}
              <p className="knowledge-text">{topic.body.replace(/^# .*\n+/, "")}</p>
            </li>
          ))}
        </ul>
      </div>
      <button type="button" ref={ui.part("accept")}
              {...bindingProps(bind("knowledge.seed.answer", { seedId: seed.id, answer: "accept" }))}>
        {t("main.knowledge.seedAccept")}
      </button>
      <button type="button" ref={ui.part("decline")}
              {...bindingProps(bind("knowledge.seed.answer", { seedId: seed.id, answer: "decline" }))}>
        {t("main.knowledge.seedDecline")}
      </button>
    </li>
  );
}

const MB = 1 << 20;

/** How far the index build is (knowledge_api.build), the line Settings and
 * the Knowledge view show; the brain reads the same through knowledge_status. */
function BuildLine({ build }: { build: Build }) {
  const t = useT();
  if (build.state === "off") return null;
  const done = formatNumber(build.items_embedded);
  const total = formatNumber(build.items_embedded + build.items_waiting);
  const line = build.state === "done" ? t("main.knowledge.build.done", { total })
    : build.state === "held" ? t("main.knowledge.build.held", { done, total })
    : build.state === "paused" ? t("main.knowledge.build.paused", { done, total })
    : build.seconds_left !== null
      ? t("main.knowledge.build.building", { done, total, texts: formatNumber(build.texts_waiting),
                                              left: shortAge(build.seconds_left * 1000) })
      : t("main.knowledge.build.measuring", { done, total, texts: formatNumber(build.texts_waiting) });
  return (
    <p className="knowledge-note" role="status" data-build={build.state}>
      {line}
      {(build.files_waiting ?? 0) > 0 && <> {t("main.knowledge.build.files", { count: build.files_waiting ?? 0 })}</>}
    </p>
  );
}

export function Vectors({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const live = useSyncExternalStore(embedder.subscribe, embedder.get);
  const e = live.value;
  const install = e?.install;
  const running = install && ["downloading", "verifying", "installing"].includes(install.state);
  return (
    <section aria-label={t("main.knowledge.vectorsTitle")} ref={partRef}>
      <h3>{t("main.knowledge.vectorsTitle")}</h3>
      {live.error && <p className="read-failure">{t("main.knowledge.unreadable", { error: live.error })}</p>}
      {e && (
        <p className="knowledge-note" data-embedder={e.installed ? "installed" : "missing"}>
          {e.installed
            ? (e.runtime_problem ? t("main.knowledge.vectorsNoRuntime", { model: e.label })
              : e.engine.running
                ? t("main.knowledge.vectorsReady", { model: e.label, rows: e.engine.rows, waiting: e.engine.waiting ?? 0 })
                : t("main.knowledge.vectorsInstalled", { model: e.label }))
            : t("main.knowledge.vectorsMissing", { model: e.label, size: Math.round(e.download_bytes / MB) })}
        </p>
      )}
      {e?.engine.build && <BuildLine build={e.engine.build} />}
      {running && install && (
        <p className="knowledge-note" role="status">
          {t(`main.knowledge.vectorsInstall.${install.state as "downloading" | "verifying" | "installing"}`,
             { done: Math.round(install.done_bytes / MB), total: Math.round(install.total_bytes / MB) })}
        </p>
      )}
      {install?.state === "failed" && (
        <p className="read-failure">{t("main.knowledge.vectorsFailed", { error: install.error ?? "" })}</p>
      )}
      {e && !e.installed && (
        <button type="button" disabled={!!running} {...bindingProps(bind("knowledge.embedder.install", {}))}>
          {t("main.knowledge.vectorsInstallButton", { size: Math.round(e.download_bytes / MB) })}
        </button>
      )}
    </section>
  );
}
