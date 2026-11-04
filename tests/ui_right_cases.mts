// The right panel's own navigation (frontend/src/uiRight.ts: the Agents
// list's switch, paging and reveal) run under node against fixture answers,
// for tests/test_ui_right.py. Every case runs the same commands as a person
// (`byHand`'s actor) and as the brain and checks they leave the same list
// state with the same effects. Prints one JSON line: {"passed": [...names]}
// or exits non-zero on the first failure.
//
// Run with: node --experimental-transform-types --no-warnings
//   --import ./tests/ui_ts_loader.mjs tests/ui_right_cases.mts
import assert from "node:assert/strict";

// ── a page's globals, as little as these modules touch ─────────────────

const g = globalThis as Record<string, unknown>;
g.window = { location: { search: "" }, innerWidth: 1200, innerHeight: 800 };
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(performance.now()), 4) as unknown as number;
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
g.document = { elementFromPoint: () => null, visibilityState: "visible" };

const agent = (n: number, active = true) => ({
  session_id: `sess-${n}`, name: `Agent ${n}`, active, state: "idle", needs: 0, provider: null,
});
// Five active agents and one the user has not activated.
const AGENTS = [1, 2, 3, 4, 5].map((n) => agent(n)).concat([agent(6, false)]);
g.fetch = async (path: string) => {
  if (path === "/api/agents") return { ok: true, status: 200, json: async () => ({ agents: AGENTS }) };
  return { ok: false, status: 404, json: async () => ({}) };
};

const registry = await import("../frontend/src/uiRegistry.ts");
const right = await import("../frontend/src/uiRight.ts");
const { dispatch } = registry;

/** The list as the person sees it: the switch, the first row and the rows. */
const state = () => {
  const start = right.firstShown();
  return { show: right.agentsShow.get(), first: start,
           rows: right.listedAgents().slice(start, start + right.agentsFit.get()).map((a) => a.session_id) };
};

/** The Agents tab shown, the list read and laid out two rows high, at its
 *  top, showing active agents. */
async function reset() {
  await dispatch("right.tab.select", { tab: "agents" }, { actor: "human" });
  await dispatch("agents.filter", { show: "active" }, { actor: "human" });
  right.agentRows.set(AGENTS);
  right.agentsFit.set(2);
  right.agentsFirst.set(0);
}

type Steps = Array<[string, Record<string, unknown>]>;

async function run(actor: "human" | "brain", steps: Steps) {
  await reset();
  const outcomes = [];
  for (const [id, args] of steps) outcomes.push(await dispatch(id as never, args as never, { actor }));
  return { outcomes, state: state() };
}

const passed: string[] = [];

async function parity(name: string, steps: Steps, expect: (r: Awaited<ReturnType<typeof run>>) => void) {
  const human = await run("human", steps);
  const brain = await run("brain", steps);
  assert.deepEqual(brain, human, `${name}: the brain's request and the person's control differ`);
  expect(human);
  passed.push(name);
}

await parity("filter shows all, once", [["agents.filter", { show: "all" }], ["agents.filter", { show: "all" }]],
  ({ outcomes, state }) => {
    assert.deepEqual(outcomes.map((o) => o.effects), [["list.filtered"], []]);
    assert.equal(state.show, "all");
  });

await parity("filter goes back to the top", [
  ["agents.page", { direction: "next" }], ["agents.filter", { show: "all" }],
], ({ state }) => {
  assert.equal(state.first, 0);
  assert.deepEqual(state.rows, ["sess-1", "sess-2"]);
});

await parity("page on and back", [["agents.page", { direction: "next" }], ["agents.page", { direction: "next" }],
                                  ["agents.page", { direction: "previous" }]],
  ({ outcomes, state }) => {
    assert.ok(outcomes.every((o) => o.status === "completed"));
    assert.deepEqual(outcomes[0].effects, ["list.paged"]);
    assert.deepEqual(state.rows, ["sess-2", "sess-3"]);
  });

await parity("page stops at the top", [["agents.page", { direction: "previous" }]], ({ outcomes, state }) => {
  assert.deepEqual(outcomes[0], { status: "unavailable", effects: [], reason: "no-entity" });
  assert.equal(state.first, 0);
});

await parity("page stops at the bottom", [
  ["agents.page", { direction: "next" }], ["agents.page", { direction: "next" }],
  ["agents.page", { direction: "next" }], ["agents.page", { direction: "next" }],
], ({ outcomes, state }) => {
  assert.equal(outcomes[3].reason, "no-entity");
  assert.deepEqual(state.rows, ["sess-4", "sess-5"]);
});

await parity("reveal pages to the row", [["agents.reveal", { sessionId: "sess-4" }]], ({ outcomes, state }) => {
  assert.deepEqual(outcomes[0], { status: "completed", effects: ["list.paged"] });
  assert.ok(state.rows.includes("sess-4"));
});

await parity("reveal of a row showing changes nothing", [["agents.reveal", { sessionId: "sess-2" }]],
  ({ outcomes, state }) => {
    assert.deepEqual(outcomes[0], { status: "completed", effects: [] });
    assert.equal(state.first, 0);
  });

await parity("reveal never changes the switch", [["agents.reveal", { sessionId: "sess-6" }]],
  ({ outcomes, state }) => {
    assert.deepEqual(outcomes[0], { status: "unavailable", effects: [], reason: "no-entity" });
    assert.equal(state.show, "active");
  });

await parity("paging needs the list on the page", [
  ["right.tab.select", { tab: "queue" }], ["agents.page", { direction: "next" }],
  ["agents.reveal", { sessionId: "sess-4" }],
], ({ outcomes }) => {
  assert.equal(outcomes[1].reason, "not-mounted");
  assert.equal(outcomes[2].reason, "not-mounted");
});

await parity("invalid direction refused", [["agents.page", { direction: "sideways" }]], ({ outcomes }) => {
  assert.equal(outcomes[0].status, "invalid");
});

{
  // The list leaving the page puts it back at its top.
  await reset();
  await dispatch("agents.page", { direction: "next" }, { actor: "brain" });
  right.agentsListLeft();
  right.agentsFit.set(2);
  assert.equal(right.firstShown(), 0);
  passed.push("leaving the page goes back to the top");
}

for (const id of ["agents.filter", "agents.page", "agents.reveal"]) {
  assert.ok(!registry.missingHandlers().includes(id), `${id} has a handler`);
}
passed.push("every agents command has a handler");

process.stdout.write(JSON.stringify({ passed }));
process.exit(0);
