/**
 * JARVIS — Multi-mode particle visualization.
 *
 * Floating particles with line connections between nearby ones.
 * Lines fade in/out based on state. Transition tumble on state change.
 * Speaking pulls particles closer for denser connections.
 */

import * as THREE from "three";
import { createStore } from "./store";

/** Why the orbs cannot be drawn, or null while they can. Chrome switches
 *  WebGL off for the whole browser after its graphics process crashes a few
 *  times (it then runs that process with --use-gl=disabled until Chrome is
 *  restarted), and it can take a context back at any time. Neither may stop
 *  the page: the orbs go still and blank, everything else carries on, and
 *  the Diagnostics tab says so (subsystems.ts). */
export const graphicsTrouble = createStore<string | null>(null);

export const WEBGL_OFF = "Chrome has switched WebGL off, so the orbs are not drawn. Quit and reopen Chrome to turn it back on.";
export const WEBGL_LOST = "Chrome took the orbs' graphics away; they come back when it gives them back.";

/** An orb that draws nothing: what the page gets when WebGL is off. */
const STILL_ORB: Orb = {
  setState() {}, setGray() {}, setPaused() {}, transmit() {}, receive() {},
  endTransmission() {}, setAnalyser() {}, destroy() {},
};

// "compacting": the brain is being swapped for a fresh one behind the scenes
// (a context rotation). He answers nothing for a few seconds, and silence with
// no explanation reads as a crash. It used to be followed by a spoken line;
// the user found that annoying, so the orb carries it instead: dim, slow,
// drawn inward, in a cooler colour -- unmistakably not "about to answer".
export type OrbState = "idle" | "listening" | "thinking" | "speaking" | "compacting";

export interface Orb {
  setState(s: OrbState): void;
  /** Drawn entirely in gray, with no colour, while true. */
  setGray(gray: boolean): void;
  /** Holds still (draws nothing new) while true: the quiet page. */
  setPaused(paused: boolean): void;
  /** The "transmitting" state, laid over whatever state the orb is in: it
   *  spins up, fills with `colour` from the centre out, and its particles
   *  lean toward `direction` (screen x, y; the way the ray leaves), for
   *  `seconds`, easing back to the state it was in over the last of them. */
  transmit(colour: string, options?: { direction?: [number, number]; seconds?: number }): void;
  /** The "receiving" state, the other end of a transmission: the same as
   *  transmitting, except the colour comes in from the edge to the centre,
   *  and `direction` is the way the ray arrives from. */
  receive(colour: string, options?: { direction?: [number, number]; seconds?: number }): void;
  /** Ends a transmitting or receiving state at once. */
  endTransmission(): void;
  setAnalyser(a: AnalyserNode | null): void;
  destroy(): void;
}

/** The colour each state settles to. JARVIS's own is blue. */
export interface OrbPalette {
  idle: number;
  thinking: number;
  speaking: number;
  compacting: number;
}

export const BLUE_PALETTE: OrbPalette = {
  idle: 0x4ca8e8, thinking: 0x6ec4ff, speaking: 0x5ab8f0, compacting: 0x3a5f8a,
};

export interface OrbOptions {
  particles?: number;      // 2000 for the main orb; fewer for a small one
  transparent?: boolean;   // draw on whatever is behind the canvas
  core?: boolean;          // a permanent glowing core at the centre (the main orb)
  palette?: OrbPalette;    // another orb's colours: a workflow's is light red
  /** Drawn by a WebGL renderer of its own (the main orb). Every other orb is
   *  drawn by the one renderer the small orbs share (SharedOrbs), so the
   *  page holds two WebGL contexts however many orbs it shows: Chrome allows
   *  a page sixteen, and takes the oldest away past that. */
  ownRenderer?: boolean;
}

/** One orb as the renderer drawing it sees it. */
interface Drawable {
  readonly canvas: HTMLCanvasElement;
  readonly scene: THREE.Scene;
  readonly camera: THREE.PerspectiveCamera;
  readonly transparent: boolean;
  readonly paused: () => boolean;
  /** Moves the orb on one frame. `pointScale` makes its points the size
   *  they are on a renderer of its own (1) when it is drawn by a renderer
   *  of another size: three.js sizes points by the renderer's height. */
  readonly step: (pointScale: number) => void;
}

const CLEAR_COLOUR = 0x050508;

/** Reports a renderer's context going and coming back (graphicsTrouble);
 *  three.js keeps the scene and redraws it once the context is restored.
 *  Returns how to stop watching. */
function watchContext(canvas: HTMLCanvasElement, onLost = () => {}, onRestored = () => {}) {
  const lost = () => { graphicsTrouble.set(WEBGL_LOST); onLost(); };
  const restored = () => {
    if (graphicsTrouble.get() === WEBGL_LOST) graphicsTrouble.set(null);
    onRestored();
  };
  canvas.addEventListener("webglcontextlost", lost);
  canvas.addEventListener("webglcontextrestored", restored);
  return () => {
    canvas.removeEventListener("webglcontextlost", lost);
    canvas.removeEventListener("webglcontextrestored", restored);
  };
}

function newRenderer(canvas: HTMLCanvasElement, alpha: boolean): THREE.WebGLRenderer | null {
  try {
    return new THREE.WebGLRenderer({ canvas, antialias: true, alpha });
  } catch (e) {
    console.error("orb: no WebGL context", e);
    graphicsTrouble.set(WEBGL_OFF);
    return null;
  }
}

/** Gives a renderer's WebGL context back now, not when it is garbage
 *  collected: a page may only hold a few at once. Its watch is taken off
 *  first, so giving it back is not reported as losing it. */
function release(renderer: THREE.WebGLRenderer, unwatch: () => void) {
  unwatch();
  renderer.dispose();
  renderer.forceContextLoss();
}

/** The main orb's drawing: a renderer on its own canvas, in a loop of its
 *  own. Returns how to stop, or null without WebGL. */
function drawOwn(orb: Drawable): (() => void) | null {
  const renderer = newRenderer(orb.canvas, orb.transparent);
  if (!renderer) return null;
  const unwatch = watchContext(orb.canvas);
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setClearColor(CLEAR_COLOUR, orb.transparent ? 0 : 1);
  const resize = () => renderer.setSize(Math.max(1, orb.canvas.clientWidth),
                                        Math.max(1, orb.canvas.clientHeight), false);
  resize();
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(orb.canvas);
  let stopped = false;
  const animate = () => {
    if (stopped) return;
    requestAnimationFrame(animate);
    if (orb.paused()) return;
    orb.step(1);
    renderer.render(orb.scene, orb.camera);
  };
  animate();
  return () => {
    stopped = true;
    resizeObserver.disconnect();
    release(renderer, unwatch);
  };
}

/** A small orb on the shared renderer: its own canvas, drawn on in 2D. */
interface SharedOrb {
  orb: Drawable;
  context: CanvasRenderingContext2D;
  visible: boolean;
  width: number;    // its canvas's size, in device pixels
  height: number;
}

/**
 * Every small orb, drawn by one WebGL renderer in one loop. The renderer
 * draws on a canvas that is not on the page, each orb in turn in its lower
 * left corner, and each is copied straight onto that orb's own canvas, so
 * every orb keeps its own element where it always was (the rays find their
 * ends by those elements). An orb out of sight still moves but is not drawn.
 * The renderer is made with the first small orb and given back once the
 * last has gone. Losing its context is reported like losing any other, and
 * the orbs it draws go blank until it comes back.
 */
const SharedOrbs = (() => {
  const orbs = new Set<SharedOrb>();
  let renderer: THREE.WebGLRenderer | null = null;
  let unwatch = () => {};
  let lost = false;
  let frame = 0;
  // The renderer's canvas only grows, so it is not made again every frame.
  let bufferWidth = 1, bufferHeight = 1;
  const bufferSize = new THREE.Vector2();

  const blank = (o: SharedOrb) => o.context.clearRect(0, 0, o.width, o.height);

  function start(): boolean {
    if (renderer) return true;
    renderer = newRenderer(document.createElement("canvas"), true);
    if (!renderer) return false;
    unwatch = watchContext(renderer.domElement,
                           () => { lost = true; orbs.forEach(blank); },
                           () => { lost = false; });
    lost = false;
    bufferWidth = bufferHeight = 1;
    // Sizes are in device pixels: each orb sets its own share of them.
    renderer.setPixelRatio(1);
    renderer.setScissorTest(true);
    return true;
  }

  function stop() {
    frame = 0;
    if (renderer) release(renderer, unwatch);
    renderer = null;
  }

  function draw() {
    // Given back a frame after the last orb goes, not at once: a view that
    // swaps its orbs for another's drops them all before it adds the next.
    if (!orbs.size) {
      stop();
      return;
    }
    frame = requestAnimationFrame(draw);
    const r = renderer!;
    for (const o of orbs) {
      if (o.visible) {
        bufferWidth = Math.max(bufferWidth, o.width);
        bufferHeight = Math.max(bufferHeight, o.height);
      }
    }
    r.getSize(bufferSize);
    if (bufferSize.x !== bufferWidth || bufferSize.y !== bufferHeight) {
      r.setSize(bufferWidth, bufferHeight, false);
    }

    for (const o of orbs) {
      if (o.orb.paused()) continue;
      const drawn = o.visible && !lost;
      // On a renderer of its own its points are sized by its canvas's
      // height; here they would be sized by the shared canvas's.
      o.orb.step(drawn ? o.height / bufferHeight : 1);
      if (!drawn) continue;
      r.setViewport(0, 0, o.width, o.height);
      r.setScissor(0, 0, o.width, o.height);
      r.setClearColor(CLEAR_COLOUR, o.orb.transparent ? 0 : 1);
      r.render(o.orb.scene, o.orb.camera);
      // WebGL counts rows from the bottom, the 2D canvas from the top.
      blank(o);
      o.context.drawImage(r.domElement, 0, bufferHeight - o.height, o.width, o.height,
                          0, 0, o.width, o.height);
    }
  }

  return {
    /** Starts drawing `orb`; returns how to stop, or null without WebGL. */
    add(orb: Drawable): (() => void) | null {
      const context = orb.canvas.getContext("2d");
      if (!context || !start()) return null;
      const o: SharedOrb = { orb, context, visible: false, width: 1, height: 1 };
      // The same size its own renderer would give its canvas.
      const resize = () => {
        const ratio = window.devicePixelRatio;
        o.width = orb.canvas.width = Math.max(1, Math.floor(orb.canvas.clientWidth * ratio));
        o.height = orb.canvas.height = Math.max(1, Math.floor(orb.canvas.clientHeight * ratio));
      };
      resize();
      const resizeObserver = new ResizeObserver(resize);
      resizeObserver.observe(orb.canvas);
      const seen = new IntersectionObserver((entries) => {
        o.visible = entries[entries.length - 1].isIntersecting;
      });
      seen.observe(orb.canvas);
      orbs.add(o);
      if (!frame) frame = requestAnimationFrame(draw);
      return () => {
        resizeObserver.disconnect();
        seen.disconnect();
        orbs.delete(o);
        blank(o);
      };
    },
  };
})();

export function createOrb(canvas: HTMLCanvasElement,
                          { particles = 2000, transparent = false, core = false,
                            palette = BLUE_PALETTE, ownRenderer = false }: OrbOptions = {}): Orb {
  const N = particles;

  // The orb fills its canvas, and the canvas is sized by the page's layout,
  // not the window: it is one control on the page.
  const width = () => Math.max(1, canvas.clientWidth);
  const height = () => Math.max(1, canvas.clientHeight);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, width() / height(), 1, 1000);

  // ── Fitting the sphere to its box ──
  // The orb is a sphere around its centre. At its largest -- the widest
  // state's radius, at the nearest the idle breathing brings it -- it should
  // fill FILL of the box's shorter side, and nothing may ever reach the edge.
  // The camera is placed so that is true for the box's current shape, and
  // every frame each particle is held inside the radius that still projects
  // within FILL at the cloud's current depth.
  const FILL = 0.95;
  const R_MAX = 30;          // the largest radius any state settles at, plus its swell
  const Z_NEAREST = 10;      // how near the idle breathing brings the cloud
  const POINT_MARGIN = 0.5;  // a particle is drawn as a dot, not a point
  let fitSin = 1;

  function fitCamera() {
    const halfFov = THREE.MathUtils.degToRad(camera.fov / 2);
    const shorterHalf = Math.tan(halfFov) * Math.min(1, camera.aspect);
    fitSin = Math.sin(Math.atan(FILL * shorterHalf));
    camera.position.set(0, 0, Z_NEAREST + (R_MAX + POINT_MARGIN) / fitSin);
    camera.lookAt(0, 0, 0);
  }
  fitCamera();

  // ── Particles ──
  const geo = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3);
  const vel = new Float32Array(N * 3);
  const phase = new Float32Array(N);

  for (let i = 0; i < N; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = Math.pow(Math.random(), 0.5) * 25;
    pos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);
    phase[i] = Math.random() * 1000;
  }

  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  // Each particle's own colour: normally the state's colour (`base`), and
  // during a transmission the transmission's colour where its wave has
  // reached. The material itself stays white so these show as they are.
  const col = new Float32Array(N * 3);
  geo.setAttribute("color", new THREE.BufferAttribute(col, 3));

  const base = new THREE.Color(palette.idle);
  const mat = new THREE.PointsMaterial({
    color: 0xffffff, vertexColors: true, size: 0.4, transparent: true, opacity: 0.6,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const points = new THREE.Points(geo, mat);
  scene.add(points);

  // ── Connection lines ──
  const MAX_LINES = 8000;
  const linePos = new Float32Array(MAX_LINES * 6);
  const lineGeo = new THREE.BufferGeometry();
  lineGeo.setAttribute("position", new THREE.BufferAttribute(linePos, 3));
  lineGeo.setDrawRange(0, 0);

  const lineMat = new THREE.LineBasicMaterial({
    color: palette.idle, transparent: true, opacity: 0.0,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const lines = new THREE.LineSegments(lineGeo, lineMat);
  scene.add(lines);

  // ── Electrons — bright dots that travel along connections ──
  const MAX_ELECTRONS = 200;
  const electronGeo = new THREE.BufferGeometry();
  const electronPos = new Float32Array(MAX_ELECTRONS * 3);
  electronGeo.setAttribute("position", new THREE.BufferAttribute(electronPos, 3));
  electronGeo.setDrawRange(0, 0);

  const ELECTRON_SIZE = 0.8;
  const electronMat = new THREE.PointsMaterial({
    color: 0xffffff, size: ELECTRON_SIZE, transparent: true, opacity: 1.0,
    sizeAttenuation: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });

  const electrons = new THREE.Points(electronGeo, electronMat);
  scene.add(electrons);

  // ── The core ──
  // The central 2% of the orb, at its nexus: always lit, whatever the state.
  // It wears the transmission's colour while one is going out, and the
  // colour wave and the ray both start from it.
  const CORE_RADIUS = 0.02 * 2 * R_MAX;
  const CORE_COLOUR = new THREE.Color(0xd8f3ff);
  // Solid and drawn last, not added like the particles: added on top of the
  // dense centre it saturated to white and its colour was lost. It is marked
  // transparent (at full opacity) only so it is drawn in the same pass as the
  // particles, where renderOrder can put it after them; a solid object is
  // always drawn first and the particles were then added over it.
  const coreMat = new THREE.MeshBasicMaterial({
    color: CORE_COLOUR, transparent: true, opacity: 1, depthTest: false, depthWrite: false });
  const coreHaloMat = new THREE.MeshBasicMaterial({
    color: CORE_COLOUR, transparent: true, opacity: 0.25,
    blending: THREE.AdditiveBlending, depthWrite: false });
  const coreMesh = new THREE.Mesh(new THREE.SphereGeometry(CORE_RADIUS, 16, 12), coreMat);
  coreMesh.renderOrder = 10;
  const coreHalo = new THREE.Mesh(new THREE.SphereGeometry(CORE_RADIUS * 2.2, 16, 12), coreHaloMat);
  if (core) {
    scene.add(coreHalo);
    scene.add(coreMesh);
  }

  // Each electron: start point, end point, progress (0-1), speed
  interface Electron { sx: number; sy: number; sz: number; ex: number; ey: number; ez: number; t: number; speed: number; }
  const activeElectrons: Electron[] = [];
  let electronSpawnRate = 0;
  let targetElectronRate = 0;
  let lastElectronSpawn = 0; // timestamp of last spawn

  // Store active connections for electron spawning
  let activeConnections: { x1: number; y1: number; z1: number; x2: number; y2: number; z2: number }[] = [];

  // ── State ──
  let state: OrbState = "idle";
  let gray = false;
  // The transmitting or receiving state under way, if any (see transmit()
  // and receive()). It never touches `state`, so when it ends the orb is in
  // exactly the state it was.
  let tx: { colour: THREE.Color; start: number; seconds: number;
            dir: THREE.Vector3; inward: boolean } | null = null;
  const TX_RISE_SEC = 0.6;        // eases in this fast
  const TX_FADE_SEC = 1.5;        // and back out over the last this long
  const TX_WAVE_SEC = 0.8;        // the colour crosses the orb in this long
  const TX_WAVE_REACH = R_MAX * 1.2;   // as far as the wave goes
  const TX_COLOUR = 0.95;         // how much of the colour the orb takes
  const TX_SHIFT = 5;             // how far the cloud leans toward the ray, at full strength
  const TX_SPIN = 0.03;           // extra turn a frame at full strength
  const TX_DEFAULT_SEC = 4.4;
  // Where the cloud sits this frame: leaned toward the ray while
  // transmitting, centred otherwise. The core never moves.
  const cloudShift = new THREE.Vector3();
  // The lines' own state colour; while transmitting they show the ray's
  // colour over it, like the particles.
  const lineBase = new THREE.Color(palette.idle);
  const stateColours = {
    idle: new THREE.Color(palette.idle), thinking: new THREE.Color(palette.thinking),
    speaking: new THREE.Color(palette.speaking), compacting: new THREE.Color(palette.compacting),
  };
  const mixed = new THREE.Color();
  const THINKING_SPIN = 0.012;   // radians a frame
  const GRAY = 0x8a8a8a;
  let targetRadius = 25, currentRadius = 25;
  let targetSpeed = 0.3, currentSpeed = 0.3;
  let targetBright = 0.6, currentBright = 0.6;
  let targetSize = 0.4, currentSize = 0.4;
  let lineAmount = 0, targetLineAmount = 0;
  let lineDistance = 8;

  // Transition tumble
  let spinX = 0, spinY = 0, spinZ = 0;
  let transitionEnergy = 0;
  let lastState: OrbState = "idle";

  // Depth Z
  let cloudZ = 0, cloudZVel = 0;

  // ── Audio ──
  let analyser: AnalyserNode | null = null;
  let freqData = new Uint8Array(64);
  let bass = 0, mid = 0;

  const clock = new THREE.Clock();

  let paused = false;

  function step(pointScale: number) {
    const t = clock.getElapsedTime();

    switch (state) {
      // Idle and thinking must read as different at a glance: idle barely
      // drifts, thinking churns and turns (see THINKING_SPIN below).
      case "idle":
        targetRadius = 28; targetSpeed = 0.05; targetBright = 0.5; targetSize = 0.35;
        targetLineAmount = 0.15; targetElectronRate = 0; break;
      case "listening":
        targetRadius = 22; targetSpeed = 0.3; targetBright = 0.65; targetSize = 0.4;
        targetLineAmount = 0.4; targetElectronRate = 0; break;
      case "thinking":
        targetRadius = 16; targetSpeed = 1.6; targetBright = 0.7; targetSize = 0.3;
        targetLineAmount = 1.0; targetElectronRate = 0.015; break;
      case "speaking":
        targetRadius = 18; targetSpeed = 0.2; targetBright = 0.7; targetSize = 0.4;
        targetLineAmount = 0.8; targetElectronRate = 0; break;
      case "compacting":
        // Smaller than idle, slower than anything, half the brightness, no
        // connecting lines: a held breath, not a working mind.
        targetRadius = 12; targetSpeed = 0.08; targetBright = 0.28; targetSize = 0.28;
        targetLineAmount = 0.0; targetElectronRate = 0; break;
    }

    currentRadius += (targetRadius - currentRadius) * 0.02;
    currentSpeed += (targetSpeed - currentSpeed) * 0.02;
    currentBright += (targetBright - currentBright) * 0.02;
    currentSize += (targetSize - currentSize) * 0.02;
    lineAmount += (targetLineAmount - lineAmount) * 0.02;
    electronSpawnRate += (targetElectronRate - electronSpawnRate) * 0.02;

    // Transition energy
    if (state !== lastState) { transitionEnergy = 1.0; lastState = state; }
    transitionEnergy *= 0.985;
    if (transitionEnergy > 0.05) {
      spinX += transitionEnergy * 0.012 * Math.sin(t * 1.7);
      spinY += transitionEnergy * 0.015;
      spinZ += transitionEnergy * 0.008 * Math.cos(t * 1.3);
    }
    // Transmitting: eases in, holds, and eases back out by the time the ray
    // fades, spinning up as it goes.
    let txWave = -1, txStrength = 0;
    if (tx) {
      const since = t - tx.start;
      if (since >= tx.seconds) {
        tx = null;
      } else {
        txWave = (since / TX_WAVE_SEC) * TX_WAVE_REACH;
        txStrength = Math.min(1, since / TX_RISE_SEC,
                              (tx.seconds - since) / TX_FADE_SEC);
        spinY += TX_SPIN * txStrength;
      }
    }

    // Thinking keeps turning for as long as it lasts, not only as it starts.
    if (state === "thinking") {
      spinY += THINKING_SPIN;
      spinX += THINKING_SPIN * 0.4 * Math.sin(t * 0.7);
    }

    // Audio
    bass = 0; mid = 0;
    if (analyser) {
      analyser.getByteFrequencyData(freqData);
      let bSum = 0, mSum = 0;
      for (let i = 0; i < 8; i++) bSum += freqData[i];
      for (let i = 8; i < 24; i++) mSum += freqData[i];
      bass = bSum / (8 * 255); mid = mSum / (16 * 255);
    }

    // Depth Z breathing
    let zTarget = Math.sin(t * 0.12) * 8;
    if (state === "idle") zTarget = Math.sin(t * 0.12) * 2;   // still, a slow breath
    else if (state === "thinking") zTarget = Math.sin(t * 0.3) * 15 + Math.sin(t * 0.9) * 6;
    else if (state === "speaking") zTarget = Math.sin(t * 0.15) * 6 - bass * 10;
    else if (state === "compacting") zTarget = -20 + Math.sin(t * 0.08) * 3;   // withdrawn, barely moving
    cloudZVel += (zTarget - cloudZ) * 0.008;
    cloudZVel *= 0.94;
    cloudZ += cloudZVel;

    // Transmitting: the cloud leans toward the ray, around the core.
    if (tx) cloudShift.copy(tx.dir).multiplyScalar(TX_SHIFT * txStrength);
    else cloudShift.set(0, 0, 0);

    points.rotation.x = spinX; points.rotation.y = spinY; points.rotation.z = spinZ;
    points.position.set(cloudShift.x, cloudShift.y, cloudZ);
    lines.rotation.x = spinX; lines.rotation.y = spinY; lines.rotation.z = spinZ;
    lines.position.set(cloudShift.x, cloudShift.y, cloudZ);

    // ── Update particles ──
    const p = geo.getAttribute("position") as THREE.BufferAttribute;
    const a = p.array as Float32Array;
    // The largest radius that still projects within FILL of the box at the
    // cloud's depth this frame.
    // Less by the lean, so a leaning cloud still stays inside the box.
    const maxR = Math.min(R_MAX, (camera.position.z - cloudZ) * fitSin - POINT_MARGIN)
      - cloudShift.length();

    for (let i = 0; i < N; i++) {
      const i3 = i * 3;
      let x = a[i3], y = a[i3 + 1], z = a[i3 + 2];
      const px = phase[i];

      vel[i3] += Math.sin(t * 0.05 + px) * 0.001 * currentSpeed;
      vel[i3 + 1] += Math.cos(t * 0.06 + px * 1.3) * 0.001 * currentSpeed;
      vel[i3 + 2] += Math.sin(t * 0.055 + px * 0.7) * 0.001 * currentSpeed;
      vel[i3] += Math.sin(t * 0.02 + px * 2.1 + y * 0.1) * 0.0008 * currentSpeed;
      vel[i3 + 1] += Math.cos(t * 0.025 + px * 1.7 + z * 0.1) * 0.0008 * currentSpeed;
      vel[i3 + 2] += Math.sin(t * 0.022 + px * 0.9 + x * 0.1) * 0.0008 * currentSpeed;

      const dist = Math.sqrt(x * x + y * y + z * z) || 0.01;
      // The steady inward drift scales with the state's speed, so a still
      // state is still; the pull back from beyond the radius does not.
      const pull = Math.max(0, dist - currentRadius) * 0.002 + 0.001 * currentSpeed;
      vel[i3] -= (x / dist) * pull;
      vel[i3 + 1] -= (y / dist) * pull;
      vel[i3 + 2] -= (z / dist) * pull;

      if (bass > 0.05) {
        vel[i3] += (x / dist) * bass * 0.02;
        vel[i3 + 1] += (y / dist) * bass * 0.02;
        vel[i3 + 2] += (z / dist) * bass * 0.02;
      }
      if (state === "speaking" && mid > 0.1) {
        const pulse = Math.sin(t * 8 + px);
        vel[i3] += (x / dist) * mid * 0.012 * pulse;
        vel[i3 + 1] += (y / dist) * mid * 0.012 * pulse;
      }

      vel[i3] *= 0.992; vel[i3 + 1] *= 0.992; vel[i3 + 2] *= 0.992;
      a[i3] += vel[i3]; a[i3 + 1] += vel[i3 + 1]; a[i3 + 2] += vel[i3 + 2];

      // Never past the sphere that fits the box (see fitCamera). A particle
      // that reaches it stays on it, and loses its outward speed.
      const r = Math.sqrt(a[i3] * a[i3] + a[i3 + 1] * a[i3 + 1] + a[i3 + 2] * a[i3 + 2]);
      if (r > maxR) {
        const k = maxR / r;
        a[i3] *= k; a[i3 + 1] *= k; a[i3 + 2] *= k;
        const outward = (vel[i3] * a[i3] + vel[i3 + 1] * a[i3 + 1] + vel[i3 + 2] * a[i3 + 2]) / maxR;
        if (outward > 0) {
          vel[i3] -= outward * a[i3] / maxR;
          vel[i3 + 1] -= outward * a[i3 + 1] / maxR;
          vel[i3 + 2] -= outward * a[i3 + 2] / maxR;
        }
      }
    }
    p.needsUpdate = true;

    // ── Particle colours: the state's colour, and the transmission's colour
    // inside its wave (a soft edge a few units wide): out from the centre
    // when transmitting, in from the edge when receiving ──
    const ca = (geo.getAttribute("color") as THREE.BufferAttribute).array as Float32Array;
    for (let i = 0; i < N; i++) {
      const i3 = i * 3;
      let w = 0;
      if (tx && txWave >= 0) {
        const d = Math.sqrt(a[i3] * a[i3] + a[i3 + 1] * a[i3 + 1] + a[i3 + 2] * a[i3 + 2]);
        const reached = tx.inward ? d - (TX_WAVE_REACH - txWave) : txWave - d;
        w = Math.min(1, Math.max(0, reached / 4)) * txStrength * TX_COLOUR;
      }
      if (w > 0) mixed.copy(base).lerp(tx!.colour, w);
      else mixed.copy(base);
      ca[i3] = mixed.r; ca[i3 + 1] = mixed.g; ca[i3 + 2] = mixed.b;
    }
    geo.getAttribute("color").needsUpdate = true;

    // ── Update lines ──
    if (lineAmount > 0.01) {
      const lp = lineGeo.getAttribute("position") as THREE.BufferAttribute;
      const la = lp.array as Float32Array;
      let lineCount = 0;
      const maxDist = lineDistance * (1 + bass * 0.5);
      const maxDistSq = maxDist * maxDist;
      const step = Math.max(1, Math.floor(N / 600));

      for (let i = 0; i < N && lineCount < MAX_LINES; i += step) {
        const i3 = i * 3;
        const x1 = a[i3], y1 = a[i3 + 1], z1 = a[i3 + 2];
        for (let j = i + step; j < N && lineCount < MAX_LINES; j += step) {
          const j3 = j * 3;
          const dx = a[j3] - x1, dy = a[j3 + 1] - y1, dz = a[j3 + 2] - z1;
          if (dx * dx + dy * dy + dz * dz < maxDistSq) {
            const idx = lineCount * 6;
            la[idx] = x1; la[idx+1] = y1; la[idx+2] = z1;
            la[idx+3] = a[j3]; la[idx+4] = a[j3+1]; la[idx+5] = a[j3+2];
            lineCount++;
          }
        }
      }
      lineGeo.setDrawRange(0, lineCount * 2);
      lp.needsUpdate = true;
      lineMat.opacity = lineAmount * 0.12;

      // Store connections for electron spawning
      activeConnections = [];
      for (let c = 0; c < Math.min(lineCount, 500); c++) {
        const ci = c * 6;
        activeConnections.push({
          x1: la[ci], y1: la[ci+1], z1: la[ci+2],
          x2: la[ci+3], y2: la[ci+4], z2: la[ci+5],
        });
      }
    } else {
      lineGeo.setDrawRange(0, 0);
      activeConnections = [];
    }

    // ── Update electrons — only during thinking ──
    // One fires off every ~1 second, max 3 alive, takes 2-4s to travel
    if (activeConnections.length > 0 && electronSpawnRate > 0.005) {
      if (activeElectrons.length < 3 && (t - lastElectronSpawn) > 1.0) {
        const conn = activeConnections[Math.floor(Math.random() * activeConnections.length)];
        // speed: 1/fps * speed = progress per frame. At 60fps, speed 0.005 = 200 frames = 3.3s
        activeElectrons.push({
          sx: conn.x1, sy: conn.y1, sz: conn.z1,
          ex: conn.x2, ey: conn.y2, ez: conn.z2,
          t: 0,
          speed: 0.003 + Math.random() * 0.003, // 2-4 seconds to travel
        });
        lastElectronSpawn = t;
      }
    }

    // Update electron positions
    const ep = electronGeo.getAttribute("position") as THREE.BufferAttribute;
    const ea = ep.array as Float32Array;
    let aliveCount = 0;

    for (let e = activeElectrons.length - 1; e >= 0; e--) {
      const el = activeElectrons[e];
      el.t += el.speed;
      if (el.t >= 1) {
        activeElectrons.splice(e, 1);
        continue;
      }
      const ei = aliveCount * 3;
      ea[ei] = el.sx + (el.ex - el.sx) * el.t;
      ea[ei + 1] = el.sy + (el.ey - el.sy) * el.t;
      ea[ei + 2] = el.sz + (el.ez - el.sz) * el.t;
      aliveCount++;
    }

    electronGeo.setDrawRange(0, aliveCount);
    ep.needsUpdate = true;

    // Electrons follow the same rotation/position as the main group
    electrons.rotation.x = spinX; electrons.rotation.y = spinY; electrons.rotation.z = spinZ;
    electrons.position.set(cloudShift.x, cloudShift.y, cloudZ);
    coreMesh.position.z = cloudZ;
    coreHalo.position.z = cloudZ;

    mat.opacity = currentBright + bass * 0.08;
    mat.size = (currentSize + bass * 0.05) * pointScale;
    electronMat.size = ELECTRON_SIZE * pointScale;

    if (gray) { base.set(GRAY); lineBase.set(GRAY); }
    else if (state === "thinking") { base.lerp(stateColours.thinking, 0.015); lineBase.lerp(stateColours.thinking, 0.015); }
    else if (state === "speaking") { base.lerp(stateColours.speaking, 0.015); lineBase.lerp(stateColours.speaking, 0.015); }
    else if (state === "compacting") { base.lerp(stateColours.compacting, 0.03); lineBase.lerp(stateColours.compacting, 0.03); }   // desaturated, cooler
    else { base.lerp(stateColours.idle, 0.015); lineBase.lerp(stateColours.idle, 0.015); }

    lineMat.color.copy(lineBase);
    if (tx) lineMat.color.lerp(tx.colour, txStrength * TX_COLOUR);

    // The core: its own pale colour, or the transmission's while one is going.
    const coreColour = tx ? mixed.copy(CORE_COLOUR).lerp(tx.colour, txStrength) : CORE_COLOUR;
    coreMat.color.copy(coreColour);
    coreHaloMat.color.copy(coreColour);
  }

  function onResize() {
    camera.aspect = width() / height();
    camera.updateProjectionMatrix();
    fitCamera();
  }

  function startTransmission(colour: string,
                             options: { direction?: [number, number]; seconds?: number },
                             inward: boolean) {
    const [dx, dy] = options.direction ?? [0, 0];
    tx = { colour: new THREE.Color(colour), start: clock.getElapsedTime(),
           seconds: options.seconds ?? TX_DEFAULT_SEC,
           // Screen y runs down; the scene's y runs up.
           dir: new THREE.Vector3(dx, -dy, 0).normalize(), inward };
  }

  // What this orb holds on the GPU, given back when it goes.
  function dispose() {
    for (const held of [geo, mat, lineGeo, lineMat, electronGeo, electronMat,
                        coreMat, coreHaloMat, coreMesh.geometry, coreHalo.geometry]) {
      held.dispose();
    }
  }

  const drawable: Drawable = { canvas, scene, camera, transparent, paused: () => paused, step };
  const stopDrawing = ownRenderer ? drawOwn(drawable) : SharedOrbs.add(drawable);
  if (!stopDrawing) {
    dispose();
    return STILL_ORB;
  }
  const resizeObserver = new ResizeObserver(onResize);
  resizeObserver.observe(canvas);

  return {
    setState(s: OrbState) { state = s; },
    setGray(g: boolean) { gray = g; },
    setPaused(p: boolean) { paused = p; },
    transmit(colour: string, options = {}) { startTransmission(colour, options, false); },
    receive(colour: string, options = {}) { startTransmission(colour, options, true); },
    endTransmission() { tx = null; },
    setAnalyser(a: AnalyserNode | null) {
      analyser = a;
      if (a) freqData = new Uint8Array(a.frequencyBinCount);
    },
    destroy() {
      resizeObserver.disconnect();
      stopDrawing();
      dispose();
    },
  };
}
