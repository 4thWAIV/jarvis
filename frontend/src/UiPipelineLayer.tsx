/**
 * The pipeline drawn: nothing at rest. The fixed map (the orb's rise, the
 * top pipeline, one rail per region, a branch into each object) is data
 * only (uiPipeline.ts), which routing and hit-testing use; none of it is
 * drawn while no cue runs. A white attention cue under way (uiAttention.ts)
 * draws its own route alone: the route faint, three bright pulses running
 * along it, two rings where it arrives, the target's contour, then the
 * fade, after which nothing of it is left. A cue waiting for a real
 * transmission to give the orb back shows its route dashed until it can go.
 *
 * Three layers, all pointer-transparent. A route's run behind the side
 * columns, as the transmissions' rays do (so it comes out from inside the
 * orb). A part's leaf inside its object, the rings and the contour are
 * drawn above the columns, so they show on the object itself. An open
 * modal's rail, branch and leaf are drawn in a layer above the modal, in
 * the same page coordinates. Every position is computed from the one
 * schedule (uiSignal.ts) that also decides when the request is answered.
 */

import { useSyncExternalStore } from "react";
import "./uiPipeline.css";
import { VISUALLY_HIDDEN } from "./effectLife";
import { useT, type Translate } from "./locale";
import { orbSource } from "./orbSource";
import { cues, type CueFrame } from "./uiAttention";
import { polyline, type RouteEdge } from "./uiPipeline";
import {
  fadeAt, length, pathOf, pointAlong, pulseFractions, ringProgress, routeLit, type Point,
} from "./uiSignal";

const RING_R = 26;
const CONTOUR_PAD = 4;

type Layer = "base" | "over" | "modal";
const LAYERS: Layer[] = ["base", "over", "modal"];

/** Which of the three layers an edge is drawn in. */
const layerOf = (e: RouteEdge): Layer => e.host === "modal" ? "modal" : e.layer === "leaf" ? "over" : "base";

/** Each pulse still travelling, and the layer it is in now. */
function pulses(cue: CueFrame): { at: Point; layer: Layer }[] {
  if (cue.still || cue.queued) return [];
  const runs = cue.route.edges.map((e) => ({ layer: layerOf(e), run: length(e.points) }));
  const total = length(cue.points);
  return pulseFractions(cue.elapsed).map((p) => {
    let d = p * total;
    let layer: Layer = runs[runs.length - 1].layer;
    for (const r of runs) {
      if (d <= r.run) { layer = r.layer; break; }
      d -= r.run;
    }
    return { at: pointAlong(cue.points, p), layer };
  });
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
  if (cue.still || cue.queued) return null;
  const [cx, cy] = cue.points[cue.points.length - 1];
  return (
    <>
      {ringProgress(cue.elapsed).map((t, i) => t === null ? null
        : <circle key={i} cx={cx} cy={cy} r={4 + t * RING_R} className="ui-ring" style={{ opacity: 1 - t }} />)}
    </>
  );
}

/** One cue's share of one layer. */
function CueInLayer({ cue, layer }: { cue: CueFrame; layer: Layer }) {
  const edges = cue.route.edges.filter((e) => layerOf(e) === layer);
  const line = polyline(edges);
  const endLayer = layerOf(cue.route.edges[cue.route.edges.length - 1]);
  const ends = layer === (endLayer === "base" ? "over" : endLayer);
  const lit = routeLit(cue.elapsed, cue.still);
  const moving = pulses(cue).filter((p) => p.layer === layer);
  if (!line.length && !moving.length && !ends) return null;
  return (
    <g data-cue={cue.cueId} data-queued={cue.queued ? "true" : "false"} data-edges={edges.map((e) => e.id).join(" ")}
       data-arrived={ends ? (cue.box ? "true" : "false") : undefined}
       style={{ opacity: fadeAt(cue.elapsed, cue.schedule) }}>
      {line.length > 1 && cue.queued && <path d={pathOf(line)} className="ui-route-queued" />}
      {line.length > 1 && !cue.queued && lit && <>
        <path d={pathOf(line)} className="ui-under" />
        {layer === "base" && <path d={pathOf(line)} className="ui-route-glow" filter="url(#ui-glow)" />}
        <path d={pathOf(line)} className="ui-route" />
      </>}
      {moving.map((p, i) => <circle key={i} cx={p.at[0]} cy={p.at[1]} r={3} className="ui-pulse" />)}
      {ends && <Rings cue={cue} />}
      {ends && cue.box && <Contour cue={cue} />}
    </g>
  );
}

/** The attention status line, one sentence per cue: what a screen reader
 *  hears while the pipeline points. A target's name is its own. */
function spoken(t: Translate, showing: CueFrame[], waiting: number): string {
  const words = showing.map((c) => c.queued ? t("main.attention.queued", { target: c.label })
    : c.box ? t("main.attention.pointing", { target: c.label }) : "").filter(Boolean);
  if (waiting && !showing.some((c) => c.queued)) words.push(t("main.attention.waiting"));
  return words.join(" ");
}

export function UiPipelineLayer() {
  const t = useT();
  const showing = useSyncExternalStore(cues.subscribe, cues.get);
  const source = useSyncExternalStore(orbSource.subscribe, orbSource.get);
  return (
    <>
      {LAYERS.map((layer) => (
        <svg key={layer} id={layer === "base" ? "ui-pipeline" : `ui-pipeline-${layer}`}
             className={`ui-pipeline ${layer}`} aria-hidden="true"
             data-source={layer === "base" ? source.holder ?? "free" : undefined}
             data-waiting={layer === "base" ? source.waiting : undefined}>
          {layer === "base" && (
            <defs>
              <filter id="ui-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.5" /></filter>
            </defs>
          )}
          {showing.map((cue) => <CueInLayer key={cue.cueId} cue={cue} layer={layer} />)}
        </svg>
      ))}
      <div id="ui-pipeline-status" role="status" style={VISUALLY_HIDDEN}>{spoken(t, showing, source.waiting)}</div>
    </>
  );
}
