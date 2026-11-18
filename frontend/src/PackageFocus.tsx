/**
 * The focus layer over a page package (PackageView.tsx): one component for
 * every time the package needs his attention on one part of its document.
 *
 * It takes where that part is (a box in the frame's own coordinates, as
 * the frame's helper reports it through the bridge: `story.focus`,
 * packageBridge.ts; the page never reads the frame's DOM), how far to dim
 * the rest, and what to present. The part stays lit and ringed (a clear
 * box whose shadow dims everything around it), the content appears in a
 * card in the space beside it, and a line joins the card to the part.
 * With no part (the question is about the whole document, or none of its
 * targets is visible), the whole view is dimmed and the card is centred.
 *
 * A decision (a question the story waits on, or one he opened from the
 * border's Questions icon) dims to 55 percent around its part; a narrated beat dims to 35
 * percent around the beat's targets, with JARVIS's words for it; the
 * architecture explanation is a wider card centred over the whole view,
 * dimmed to 25 percent, scrolling within it. The dim and the light take no
 * clicks: the document stays his to scroll, and the light follows its part
 * as it moves (the helper reports again). The card is a labelled region; a
 * decision's card and the explanation's take keyboard focus as they open
 * and give it back as they close; Escape closes (PackageView.tsx).
 */

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import type { FrameRect } from "./packageBridge";

/** How dim the rest of the view goes for each kind of focus. */
export const DIM = { decision: 0.55, whole: 0.25, narration: 0.35 } as const;

// The space between the card and the part, and kept around the card.
const GAP = 28;
const MARGIN = 16;

interface Box { left: number; top: number; width: number; height: number }

/** `rect` (the frame's coordinates) where it is in `layer`'s, cut to the
 *  frame's own visible area; null when none of it is visible. */
function inLayer(frame: HTMLIFrameElement, layer: HTMLElement, rect: FrameRect): Box | null {
  const f = frame.getBoundingClientRect();
  const l = layer.getBoundingClientRect();
  const x0 = f.left + frame.clientLeft, y0 = f.top + frame.clientTop;
  const left = Math.max(x0 + rect.left, x0), top = Math.max(y0 + rect.top, y0);
  const right = Math.min(x0 + rect.left + rect.width, x0 + frame.clientWidth);
  const bottom = Math.min(y0 + rect.top + rect.height, y0 + frame.clientHeight);
  if (right - left < 1 || bottom - top < 1) return null;
  return { left: left - l.left, top: top - l.top, width: right - left, height: bottom - top };
}

/** Where the card goes beside `lit` in a `w` by `h` layer: to its right,
 *  else its left, else below, else above, else over the corner; and the
 *  line from the part's edge to the card's. */
function besides(lit: Box | null, card: { width: number; height: number }, w: number, h: number) {
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(v, Math.max(lo, hi)));
  // No part: centred by the stylesheet (`data-centred`), so a card whose
  // content grows after it is placed (the explanation, read once it opens)
  // stays centred and inside the view without being measured again.
  if (!lit) return { left: 0, top: 0, line: null, centred: true };
  const midY = lit.top + lit.height / 2, midX = lit.left + lit.width / 2;
  const right = lit.left + lit.width + GAP, left = lit.left - GAP - card.width;
  const below = lit.top + lit.height + GAP, above = lit.top - GAP - card.height;
  let x: number, y: number, from: [number, number], to: (x: number, y: number) => [number, number];
  if (right + card.width <= w - MARGIN) {
    x = right; y = clamp(midY - card.height / 2, MARGIN, h - card.height - MARGIN);
    from = [lit.left + lit.width, midY]; to = (cx, cy) => [cx, clamp(midY, cy + 12, cy + card.height - 12)];
  } else if (left >= MARGIN) {
    x = left; y = clamp(midY - card.height / 2, MARGIN, h - card.height - MARGIN);
    from = [lit.left, midY]; to = (cx, cy) => [cx + card.width, clamp(midY, cy + 12, cy + card.height - 12)];
  } else if (below + card.height <= h - MARGIN) {
    y = below; x = clamp(midX - card.width / 2, MARGIN, w - card.width - MARGIN);
    from = [midX, lit.top + lit.height]; to = (cx, cy) => [clamp(midX, cx + 12, cx + card.width - 12), cy];
  } else if (above >= MARGIN) {
    y = above; x = clamp(midX - card.width / 2, MARGIN, w - card.width - MARGIN);
    from = [midX, lit.top]; to = (cx, cy) => [clamp(midX, cx + 12, cx + card.width - 12), cy + card.height];
  } else {
    // No room beside it: over the corner farthest from the part's middle.
    x = midX > w / 2 ? MARGIN : w - card.width - MARGIN;
    y = midY > h / 2 ? MARGIN : h - card.height - MARGIN;
    return { left: Math.max(MARGIN, x), top: Math.max(MARGIN, y), line: null, centred: false };
  }
  const [x2, y2] = to(x, y);
  return { left: x, top: y, line: { x1: from[0], y1: from[1], x2, y2 }, centred: false };
}

export function PackageFocus({ frame, rect, dim, kind, label, takeFocus, cardRef, children }: {
  frame: HTMLIFrameElement | null;
  /** The part, in the frame's coordinates; null: the whole document. */
  rect: FrameRect | null;
  dim: number;
  kind: "decision" | "narration" | "explanation";
  /** The card's name for screen readers. */
  label: string;
  /** Move keyboard focus into the card as it opens (and back as it closes). */
  takeFocus: boolean;
  cardRef?: (el: Element | null) => void;
  children: ReactNode;
}) {
  const layer = useRef<HTMLDivElement | null>(null);
  const card = useRef<HTMLElement | null>(null);
  const [lit, setLit] = useState<Box | null>(null);
  const [place, setPlace] = useState<ReturnType<typeof besides> | null>(null);

  // Where the part and the card are: again whenever the part moves or the
  // layer or the card changes size.
  useLayoutEffect(() => {
    const el = layer.current, c = card.current;
    if (!el || !c) return undefined;
    const fit = () => {
      const now = frame && rect ? inLayer(frame, el, rect) : null;
      setLit(now);
      setPlace(besides(now, { width: c.offsetWidth, height: c.offsetHeight }, el.clientWidth, el.clientHeight));
    };
    fit();
    const watch = new ResizeObserver(fit);
    watch.observe(el);
    watch.observe(c);
    if (frame) watch.observe(frame);
    return () => watch.disconnect();
  }, [frame, rect]);

  // Once it is placed (hidden until then, so it could not take focus).
  const placed = place !== null;
  useLayoutEffect(() => {
    if (!takeFocus || !placed) return undefined;
    const before = document.activeElement as HTMLElement | null;
    card.current?.focus({ preventScroll: true });
    return () => {
      if (card.current?.contains(document.activeElement) || document.activeElement === document.body) {
        before?.focus?.({ preventScroll: true });
      }
    };
  }, [takeFocus, placed]);

  const shade = `rgba(2, 6, 23, ${dim})`;
  return (
    <div className="package-focus" data-kind={kind} data-dim={dim} data-lit={lit ? "true" : "false"} ref={layer}>
      {lit
        ? <div className="package-focus-lit" aria-hidden="true"
               style={{ left: lit.left, top: lit.top, width: lit.width, height: lit.height,
                        boxShadow: `0 0 0 200vmax ${shade}` }} />
        : <div className="package-focus-dim" aria-hidden="true" style={{ background: shade }} />}
      {place?.line && (
        <svg className="package-focus-line" aria-hidden="true">
          <line x1={place.line.x1} y1={place.line.y1} x2={place.line.x2} y2={place.line.y2} />
          <circle cx={place.line.x1} cy={place.line.y1} r={4} />
        </svg>
      )}
      <section className="package-focus-card" role="region" aria-label={label} tabIndex={-1}
               aria-live={kind === "narration" ? "polite" : undefined}
               data-centred={place?.centred ? "true" : undefined}
               style={!place ? { visibility: "hidden" } : place.centred ? undefined : { left: place.left, top: place.top }}
               ref={(el) => { card.current = el; cardRef?.(el); }}>
        {children}
      </section>
    </div>
  );
}
