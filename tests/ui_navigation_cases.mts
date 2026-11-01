// The page's navigation (frontend/src/uiNavigation.ts, the detail slots in
// the data modules and uiNavigationReveal.ts) run under node against
// fixture answers, for tests/test_ui_navigation.py. Every case runs the
// same command as a person (`byHand`'s actor) and as the brain and checks
// they leave the same route state with the same effects. Prints one JSON
// line: {"passed": [...names]} or exits non-zero on the first failure.
//
// Run with: node --experimental-transform-types --no-warnings
//   --import ./tests/ui_ts_loader.mjs tests/ui_navigation_cases.mts
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";

// ── a page's globals, as little as these modules touch ─────────────────

type Answer = { status: number; body?: unknown; wait?: Promise<void> };
const answers = new Map<string, Answer>();
let fetched: string[] = [];

const g = globalThis as Record<string, unknown>;
g.window = { location: { search: "" }, innerWidth: 1200, innerHeight: 800 };
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(performance.now()), 4) as unknown as number;
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
g.fetch = async (path: string, init?: { signal?: AbortSignal }) => {
  fetched.push(path);
  const a = answers.get(path) ?? { status: 404 };
  if (a.wait) {
    await Promise.race([a.wait, new Promise((_, reject) =>
      init?.signal?.addEventListener("abort", () => reject(new Error("aborted"))))]);
  }
  return { ok: a.status < 300, status: a.status, json: async () => a.body };
};

const elementAtPoint: { el: unknown } = { el: null };
g.document = { elementFromPoint: () => elementAtPoint.el, visibilityState: "visible" };

// ── fixtures ────────────────────────────────────────────────────────────

const workflow = (id: string) => ({ id, name: id, project: "jarvis", state: "running", phase: null,
                                    last_event_at: 1, dismissed: false, dismiss_at: null });
answers.set("/api/workflows", { status: 200, body: {
  workflows: [workflow("wf-1")],
  subagents: [{ key: "agent:sess-1/a1", session_id: "sess-1", session_name: "Builder", project: "jarvis",
                id: "a1", description: "", type: "general", background: false }],
} });
answers.set("/api/workflows/wf-3", { status: 200, body: { workflow: workflow("wf-3"), subagents: [] } });
answers.set("/api/conversation", { status: 200, body: { earlier: null, items: [
  { id: 1, at: 1, kind: "to_agent", text: "x", agent_session_id: "sess-1", agent_name: "Builder", message: null },
] } });
answers.set("/api/conversation/agents/sess-5", { status: 200, body: {
  earlier: null, items: [], agent: { session_id: "sess-5", name: "Scout", state: "idle" } } });
answers.set("/api/conversation/agents/sess-9", { status: 200, body: {
  earlier: null, items: [], agent: { session_id: "sess-9", name: null, state: "gone" } } });
const subsystem = (id: string) => ({ id, name: id, state: "ok", summary: "", checks: [] });
answers.set("/api/diagnostics/subsystems", { status: 200, body: {
  now: 1, subsystems: [subsystem("communications"), subsystem("voice_input"), subsystem("brain")] } });
answers.set("/api/folders/f-1", { status: 200, body: { folder: { id: "f-1" } } });

// ── the modules, once the globals are there ─────────────────────────────

const registry = await import("../frontend/src/uiRegistry.ts");
const nav = await import("../frontend/src/uiNavigation.ts");
const reveal = await import("../frontend/src/uiNavigationReveal.ts");
const workflows = await import("../frontend/src/workflows.ts");
const conversation = await import("../frontend/src/conversation.ts");
const voice = await import("../frontend/src/voiceHealth.ts");
const subsystems = await import("../frontend/src/subsystems.ts");
const folders = await import("../frontend/src/folders.ts");
const agents = await import("../frontend/src/agents.ts");
await import("../frontend/src/uiRight.ts");
await import("../frontend/src/captions.ts");
const { dispatch, navigationRevision, lastNavigationActor } = registry;

const settle = async () => {
  for (let i = 0; i < 50; i++) {
    if (workflows.workflows.get().value && subsystems.subsystems.get().value) return;
    await new Promise((r) => setTimeout(r, 5));
  }
  throw new Error("fixtures never read");
};
await settle();
await conversation.getConversation();      // the record names sess-1 "Builder"

// ── route state, and running one command as each actor ──────────────────

const state = () => ({
  right: nav.rightTab.get(),
  main: nav.mainView.get(),
  open: [...nav.openDetails.get()].sort(),
  workflow: workflows.openWorkflow.get(),
  thread: conversation.openThread.get(),
  diagnostics: voice.openDiagnostics.get(),
  subsystem: subsystems.openSubsystem.get(),
  folder: folders.selectedFolder.get(),
});

/** Back to the page as it opens: every detail closed, Conversation and Agents. */
async function reset() {
  for (const close of ["workflow.close", "thread.close", "voice.diagnostics.close", "subsystem.close",
                       "folders.close"] as const) {
    await dispatch(close, {}, { actor: "human" });
  }
  await dispatch("main.tab.select", { tab: "conversation" }, { actor: "human" });
  await dispatch("right.tab.select", { tab: "agents" }, { actor: "human" });
  agents.agentsTabLit.set(false);
}

type Steps = Array<[string, Record<string, unknown>]>;

/** Run `steps` from a fresh page as `actor`; the last step's outcome and the
 *  state it leaves. */
async function run(actor: "human" | "brain", steps: Steps) {
  await reset();
  let outcome = null;
  for (const [id, args] of steps) outcome = await dispatch(id as never, args as never, { actor });
  return { outcome, state: state() };
}

const passed: string[] = [];

/** The same steps as a person and as the brain: the same everything. */
async function parity(name: string, steps: Steps, expect: (r: Awaited<ReturnType<typeof run>>) => void) {
  const human = await run("human", steps);
  const brain = await run("brain", steps);
  assert.deepEqual(brain, human, `${name}: the brain's request and the person's control differ`);
  expect(human);
  passed.push(name);
}

// ── the cases ───────────────────────────────────────────────────────────

await parity("right tab", [["right.tab.select", { tab: "network" }]], ({ outcome, state }) => {
  assert.deepEqual(outcome, { status: "completed", effects: [] });
  assert.equal(state.right, "network");
});

{
  // Agents' lit mark is cleared by selecting it, whoever selects it.
  for (const actor of ["human", "brain"] as const) {
    await reset();
    await dispatch("right.tab.select", { tab: "queue" }, { actor: "human" });
    agents.agentsTabLit.set(true);
    const o = await dispatch("right.tab.select", { tab: "agents" }, { actor });
    assert.deepEqual(o.effects, ["agents.tab-attention-cleared"]);
    assert.equal(agents.agentsTabLit.get(), false);
  }
  passed.push("agents tab clears its mark");
}

await parity("closed detail tab refused", [["main.tab.select", { tab: "workflow" }]], ({ outcome, state }) => {
  assert.deepEqual(outcome, { status: "unavailable", effects: [], reason: "no-entity" });
  assert.equal(state.main, "conversation");
});

await parity("workflow open shows it", [["workflow.open", { workflowId: "wf-1" }]], ({ outcome, state }) => {
  assert.deepEqual(outcome, { status: "completed", effects: ["centre.view-shown"] });
  assert.equal(state.main, "workflow");
  assert.equal(state.workflow, "wf-1");
  assert.ok(state.open.includes("workflow"));
});

await parity("unlisted workflow read first, then replaces", [
  ["workflow.open", { workflowId: "wf-1" }], ["workflow.open", { workflowId: "wf-3" }],
], ({ outcome, state }) => {
  assert.deepEqual(outcome, { status: "completed", effects: ["centre.detail-replaced"] });
  assert.equal(state.workflow, "wf-3");
});

await parity("gone workflow refused, nothing opened", [["workflow.open", { workflowId: "wf-2" }]],
  ({ outcome, state }) => {
    assert.deepEqual(outcome, { status: "unavailable", effects: [], reason: "entity-gone" });
    assert.equal(state.workflow, null);
    assert.equal(state.main, "conversation");
  });

await parity("reopening a shown-away detail shows it again", [
  ["workflow.open", { workflowId: "wf-1" }], ["main.tab.select", { tab: "conversation" }],
  ["workflow.open", { workflowId: "wf-1" }],
], ({ outcome, state }) => {
  assert.deepEqual(outcome, { status: "completed", effects: ["centre.view-shown"] });
  assert.equal(state.main, "workflow");
});

await parity("close goes back to Conversation", [
  ["workflow.open", { workflowId: "wf-1" }], ["workflow.close", {}],
], ({ outcome, state }) => {
  assert.deepEqual(outcome, { status: "completed", effects: ["centre.detail-closed"] });
  assert.equal(state.main, "conversation");
  assert.ok(!state.open.includes("workflow"));
});

await parity("close while another view is shown keeps that view", [
  ["workflow.open", { workflowId: "wf-1" }], ["main.tab.select", { tab: "folders" }], ["workflow.close", {}],
], ({ state }) => {
  assert.equal(state.main, "folders");
  assert.equal(state.workflow, null);
});

await parity("close with nothing open refused", [["workflow.close", {}]], ({ outcome }) => {
  assert.deepEqual(outcome, { status: "unavailable", effects: [], reason: "no-entity" });
});

await parity("subagent opens in the workflow slot", [["subagent.open", { sessionId: "sess-1", agentId: "a1" }]],
  ({ state }) => {
    assert.equal(state.workflow, "agent:sess-1/a1");
    assert.equal(state.main, "workflow");
  });

await parity("thread named from the record", [["thread.open", { sessionId: "sess-1" }]], ({ state }) => {
  assert.deepEqual(state.thread, { sessionId: "sess-1", name: "Builder" });
  assert.equal(state.main, "thread");
});

await parity("thread read from JARVIS when not in the record", [["thread.open", { sessionId: "sess-5" }]],
  ({ state }) => assert.deepEqual(state.thread, { sessionId: "sess-5", name: "Scout" }));

await parity("thread with nothing on record refused", [["thread.open", { sessionId: "sess-9" }]],
  ({ outcome, state }) => {
    assert.deepEqual(outcome, { status: "unavailable", effects: [], reason: "entity-gone" });
    assert.equal(state.thread, null);
  });

await parity("voice diagnostics", [["voice.diagnostics.open", {}]], ({ state }) => {
  assert.equal(state.diagnostics, true);
  assert.equal(state.main, "diagnostics");
});

await parity("subsystem closes voice diagnostics", [
  ["voice.diagnostics.open", {}], ["subsystem.open", { subsystemId: "brain" }],
], ({ outcome, state }) => {
  assert.equal(outcome!.status, "completed");
  assert.deepEqual(outcome!.effects, ["centre.detail-closed", "centre.view-shown"]);
  assert.equal(state.diagnostics, false);
  assert.equal(state.subsystem, "brain");
  assert.equal(state.main, "subsystem");
});

await parity("voice input's row opens voice diagnostics", [
  ["subsystem.open", { subsystemId: "brain" }], ["subsystem.open", { subsystemId: "voice_input" }],
], ({ state }) => {
  assert.equal(state.subsystem, null);
  assert.equal(state.diagnostics, true);
  assert.equal(state.main, "diagnostics");
});

await parity("this page's row has no detail", [["subsystem.open", { subsystemId: "page" }]], ({ outcome }) =>
  assert.deepEqual(outcome, { status: "unavailable", effects: [], reason: "no-entity" }));

await parity("unlisted subsystem refused", [["subsystem.open", { subsystemId: "nothing" }]], ({ outcome }) =>
  assert.deepEqual(outcome, { status: "unavailable", effects: [], reason: "no-entity" }));

await parity("folder shows the Folders view", [["folders.open", { folderId: "f-1" }]], ({ outcome, state }) => {
  assert.deepEqual(outcome, { status: "completed", effects: ["centre.view-shown"] });
  assert.equal(state.folder, "f-1");
  assert.equal(state.main, "folders");
});

await parity("gone folder refused", [["folders.open", { folderId: "f-404" }]], ({ outcome, state }) => {
  assert.deepEqual(outcome, { status: "unavailable", effects: [], reason: "entity-gone" });
  assert.equal(state.folder, null);
});

await parity("folder closed", [["folders.open", { folderId: "f-1" }], ["folders.close", {}]], ({ state }) => {
  assert.equal(state.folder, null);
  assert.equal(state.main, "folders");
});

{
  // The stores the controls still write run the same commands, as a person.
  await reset();
  const before = navigationRevision.get();
  workflows.openWorkflow.set("wf-1");
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(nav.mainView.get(), "workflow");
  assert.equal(navigationRevision.get(), before + 1);
  assert.equal(lastNavigationActor.get(), "human");
  workflows.openWorkflow.set(null);
  folders.openFolder(null);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(nav.mainView.get(), "folders");
  passed.push("legacy stores go through the registry");
}

{
  // The brain may not invent arguments.
  const o = await dispatch("workflow.open", { workflowId: "wf 1" } as never, { actor: "brain" });
  assert.equal(o.status, "invalid");
  passed.push("invalid key refused");
}

{
  // A person's navigation while an open is being read wins: the late
  // answer opens nothing and his view stays.
  await reset();
  let release!: () => void;
  answers.set("/api/workflows/wf-slow", { status: 200, body: { workflow: workflow("wf-slow") },
                                          wait: new Promise<void>((r) => { release = r; }) });
  const asked = dispatch("workflow.open", { workflowId: "wf-slow" }, { actor: "brain" });
  await new Promise((r) => setTimeout(r, 10));
  await dispatch("main.tab.select", { tab: "folders" }, { actor: "human" });
  release();
  assert.deepEqual(await asked, { status: "cancelled", effects: [], reason: "superseded" });
  assert.equal(nav.mainView.get(), "folders");
  assert.equal(workflows.openWorkflow.get(), null);
  passed.push("person's navigation supersedes a slow open");
}

{
  // A withdrawn request opens nothing.
  await reset();
  let release!: () => void;
  answers.set("/api/workflows/wf-slow2", { status: 200, body: { workflow: workflow("wf-slow2") },
                                           wait: new Promise<void>((r) => { release = r; }) });
  const control = new AbortController();
  const asked = dispatch("workflow.open", { workflowId: "wf-slow2" }, { actor: "brain", signal: control.signal });
  await new Promise((r) => setTimeout(r, 10));
  control.abort();
  release();
  assert.equal((await asked).status, "cancelled");
  assert.equal(workflows.openWorkflow.get(), null);
  passed.push("aborted open opens nothing");
}

// ── reveal ──────────────────────────────────────────────────────────────

/** A stand-in element: hidden until `shown()`, off screen until scrolled. */
function element(shown: () => boolean, offscreen = false) {
  const el = {
    isConnected: true,
    scrolled: 0,
    closest: () => (shown() ? null : {}),
    contains: (other: unknown) => other === el,
    getBoundingClientRect: () => {
      const top = offscreen && !el.scrolled ? 2000 : 100;
      return { left: 10, top, right: 110, bottom: top + 20, width: 100, height: 20 };
    },
    scrollIntoView: () => { el.scrolled++; },
  };
  elementAtPoint.el = el;
  return el;
}

const folderRef = { region: "right", view: "right.folders", kind: "folder", key: "f-1" } as const;
const ctx = (signal = new AbortController().signal) => ({ actor: "brain" as const, requestId: "r1", signal });

function registerFolderRow(el: unknown) {
  return registry.registerTarget({
    ref: folderRef, label: "f-1", socket: { rail: "rail.right", side: "right" } as never,
    element: () => el as Element, part: () => null,
  });
}

{
  // Already visible: nothing navigates.
  await reset();
  await dispatch("right.tab.select", { tab: "folders" }, { actor: "human" });
  const off = registerFolderRow(element(() => true));
  const before = navigationRevision.get();
  const o = await reveal.reveal(folderRef as never, ctx());
  assert.equal(o.status, "completed");
  assert.equal(navigationRevision.get(), before);
  off();
  passed.push("reveal: visible needs nothing");
}

{
  // Hidden behind another tab: its declared reveal command shows it.
  await reset();
  const off = registerFolderRow(element(() => nav.rightTab.get() === "folders"));
  const o = await reveal.reveal(folderRef as never, ctx());
  assert.equal(o.status, "completed");
  assert.equal(nav.rightTab.get(), "folders");
  assert.equal(lastNavigationActor.get(), "brain");
  off();
  passed.push("reveal: runs the declared command");
}

{
  // Scrolled out of view: scrolled to, once, without a fixed wait.
  await reset();
  await dispatch("right.tab.select", { tab: "folders" }, { actor: "human" });
  const el = element(() => true, true);
  const off = registerFolderRow(el);
  const o = await reveal.reveal(folderRef as never, ctx());
  assert.equal(o.status, "completed");
  assert.equal(el.scrolled, 1);
  off();
  passed.push("reveal: scrolls an offscreen target");
}

{
  // Not mounted: the view's revealer brings it in (here: registers it).
  await reset();
  let off = () => {};
  const unregister = reveal.registerRevealer("right.folders", "folder", async () => {
    off = registerFolderRow(element(() => true));
    return true;
  });
  const o = await reveal.reveal(folderRef as never, ctx());
  assert.equal(o.status, "completed");
  off();
  unregister();
  passed.push("reveal: a revealer brings a late item");
}

{
  // The revealer says the item is gone: refused, no other row stands in.
  await reset();
  const unregister = reveal.registerRevealer("right.folders", "folder", async () => "entity-gone");
  const o = await reveal.reveal(folderRef as never, ctx());
  assert.deepEqual(o, { status: "unavailable", reason: "entity-gone", effects: [] });
  unregister();
  passed.push("reveal: a gone item is refused");
}

{
  // A person navigates while it reveals: cancelled, his view kept.
  await reset();
  let release!: () => void;
  const unregister = reveal.registerRevealer("right.folders", "folder", () =>
    new Promise((r) => { release = () => r(true); }));
  const asked = reveal.reveal(folderRef as never, ctx());
  await new Promise((r) => setTimeout(r, 10));
  await dispatch("right.tab.select", { tab: "queue" }, { actor: "human" });
  release();
  const o = await asked;
  assert.equal(o.status, "cancelled");
  assert.equal(nav.rightTab.get(), "queue");
  unregister();
  passed.push("reveal: a person's navigation cancels it");
}

{
  // Nothing declared to reveal it, and hidden: not visible, no navigation.
  await reset();
  const ref = { region: "main", view: "main.folders", kind: "folderEntry", key: "f-1/notes.md" } as const;
  const off = registry.registerTarget({ ref, label: "notes.md", socket: { rail: "rail.main", side: "left" } as never,
                                        element: () => element(() => false) as never, part: () => null });
  const before = navigationRevision.get();
  const o = await reveal.reveal(ref as never, ctx());
  assert.deepEqual(o, { status: "not_visible", reason: "hidden", effects: [] });
  assert.equal(navigationRevision.get(), before);
  off();
  passed.push("reveal: nothing declared, nothing guessed");
}

// network.ts reads WeeklyUsage.tsx, and queue.ts reads network.ts, so
// node cannot load them; nor can it load a component (.tsx), so a command
// whose handler a component registers is left to the page tests too.
const src = new URL("../frontend/src/", import.meta.url);
const inComponents = new Set(readdirSync(src).filter((f) => f.endsWith(".tsx")).flatMap(
  (f) => [...readFileSync(new URL(f, src), "utf8").matchAll(/registerCommand\("([^"]+)"/g)].map((m) => m[1])));
assert.deepEqual(registry.missingHandlers().filter((id) => !/^(network|queue)\./.test(id) && !inComponents.has(id)), [],
                 "every declared navigation command loaded here has a handler");
passed.push("no declared command without a handler");

process.stdout.write(JSON.stringify({ passed }));
process.exit(0);
