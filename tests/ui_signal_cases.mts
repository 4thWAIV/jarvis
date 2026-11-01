// The white signal's schedule (frontend/src/uiSignal.ts) and the orb's
// ownership (frontend/src/orbSource.ts), run under node by
// tests/test_ui_pipeline.py: `node --experimental-strip-types
// tests/ui_signal_cases.mts`. Each check is an assertion; the script exits
// non-zero on the first that fails and prints what it checked otherwise.
import assert from "node:assert/strict";
import {
  BEVEL_PX, bevelled, fadeAt, leaving, length, orbEmission, phaseAt, pointAlong, pulseFractions, ringProgress, routeLit,
  scheduleFor, TIMING,
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

const timing = [
  check("the contract's timing is the design's", () => {
    assert.deepEqual(
      [TIMING.emitMs, TIMING.travelMs, TIMING.pulseCount, TIMING.pulseOffsetMs, TIMING.arrivalAtMs,
       TIMING.holdUntilMs, TIMING.resetByMs],
      [220, 1400, 3, 170, 1620, 4400, 5000]);
  }),
  check("the first pulse arrives exactly when emission and travel are over", () => {
    assert.equal(TIMING.arrivalAtMs, TIMING.emitMs + TIMING.travelMs);
  }),
  check("phases run emit, travel, hold, fade, done", () => {
    assert.equal(phaseAt(0, plain), "emitting");
    assert.equal(phaseAt(219, plain), "emitting");
    assert.equal(phaseAt(220, plain), "travelling");
    assert.equal(phaseAt(1619, plain), "travelling");
    assert.equal(phaseAt(1620, plain), "holding");
    assert.equal(phaseAt(4399, plain), "holding");
    assert.equal(phaseAt(4400, plain), "fading");
    assert.equal(phaseAt(4999, plain), "fading");
    assert.equal(phaseAt(5000, plain), "done");
  }),
  check("the route is not drawn before the orb emits, and at once when still", () => {
    assert.equal(routeLit(100, false), false);
    assert.equal(routeLit(220, false), true);
    assert.equal(routeLit(0, true), true);
  }),
  check("three pulses leave 170 ms apart and each takes the whole travel", () => {
    assert.deepEqual(pulseFractions(220), []);
    assert.equal(pulseFractions(221).length, 1);
    assert.equal(pulseFractions(220 + 170 + 1).length, 2);
    assert.equal(pulseFractions(220 + 340 + 1).length, 3);
    const [first] = pulseFractions(220 + 700);
    assert.ok(Math.abs(first - 0.5) < 1e-9);
    assert.equal(pulseFractions(1620).length, 2);            // the first has arrived
    assert.deepEqual(pulseFractions(1620 + 340), []);         // the last has too
  }),
  check("arrival rings start at arrival, the second ringGapMs later", () => {
    assert.deepEqual(ringProgress(1620), [null, null]);
    const [a, b] = ringProgress(1620 + 100);
    assert.ok(a !== null && b === null);
    const [, c] = ringProgress(1620 + TIMING.ringGapMs + 100);
    assert.ok(c !== null);
  }),
  check("the cue is whole through the hold and gone by the reset", () => {
    assert.equal(fadeAt(4400, plain), 1);
    assert.ok(Math.abs(fadeAt(4700, plain) - 0.5) < 1e-9);
    assert.equal(fadeAt(5000, plain), 0);
  }),
  check("a longer explanation extends the hold, bounded, never the travel", () => {
    const long = scheduleFor(2000);
    assert.deepEqual(long, { holdUntil: 6400, resetBy: 7000 });
    assert.equal(phaseAt(1620, long), "holding");
    assert.deepEqual(scheduleFor(1e9), { holdUntil: 4400 + TIMING.maxExtraHoldMs, resetBy: 5000 + TIMING.maxExtraHoldMs });
    assert.deepEqual(scheduleFor(-5), plain);
  }),
  check("the orb rises over the emission and fades with the route", () => {
    assert.deepEqual(orbEmission(plain), { seconds: 5, riseSeconds: 0.22, fadeSeconds: 0.6 });
  }),
  check("a route's length and points along it follow its right angles", () => {
    const pts = [[0, 100], [0, 0], [50, 0]] as const;
    assert.equal(length(pts), 150);
    assert.deepEqual(pointAlong(pts, 0), [0, 100]);
    assert.deepEqual(pointAlong(pts, 0.5), [0, 25]);
    assert.deepEqual(pointAlong(pts, 1), [50, 0]);
    assert.deepEqual(leaving(pts), [0, -1]);
    assert.deepEqual(leaving([[0, 0], [0, 0], [-4, 0]]), [-1, 0]);
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
