/**
 * Who has the main orb's one transmission overlay. A message's real
 * transmission (RayLane.tsx) always takes it, whatever holds it; white
 * attention (uiAttention.ts) takes it only when nothing real is on it,
 * waiting a bounded time for one to finish, and then says it is busy.
 * Every effect holds a token and gives back only its own: an attention cue
 * ending early can never end a message's transmission.
 *
 * The orb itself (orb.ts) eases each effect out over its own seconds and
 * keeps its listening/thinking/speaking state underneath, so giving the
 * overlay back needs no call unless an effect is cut short.
 *
 * The main orb lives here (OrbControl.tsx sets it), so nothing else makes
 * it transmit. Its imports name their `.ts` extension so node can run it as
 * it is (tests/ui_signal_cases.mts).
 */

import type { Orb, TransmitOptions } from "./orb.ts";
import { createStore } from "./store.ts";

/** The main orb, while it is on the page. */
export const mainOrb: { current: Orb | null } = { current: null };

type Kind = "transfer" | "attention";
interface Holder { token: symbol; kind: Kind; until: number }

let holder: Holder | null = null;
let waiting = 0;

/** What the overlay is doing, for the page to show: which kind of effect
 *  holds it (null when none) and how many attention cues wait for it. */
export const orbSource = createStore<{ holder: Kind | null; waiting: number; turn: number }>(
  { holder: null, waiting: 0, turn: 0 });

const live = (h: Holder | null): h is Holder => h !== null && performance.now() < h.until;

function changed(): void {
  orbSource.set({ holder: live(holder) ? holder.kind : null, waiting, turn: orbSource.get().turn + 1 });
}

function take(kind: Kind, seconds: number): symbol {
  const token = Symbol(kind);
  let until = performance.now() + seconds * 1000;
  // A second message while one is going keeps the orb for the longer of
  // the two: its own shorter end must not hand the orb on under the first.
  if (kind === "transfer" && live(holder) && holder.kind === "transfer") until = Math.max(until, holder.until);
  holder = { token, kind, until };
  changed();
  return token;
}

/** A message going out: always takes the orb. */
export function transferTransmit(colour: string, motion: TransmitOptions & { seconds: number }): void {
  take("transfer", motion.seconds);
  mainOrb.current?.transmit(colour, motion);
}

/** A message coming in: always takes the orb. */
export function transferReceive(colour: string, motion: TransmitOptions & { seconds: number }): void {
  take("transfer", motion.seconds);
  mainOrb.current?.receive(colour, motion);
}

/** Whether a real transmission has the orb now. */
export function transferActive(): boolean {
  return live(holder) && holder.kind === "transfer";
}

/** Take the orb for attention, waiting at most `waitMs` for a real
 *  transmission to finish; null when it is still busy then, or `signal`
 *  is aborted first. The token's effect lasts `seconds`. */
export function claimForAttention(seconds: number, waitMs: number, signal: AbortSignal): Promise<symbol | null> {
  return new Promise((resolve) => {
    const deadline = performance.now() + waitMs;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let unsubscribe = () => {};
    let done = false;
    let queued = false;
    // Stops listening BEFORE taking the orb: taking it is itself a change
    // of hands, which must not wake this wait again.
    const finish = (claim: () => symbol | null) => {
      if (done) return;
      done = true;
      if (timer) clearTimeout(timer);
      unsubscribe();
      signal.removeEventListener("abort", onAbort);
      if (queued) waiting--;
      const token = claim();
      if (queued && !token) changed();
      resolve(token);
    };
    const none = () => null;
    const onAbort = () => finish(none);
    // Now, then again when the overlay changes hands or the transmission
    // holding it ends, until the limit.
    const wake = () => {
      if (done) return;
      if (timer) clearTimeout(timer);
      if (signal.aborted) return finish(none);
      if (!transferActive()) return finish(() => take("attention", seconds));
      const remaining = deadline - performance.now();
      if (remaining <= 0) return finish(none);
      timer = setTimeout(wake, Math.min(remaining, Math.max(0, holder!.until - performance.now()) + 1));
    };
    if (signal.aborted) return resolve(null);
    if (!transferActive()) return resolve(take("attention", seconds));
    // It waits: counted (shown as queued) before it listens, so counting
    // it does not wake it.
    queued = true;
    waiting++;
    changed();
    signal.addEventListener("abort", onAbort);
    unsubscribe = orbSource.subscribe(wake);
    wake();
  });
}

/** Start the white emission for a token that holds the orb. */
export function attentionTransmit(token: symbol, colour: string, motion: TransmitOptions): boolean {
  if (holder?.token !== token) return false;
  mainOrb.current?.transmit(colour, motion);
  return true;
}

/** Whether `token` still holds the orb. */
export function holds(token: symbol): boolean {
  return holder?.token === token && live(holder);
}

/** Give the orb back. `early`: the effect was cut short, so end it now --
 *  only if it is still this token's. */
export function releaseSource(token: symbol, early: boolean): void {
  if (holder?.token !== token) return;
  if (early) mainOrb.current?.endTransmission();
  holder = null;
  changed();
}
