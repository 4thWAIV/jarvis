/**
 * Ray colours belong to the transmission, not the agent. A ray takes the
 * first free colour from a short fixed palette while it is showing, and gives
 * it back when it fades: one transmission is always the primary colour, a
 * second at the same time the next, and so on. Several at once is rare, so
 * the palette is small; past its end the colours start again.
 */

// Blue is last: it is too close to JARVIS's own colour to stand out.
const PALETTE = [
  "#fbbf24",   // primary: amber
  "#f472b6",   // pink
  "#4ade80",   // green
  "#a78bfa",   // violet
  "#38bdf8",   // blue
];

const inUse = new Set<number>();

/** Take a colour for a new transmission; give it back with `release`. */
export function takeRayColour(): { colour: string; release: () => void } {
  let slot = 0;
  while (inUse.has(slot) && slot < PALETTE.length) slot++;
  if (slot === PALETTE.length) slot = inUse.size % PALETTE.length;   // all in use
  inUse.add(slot);
  return { colour: PALETTE[slot], release: () => inUse.delete(slot) };
}
