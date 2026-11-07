/**
 * The page's side of the knowledge engine: one store for the Knowledge view
 * (KnowledgeView.tsx), read from /api/knowledge — the same calls the brain's
 * knowledge tools run (knowledge_api.py) — again on every "knowledge" hint
 * on /ws/sessions.
 *
 * The view is a centre detail opened by `knowledge.open` (the Knowledge row
 * in Diagnostics, or the brain) and closed by `knowledge.close`; a search is
 * `knowledge.search`, and answering a proposal is `knowledge.proposal.answer`,
 * which only his own hand runs: the brain answers proposals with
 * link_knowledge, on his word.
 */

import { liveRead } from "./liveRead";
import { createStore } from "./store";
import { detailSlot, registerClose } from "./uiNavigation";
import { registerCommand } from "./uiRegistry";

export interface KnowledgeStatus {
  enabled: boolean;
  state: string;
  chunks?: number | null;
  embedder?: string | null;
  pace?: string | null;
  proposals?: number;
  ingest?: { passes?: number; last_finished_at?: number | null; documents?: number; more?: boolean } | null;
}

export interface Hit {
  key: string;
  kind: string;
  label: string | null;
  source: string | null;
  trust: "own" | "untrusted" | string | null;
  status: string | null;
  text: string | null;
  heading_path: string[] | null;
  score: number | null;
}

export interface Proposal {
  id: string;
  kind: "about" | "same_as" | string;
  topic: string;
  topic_name: string | null;
  label?: string | null;
  source?: string | null;
  other_name?: string | null;
  exact?: number;
  ambiguous?: number;
}

export interface Topic {
  slug: string;
  name: string;
  status: string;
  pinned: boolean;
  aliases: { exact: string[]; ambiguous: string[] };
  stage_of: string[];
  relates_to: string[];
}

export interface ExtraIndex {
  key: string;
  kinds: string[];
  paths: string[];
  uses: number;
  state: "made" | "proposed" | "chosen" | string;
  pinned: boolean;
  forbidden: boolean;
}

/** Moves on every "knowledge" hint on /ws/sessions. */
export const knowledgeChanged = createStore(0);

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { message?: string }).message ?? `${path}: ${res.status}`);
  return body as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify(body) });
  const answer = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((answer as { message?: string }).message ?? `${path}: ${res.status}`);
  return answer as T;
}

export const knowledgeStatus = liveRead(() => getJson<KnowledgeStatus>("/api/knowledge/health"), [knowledgeChanged]);
export const proposals = liveRead(
  () => getJson<{ proposals: Proposal[] }>("/api/knowledge/proposals").then((b) => b.proposals),
  [knowledgeChanged]);
export const topics = liveRead(
  () => getJson<{ topics: Topic[] }>("/api/knowledge/topics").then((b) => b.topics), [knowledgeChanged]);

export const extraIndexes = liveRead(
  () => getJson<{ indexes: ExtraIndex[] }>("/api/knowledge/indexes").then((b) => b.indexes), [knowledgeChanged]);

/** The last search: its words, what came back, and why it failed. */
export interface Search { query: string; hits: Hit[] | null; notes: string[]; error: string | null; busy: boolean }
export const search = createStore<Search>({ query: "", hits: null, notes: [], error: null, busy: false });

export async function runSearch(query: string): Promise<void> {
  const words = query.trim();
  search.set({ query: words, hits: search.get().hits, notes: [], error: null, busy: true });
  if (!words) {
    search.set({ query: "", hits: null, notes: [], error: null, busy: false });
    return;
  }
  try {
    const answer = await postJson<{ hits: Hit[]; notes: string[] }>("/api/knowledge/search", { text: words });
    if (search.get().query !== words) return;
    search.set({ query: words, hits: answer.hits, notes: answer.notes ?? [], error: null, busy: false });
  } catch (e) {
    search.set({ query: words, hits: null, notes: [], error: (e as Error).message, busy: false });
  }
}

/** The last answer he gave a proposal that failed, in its own words. */
export const answerError = createStore<string | null>(null);

export async function answerProposal(id: string, answer: "accept" | "reject"): Promise<boolean> {
  try {
    await postJson("/api/knowledge/links", { action: answer, proposal: id });
    answerError.set(null);
    knowledgeChanged.set(knowledgeChanged.get() + 1);
    return true;
  } catch (e) {
    answerError.set((e as Error).message);
    return false;
  }
}

// ── opening, searching and answering: the registered commands ─────────────

const knowledgeSlot = detailSlot<true>("knowledge");
/** Whether Knowledge is open in the centre. Read-only: controls run the commands. */
export const openKnowledge = knowledgeSlot.value;

registerCommand("knowledge.open", {
  availability: () => ({ available: true }),
  execute: () => ({ status: "completed", effects: knowledgeSlot.show(true) }),
});

registerClose("knowledge.close", knowledgeSlot);

registerCommand("knowledge.search", {
  availability: () => ({ available: true }),
  async execute({ query }) {
    const effects = knowledgeSlot.show(true);
    await runSearch(query);
    return { status: "completed", effects: [...effects, "list.filtered"] };
  },
});

registerCommand("knowledge.proposal.answer", {
  availability: ({ proposalId }) =>
    (proposals.get().value ?? []).some((p) => p.id === proposalId)
      ? { available: true } : { available: false, reason: "no-entity" },
  async execute({ proposalId, answer }) {
    return await answerProposal(proposalId, answer)
      ? { status: "completed", effects: [] }
      : { status: "unavailable", effects: [], reason: answerError.get() ?? undefined };
  },
});

registerCommand("knowledge.index.choose", {
  availability: () => ({ available: true }),
  async execute({ indexKey, choice }) {
    try {
      await postJson("/api/knowledge/indexes", { key: indexKey, choice });
      answerError.set(null);
      knowledgeChanged.set(knowledgeChanged.get() + 1);
      return { status: "completed", effects: [] };
    } catch (e) {
      answerError.set((e as Error).message);
      return { status: "unavailable", effects: [], reason: (e as Error).message };
    }
  },
});
