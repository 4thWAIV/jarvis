/**
 * The pipeline drawn: the faint map that is always there (the orb's rise,
 * the top pipeline, one rail per region and a branch into each object on
 * show), and over it any white attention cue under way (uiAttention.ts):
 * its whole route faint, three bright pulses running along it, two rings
 * where it arrives, the target's contour, then the fade.
 *
 * Two layers, both pointer-transparent: the map and the routes run behind
 * the side columns, as the transmissions' rays do (so the rise comes out
 * from inside the orb); a part's leaf inside its object, the rings and the
 * contour are drawn above them, so they show on the object itself. Every
 * position is computed from the contract's one timing table, the same one
 * that decides when the request is answered.
 */

import { useEffect, useState, useSyncExternalStore, type CSSProperties } from "react";
import "./uiPipeline.css";
import { cues, TIMING, type CueFrame } from "./uiAttention";
import { layoutRevision, navigationRevision, visibilityOf } from "./uiRegistry";
import { length, mapEdges, pathOf, pointAlong, polyline, socketBranches, type Point, type RouteEdge } from "./uiPipeline";

const HIDDEN: CSSProperties = {
  position: "absolute", width: 1, height: 1, margin: -1, padding: 0, overflow: "hidden",
  clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0,
};
const RING_MS = 700;
const RING_R = 26;
const CONTOUR_PAD = 4;

/** The faint map, measured again whenever the layout may have moved. */
function useMap(): RouteEdge[] {
  const [edges, setEdges] = useState<RouteEdge[]>([]);
  const layout = useSyncExternalStore(layoutRevision.subscribe, layoutRevision.get);
  const nav = useSyncExternalStore(navigationRevision.subscribe, navigationRevision.get);
  useEffect(() => {
    let raf = 0;
    const measure = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => setEdges([
        ...mapEdges(),
        ...socketBranches((t) => visibilityOf(t.element()) === "visible"),
      ]));
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, { capture: true, passive: true });
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, { capture: true });
    };
  }, [layout, nav]);
  return edges;
}

function fadeOf(cue: CueFrame): number {
  if (cue.elapsed <= cue.holdUntil) return 1;
  return Math.max(0, 1 - (cue.elapsed - cue.holdUntil) / (cue.resetBy - cue.holdUntil));
}

/** Where each pulse is, and whether it is inside the object (the leaf). */
function pulses(cue: CueFrame): { at: Point; inside: boolean }[] {
  if (cue.still || cue.elapsed >= TIMING.arrivalAtMs + (TIMING.pulseCount - 1) * TIMING.pulseOffsetMs) return [];
  const total = length(cue.points);
  const outside = length(polyline(cue.route.edges.filter((e) => e.layer !== "leaf")));
  const out: { at: Point; inside: boolean }[] = [];
  for (let i = 0; i < TIMING.pulseCount; i++) {
    const p = (cue.elapsed - TIMING.emitMs - i * TIMING.pulseOffsetMs) / TIMING.travelMs;
    if (p <= 0 || p >= 1) continue;
    out.push({ at: pointAlong(cue.points, p), inside: p * total > outside });
  }
  return out;
}

function Contour({ cue }: { cue: CueFrame }) {
  const b = cue.box!;
  const [x, y, w, h] = [b.left - CONTOUR_PAD, b.top - CONTOUR_PAD, b.width + 2 * CONTOUR_PAD, b.height + 2 * CONTOUR_PAD];
  if (cue.style === "circle") {
    const shape = { cx: x + w / 2, cy: y + h / 2, rx: w / 2 + 4, ry: h / 2 + 4 };
    return <><ellipse {...shape} className="ui-under" /><ellipse {...shape} className="ui-contour" /></>;
  }
  if (cue.style === "brackets") {
    const k = Math.min(12, w / 3, h / 3);
    const d = `M ${x} ${y + k} V ${y} H ${x + k} M ${x + w - k} ${y} H ${x + w} V ${y + k} `
            + `M ${x + w} ${y + h - k} V ${y + h} H ${x + w - k} M ${x + k} ${y + h} H ${x} V ${y + h - k}`;
    return <><path d={d} className="ui-under" /><path d={d} className="ui-contour" /></>;
  }
  const shape = { x, y, width: w, height: h, rx: 5 };
  return <><rect {...shape} className="ui-under" /><rect {...shape} className="ui-contour" /></>;
}

function Rings({ cue }: { cue: CueFrame }) {
  if (cue.still) return null;
  const [cx, cy] = cue.points[cue.points.length - 1];
  return (
    <>
      {[0, 1].map((i) => {
        const t = (cue.elapsed - TIMING.arrivalAtMs - i * TIMING.ringGapMs) / RING_MS;
        if (t <= 0 || t >= 1) return null;
        return <circle key={i} cx={cx} cy={cy} r={4 + t * RING_R} className="ui-ring" style={{ opacity: 1 - t }} />;
      })}
    </>
  );
}

export function UiPipelineLayer() {
  const map = useMap();
  const showing = useSyncExternalStore(cues.subscribe, cues.get);
  const routes = showing.map((cue) => {
    const outside = polyline(cue.route.edges.filter((e) => e.layer !== "leaf"));
    const inside = polyline(cue.route.edges.filter((e) => e.layer === "leaf"));
    // The route appears once the orb has begun to emit; still, at once.
    const lit = cue.still || cue.elapsed >= TIMING.emitMs;
    return { cue, outside, inside, lit, fade: fadeOf(cue), pulses: pulses(cue) };
  });
  return (
    <>
      <svg id="ui-pipeline" className="ui-pipeline" aria-hidden="true">
        <defs>
          <filter id="ui-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.5" /></filter>
        </defs>
        {map.map((e) => <path key={e.id} d={pathOf(e.points)} className="ui-map" data-edge={e.id} />)}
        {routes.map(({ cue, outside, lit, fade, pulses: ps }) => (
          <g key={cue.cueId} data-cue={cue.cueId} style={{ opacity: fade }}>
            {lit && <>
              <path d={pathOf(outside)} className="ui-under" />
              <path d={pathOf(outside)} className="ui-route-glow" filter="url(#ui-glow)" />
              <path d={pathOf(outside)} className="ui-route" />
            </>}
            {ps.filter((p) => !p.inside).map((p, i) => <circle key={i} cx={p.at[0]} cy={p.at[1]} r={3} className="ui-pulse" />)}
          </g>
        ))}
      </svg>
      <svg id="ui-pipeline-over" className="ui-pipeline over" aria-hidden="true">
        {routes.map(({ cue, inside, lit, fade, pulses: ps }) => (
          <g key={cue.cueId} data-cue={cue.cueId} data-arrived={cue.box ? "true" : "false"} style={{ opacity: fade }}>
            {lit && inside.length > 1 && <>
              <path d={pathOf(inside)} className="ui-under" />
              <path d={pathOf(inside)} className="ui-route" />
            </>}
            {ps.filter((p) => p.inside).map((p, i) => <circle key={i} cx={p.at[0]} cy={p.at[1]} r={3} className="ui-pulse" />)}
            <Rings cue={cue} />
            {cue.box && <Contour cue={cue} />}
          </g>
        ))}
      </svg>
      <div id="ui-pipeline-status" role="status" style={HIDDEN}>
        {showing.filter((c) => c.box).map((c) => `JARVIS is pointing at ${c.label}`).join(". ")}
      </div>
    </>
  );
}
