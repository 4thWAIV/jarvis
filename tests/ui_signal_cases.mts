// The white signal's schedule (frontend/src/uiSignal.ts) and the orb's
// ownership (frontend/src/orbSource.ts), run under node by
// tests/test_ui_pipeline.py: `node --experimental-strip-types
// tests/ui_signal_cases.mts`. Each check is an assertion; the script exits
// non-zero on the first that fails and prints what it checked otherwise.
import assert from "node:assert/strict";
import {
  along, BEVEL_PX, bevelled, drawnTo, fadeAt, leaving, length, markProgress, orbEmission, phaseAt, pointAlong,
  pulseFractions, ringProgress, scheduleFor, TIMING, upTo,
} from "../frontend/src/uiSignal.ts";
import {
  attentionTransmit, claimForAttention, holds, mainOrb, orbSource, releaseSource, transferActive,
  transferReceive, transferTransmit,
} from "../frontend/src/orbSource.ts";

const done: string[] = [];
const check = (name: string, fn: () => void | Promise<void>) => ({ name, fn });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ── the schedule ────────────────────────────────────────────────────────

const plain = scheduleFor();
// A route that passes into two boxes, a quarter and a half of the way along.
const boxed = scheduleFor(0, [0.5, 0.25]);

const timing = [
  check("the contract's timing is the design's", () => {
    assert.deepEqual(
      [TIMING.emitMs, TIMING.travelMs, TIMING.pulseCount, TIMING.pulseOffsetMs, TIMING.arrivalAtMs,
       TIMING.stopMs, TIMING.boxMarkMs, TIMING.holdUntilMs, TIMING.resetByMs],
      [220, 2000, 3, 170, 2220, 450, 900, 5000, 5600]);
  }),
  check("the first pulse arrives exactly when emission and travel are over", () => {
    assert.equal(TIMING.arrivalAtMs, TIMING.emitMs + TIMING.travelMs);
    assert.equal(plain.arrivalAt, TIMING.arrivalAtMs);
  }),
  check("phases run emit, travel, hold, fade, done", () => {
    assert.equal(phaseAt(0, plain), "emitting");
    assert.equal(phaseAt(219, plain), "emitting");
    assert.equal(phaseAt(220, plain), "travelling");
    assert.equal(phaseAt(2219, plain), "travelling");
    assert.equal(phaseAt(2220, plain), "holding");
    assert.equal(phaseAt(4999, plain), "holding");
    assert.equal(phaseAt(5000, plain), "fading");
    assert.equal(phaseAt(5599, plain), "fading");
    assert.equal(phaseAt(5600, plain), "done");
  }),
  check("each box on the way delays arrival, hold and reset by stopMs, in the order reached", () => {
    assert.deepEqual(boxed.stops, [0.25, 0.5]);
    assert.equal(boxed.arrivalAt, 2220 + 2 * 450);
    assert.equal(boxed.holdUntil, 5000 + 900);
    assert.equal(boxed.resetBy, 5600 + 900);
    assert.equal(phaseAt(3119, boxed), "travelling");
    assert.equal(phaseAt(3120, boxed), "holding");
  }),
  check("a pulse moves at an even pace and stands still at each box for stopMs", () => {
    assert.equal(along(0, boxed), 0);
    assert.equal(along(250, boxed), 0.125);
    assert.equal(along(500, boxed), 0.25);                     // at the first box's border
    assert.equal(along(500 + 449, boxed), 0.25);               // still there while it is marked
    assert.equal(along(500 + 450 + 500, boxed), 0.5);           // on to the second
    assert.equal(along(500 + 450 + 500 + 450, boxed), 0.5);
    assert.equal(along(2000 + 900, boxed), 1);                  // arrived
    assert.equal(along(1e6, boxed), 1);
    assert.equal(along(1000, plain), 0.5);
  }),
  check("nothing is drawn before the orb emits; the line grows behind the first pulse, whole at once when still", () => {
    assert.equal(drawnTo(100, boxed, false), 0);
    assert.equal(drawnTo(220, boxed, false), 0);
    assert.equal(drawnTo(220 + 500, boxed, false), 0.25);
    assert.equal(drawnTo(boxed.arrivalAt, boxed, false), 1);
    assert.equal(drawnTo(0, boxed, true), 1);
  }),
  check("each box is marked from when the first pulse reaches it, for boxMarkMs, in turn", () => {
    assert.deepEqual(markProgress(220 + 499, boxed), [null, null]);
    const [a, b] = markProgress(220 + 500 + 450, boxed);
    assert.ok(a !== null && Math.abs(a - 0.5) < 1e-9 && b === null);
    const [c, d] = markProgress(220 + 500 + 450 + 500 + 10, boxed);   // the second reached
    assert.ok(c === null && d !== null);
    assert.deepEqual(markProgress(1000, plain), []);
  }),
  check("three pulses leave 170 ms apart and each takes the whole travel", () => {
    assert.deepEqual(pulseFractions(220, plain), []);
    assert.equal(pulseFractions(221, plain).length, 1);
    assert.equal(pulseFractions(220 + 170 + 1, plain).length, 2);
    assert.equal(pulseFractions(220 + 340 + 1, plain).length, 3);
    const [first] = pulseFractions(220 + 1000, plain);
    assert.ok(Math.abs(first - 0.5) < 1e-9);
    assert.equal(pulseFractions(2220, plain).length, 2);        // the first has arrived
    assert.deepEqual(pulseFractions(2220 + 340, plain), []);     // the last has too
    // The followers gather at a box's border while it is marked.
    const [lead, second] = pulseFractions(220 + 500 + 300, boxed);
    assert.equal(lead, 0.25);
    assert.equal(second, 0.25);
  }),
  check("arrival rings start at arrival, the second ringGapMs later", () => {
    assert.deepEqual(ringProgress(2220, plain), [null, null]);
    const [a, b] = ringProgress(2220 + 100, plain);
    assert.ok(a !== null && b === null);
    const [, c] = ringProgress(2220 + TIMING.ringGapMs + 100, plain);
    assert.ok(c !== null);
    assert.deepEqual(ringProgress(2220 + 100, boxed), [null, null]);   // not before the boxes are passed
  }),
  check("the cue is whole through the hold and gone by the reset", () => {
    assert.equal(fadeAt(5000, plain), 1);
    assert.ok(Math.abs(fadeAt(5300, plain) - 0.5) < 1e-9);
    assert.equal(fadeAt(5600, plain), 0);
  }),
  check("a longer explanation extends the hold, bounded, never the travel", () => {
    const long = scheduleFor(2000);
    assert.deepEqual(long, { stops: [], arrivalAt: 2220, holdUntil: 7000, resetBy: 7600 });
    assert.equal(phaseAt(2220, long), "holding");
    assert.deepEqual(scheduleFor(1e9), { stops: [], arrivalAt: 2220, holdUntil: 5000 + TIMING.maxExtraHoldMs,
                                         resetBy: 5600 + TIMING.maxExtraHoldMs });
    assert.deepEqual(scheduleFor(-5), plain);
  }),
  check("the orb rises over the emission and fades with the route", () => {
    assert.deepEqual(orbEmission(plain), { seconds: 5.6, riseSeconds: 0.22, fadeSeconds: 0.6 });
  }),
  check("a route's length and points along it follow its right angles", () => {
    const pts = [[0, 100], [0, 0], [50, 0]] as const;
    assert.equal(length(pts), 150);
    assert.deepEqual(pointAlong(pts, 0), [0, 100]);
    assert.deepEqual(pointAlong(pts, 0.5), [0, 25]);
    assert.deepEqual(pointAlong(pts, 1), [50, 0]);
    assert.deepEqual(leaving(pts), [0, -1]);
    assert.deepEqual(leaving([[0, 0], [0, 0], [-4, 0]]), [-1, 0]);
    assert.deepEqual(upTo(pts, 0), [[0, 100]]);
    assert.deepEqual(upTo(pts, 120), [[0, 100], [0, 0], [20, 0]]);
    assert.deepEqual(upTo(pts, 1e3), [[0, 100], [0, 0], [50, 0]]);
  }),
  check("every right-angled turn is a 45-degree bevel, between runs and inside one", () => {
    assert.equal(BEVEL_PX, 6);
    // Up the orb's rise, across the top, then down a rail: two runs.
    const [rise, top] = bevelled([[[0, 100], [0, 0]], [[0, 0], [50, 0], [50, 40]]]);
    assert.deepEqual(rise, [[0, 100], [0, 6]]);                       // the earlier run stops short
    assert.deepEqual(top, [[0, 6], [6, 0], [44, 0], [50, 6], [50, 40]]); // the later one takes the bevel
    // A short leg shared by two turns gives each half of it.
    assert.deepEqual(bevelled([[[0, 0], [10, 0], [10, 4], [20, 4]]])[0], [[0, 0], [8, 0], [10, 2], [12, 4], [20, 4]]);
    // Straight runs and turns already bevelled are left alone.
    assert.deepEqual(bevelled([[[0, 0], [0, 50]], [[0, 50], [0, 80]]]), [[[0, 0], [0, 50]], [[0, 50], [0, 80]]]);
    assert.deepEqual(bevelled([top]), [top]);
    // A bevel's length is its true length.
    assert.equal(length([[0, 0], [6, 6]]), Math.hypot(6, 6));
  }),
];

// ── the orb's ownership ─────────────────────────────────────────────────

const calls: string[] = [];
mainOrb.current = {
  setState() {}, setGray() {}, setPaused() {}, setAnalyser() {}, destroy() {},
  transmit(colour: string) { calls.push(`transmit ${colour}`); },
  receive(colour: string) { calls.push(`receive ${colour}`); },
  endTransmission() { calls.push("end"); },
};
const never = new AbortController().signal;

const ownership = [
  check("attention takes a free orb at once and emits white", async () => {
    calls.length = 0;
    const token = await claimForAttention(5, 100, never);
    assert.ok(token);
    assert.equal(attentionTransmit(token!, "#ffffff", {}), true);
    assert.deepEqual(calls, ["transmit #ffffff"]);
    releaseSource(token!, true);
    assert.deepEqual(calls, ["transmit #ffffff", "end"]);
  }),
  check("a real transmission takes the orb from attention, and attention's cleanup cannot end it", async () => {
    calls.length = 0;
    const token = (await claimForAttention(5, 100, never))!;
    transferTransmit("#fbbf24", { seconds: 0.2 });
    assert.equal(transferActive(), true);
    assert.equal(holds(token), false);
    assert.equal(attentionTransmit(token, "#ffffff", {}), false);   // no longer its orb
    releaseSource(token, true);                                     // ends early: not the transfer
    assert.deepEqual(calls, ["transmit #fbbf24"]);
    assert.equal(transferActive(), true);
    await sleep(250);
  }),
  check("attention waits for a real transmission, shown as queued, then takes the orb", async () => {
    transferReceive("#f472b6", { seconds: 0.15 });
    const pending = claimForAttention(5, 1000, never);
    assert.equal(orbSource.get().waiting, 1);
    assert.equal(orbSource.get().holder, "transfer");
    const token = await pending;
    assert.ok(token);
    assert.equal(transferActive(), false);
    assert.equal(orbSource.get().waiting, 0);
    assert.equal(orbSource.get().holder, "attention");
    releaseSource(token!, false);
    assert.equal(orbSource.get().holder, null);
  }),
  check("attention gives up busy after its bounded wait, and stops waiting", async () => {
    transferTransmit("#fbbf24", { seconds: 0.5 });
    const started = performance.now();
    const token = await claimForAttention(5, 60, never);
    assert.equal(token, null);
    assert.ok(performance.now() - started < 400);
    assert.equal(orbSource.get().waiting, 0);
    assert.equal(transferActive(), true);
    await sleep(500);
  }),
  check("a withdrawn request stops waiting for the orb", async () => {
    transferTransmit("#fbbf24", { seconds: 0.5 });
    const control = new AbortController();
    const pending = claimForAttention(5, 2000, control.signal);
    control.abort();
    assert.equal(await pending, null);
    assert.equal(orbSource.get().waiting, 0);
    await sleep(500);
  }),
  check("a second message keeps the orb for the longer of the two", async () => {
    transferTransmit("#fbbf24", { seconds: 0.4 });
    transferTransmit("#f472b6", { seconds: 0.05 });
    await sleep(120);
    assert.equal(transferActive(), true);
    await sleep(350);
    assert.equal(transferActive(), false);
  }),
  check("a later attention cue's token is not an earlier one's", async () => {
    calls.length = 0;
    const a = (await claimForAttention(5, 100, never))!;
    const b = (await claimForAttention(5, 100, never))!;
    releaseSource(a, true);                                         // a's cleanup leaves b alone
    assert.deepEqual(calls, []);
    assert.equal(holds(b), true);
    releaseSource(b, true);
    assert.deepEqual(calls, ["end"]);
  }),
];

for (const { name, fn } of [...timing, ...ownership]) {
  await fn();
  done.push(name);
}
process.stdout.write(JSON.stringify({ passed: done }));
