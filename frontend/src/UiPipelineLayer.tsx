/**
 * The pipeline drawn: the faint map that is always there (the orb's rise,
 * the top pipeline, one rail per region and a branch into each object on
 * show), and over it any white attention cue under way (uiAttention.ts):
 * its whole route faint, three bright pulses running along it, two rings
 * where it arrives, the target's contour, then the fade. A cue waiting for
 * a real transmission to give the orb back shows its route dashed until it
 * can go.
 *
 * Three layers, all pointer-transparent. The map and the routes run behind
 * the side columns, as the transmissions' rays do (so the rise comes out
 * from inside the orb). A part's leaf inside its object, the rings and the
 * contour are drawn above the columns, so they show on the object itself.
 * An open modal's rail, sockets and leaves are drawn in a layer above the
 * modal, in the same page coordinates. Every position is computed from the
 * one schedule (uiSignal.ts) that also decides when the request is answered.
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import "./uiPipeline.css";
import { EffectLife, onGeometryChange, VISUALLY_HIDDEN } from "./effectLife";
import { orbSource } from "./orbSource";
import { cues, type CueFrame } from "./uiAttention";
import { visibilityOf } from "./uiRegistry";
import { mapEdges, polyline, socketBranches, type RouteEdge } from "./uiPipeline";
import {
  fadeAt, length, pathOf, pointAlong, pulseFractions, ringProgress, routeLit, type Point,
} from "./uiSignal";

const RING_R = 26;
const CONTOUR_PAD = 4;

type Layer = "base" | "over" | "modal";
const LAYERS: Layer[] = ["base", "over", "modal"];

/** Which of the three layers an edge is drawn in. */
const layerOf = (e: RouteEdge): Layer => e.host === "modal" ? "modal" : e.layer === "leaf" ? "over" : "base";

const sameEdges = (a: RouteEdge[], b: RouteEdge[]): boolean =>
  a.length === b.length && a.every((e, i) => e.id === b[i].id && e.host === b[i].host
    && pathOf(e.points) === pathOf(b[i].points));

/** The faint map, measured again whenever the layout may have moved (once
 *  per frame at most). */
function useMap(): RouteEdge[] {
  const [edges, setEdges] = useState<RouteEdge[]>([]);
  useEffect(() => {
    let life: EffectLife | null = null;
    const measure = () => {
      if (life && !life.ended) return;
      life = new EffectLife();
      const mine = life;
      mine.frame(() => {
        mine.end();
        const next = [...mapEdges(), ...socketBranches((t) => visibilityOf(t.element()) === "visible")];
        // Drawn again only when something moved: a list committing every
        // frame must not redraw an unchanged map every frame.
        setEdges((now) => sameEdges(now, next) ? now : next);
      });
    };
    measure();
    const stop = onGeometryChange(measure);
    return () => { stop(); life?.end(); };
  }, []);
  return edges;
}

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
    <g data-cue={cue.cueId} data-queued={cue.queued ? "true" : "false"}
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

function spoken(showing: CueFrame[], waiting: number): string {
  const words = showing.map((c) => c.queued ? `Waiting for the orb to point at ${c.label}`
    : c.box ? `JARVIS is pointing from the orb to ${c.label}` : "").filter(Boolean);
  if (waiting && !showing.some((c) => c.queued)) words.push("Waiting for the orb");
  return words.join(". ");
}

export function UiPipelineLayer() {
  const map = useMap();
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
          {map.filter((e) => layerOf(e) === layer).map((e) => <path key={e.id} d={pathOf(e.points)} className="ui-map" data-edge={e.id} />)}
          {showing.map((cue) => <CueInLayer key={cue.cueId} cue={cue} layer={layer} />)}
        </svg>
      ))}
      <div id="ui-pipeline-status" role="status" style={VISUALLY_HIDDEN}>{spoken(showing, source.waiting)}</div>
    </>
  );
}
