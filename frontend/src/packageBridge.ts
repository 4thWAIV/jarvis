/**
 * The bridge between a page package's frame (PackageView.tsx) and JARVIS.
 *
 * The frame is sandboxed with `allow-scripts` alone, so it has no origin:
 * it can reach none of JARVIS's routes, and Chrome will not give it the
 * microphone (getUserMedia fails "Invalid security origin" even with
 * `allow="microphone"`). What it may do, it asks for by postMessage to its
 * parent, and only messages from that frame's own window are heard. Every
 * message both ways carries `jarvis: "package"`; docs/work-packages.md has
 * the protocol.
 *
 *   save    {name, data, id?}  data a string (UTF-8), ArrayBuffer, typed
 *                              array or Blob; written as results/<name>
 *                              (packages_api.py), answered with `saved`
 *                              {id, name, ok, saved, bytes} or {.., ok:
 *                              false, error}
 *   listen  {on}               the page's own microphone (voiceInput.ts),
 *                              answered with `listening` {on, microphone,
 *                              sampleRate, channels, format}, then, while
 *                              on, one `audio` {pcm, sampleRate, ...} per
 *                              100 ms frame: PCM16 little-endian mono
 *
 * And the story (a page package with one, told by JARVIS's voice): JARVIS
 * posts `story.cue` {beat, targets, actions} as a beat's words start
 * playing and `story.clear` as they end or the voice stops; the frame's
 * helper (jarvis/package_story.js) points at the targets. The frame says
 * `story.ready` once its helper has loaded (the beat playing now is posted
 * again) and `story.missing` {beat, targets} for selectors it could not find.
 * JARVIS also posts `story.state` {state, slide, question?} whenever the
 * story's state changes (telling, waiting for his answer, paused, finished,
 * stopped), so a deck can show which question is waiting for him.
 *
 * And the room it has (takeover mode, uiNavigation.ts `layout`): JARVIS
 * posts `layout` {mode, width, height} once the frame has loaded and
 * whenever the layout or the frame's size changes (the frame's own page
 * has had its ordinary resize event by then); `mode` is "takeover" while
 * this package has taken over the page, else "normal". The frame may ask
 * for the room with `layout.request` {}: JARVIS offers Take over, as it
 * does for a package marked "wide", and changes nothing itself; he
 * chooses. `layout.restore` {} (the story helper passing on Option+Shift+T
 * pressed inside the frame) restores the normal layout while this package
 * has taken over, and does nothing else.
 */

import { t, type Said } from "./locale";
import { inputStatus } from "./mic";
import { saveResult, storyCue, storyState } from "./packages";
import { layout } from "./uiNavigation";
import { byHand } from "./uiRegistry";
import { listenToMicrophone, MICROPHONE_FORMAT } from "./voiceInput";

export const BRIDGE = "package";

export interface BridgeEvents {
  /** A save finished: its words for the view. */
  onSaved(said: Said): void;
  onListening(on: boolean): void;
  /** The story's frame could not find some of a beat's targets. */
  onStoryMissing(said: Said): void;
  /** The frame asked for more room: offer Take over. */
  onLayoutRequest(): void;
}

/** Whether this package is what has taken over the page. */
const tookOver = (packageId: string) => {
  const now = layout.get();
  return now.mode === "takeover" && now.view === "package" && now.key === packageId;
};

function asBlob(data: unknown): Blob | null {
  if (typeof data === "string" || data instanceof ArrayBuffer) return new Blob([data]);
  if (ArrayBuffer.isView(data)) {
    return new Blob([new Uint8Array(data.buffer, data.byteOffset, data.byteLength).slice()]);
  }
  return data instanceof Blob ? data : null;
}

/** Hear `frame`'s requests for package `packageId`; returns the way to stop. */
export function connectBridge(frame: HTMLIFrameElement, packageId: string,
                              events: BridgeEvents): () => void {
  let stopListening: (() => void) | null = null;
  const post = (message: Record<string, unknown>, transfer: Transferable[] = []) =>
    frame.contentWindow?.postMessage({ jarvis: BRIDGE, ...message }, "*", transfer);

  const listen = (on: boolean) => {
    stopListening?.();
    stopListening = on
      ? listenToMicrophone((pcm) => post({ type: "audio", pcm, ...MICROPHONE_FORMAT }, [pcm]))
      : null;
    post({ type: "listening", on, microphone: inputStatus.get().state, ...MICROPHONE_FORMAT });
    events.onListening(on);
  };

  const save = async (message: Record<string, unknown>) => {
    const name = message.name;
    const reply = (fields: Record<string, unknown>) =>
      post({ type: "saved", id: message.id, name, ...fields });
    const data = asBlob(message.data);
    if (typeof name !== "string" || !data) {
      const error = "A result needs a name and its data: a string, an ArrayBuffer, a typed " +
        "array or a Blob.";
      reply({ ok: false, error });
      events.onSaved({ words: () => t("main.package.notSaved"), detail: error });
      return;
    }
    try {
      const saved = await saveResult(packageId, name, data);
      reply({ ok: true, ...saved });
      events.onSaved({ words: () => t("main.package.saved", { name: saved.saved }) });
    } catch (e) {
      const error = (e as Error).message;
      reply({ ok: false, error });
      events.onSaved({ words: () => t("main.package.notSaved"), detail: error });
    }
  };

  // The beat playing now, if it is this package's, else nothing.
  let shown: string | null = null;
  const showCue = (again = false) => {
    const cue = storyCue.get();
    const mine = cue && cue.package === packageId ? cue : null;
    if (!mine) {
      if (shown !== null) post({ type: "story.clear" });
      shown = null;
      return;
    }
    if (shown === mine.beat && !again) return;
    shown = mine.beat;
    post({ type: "story.cue", beat: mine.beat, targets: mine.targets, actions: mine.actions });
  };
  const unsubscribe = storyCue.subscribe(() => showCue());
  const showState = () => {
    const state = storyState.get();
    if (!state || state.package !== packageId) return;
    post({ type: "story.state", state: state.state, slide: state.slide, question: state.question ?? null });
  };
  const unsubscribeState = storyState.subscribe(showState);

  // The room the frame has: told once it has loaded, and on every change.
  let told = "";
  const showLayout = () => {
    const { width, height } = frame.getBoundingClientRect();
    if (width === 0 && height === 0) return;           // kept, hidden: nothing to tell
    const fields = { mode: tookOver(packageId) ? "takeover" : "normal",
                     width: Math.round(width), height: Math.round(height) };
    const now = JSON.stringify(fields);
    if (now === told) return;
    told = now;
    post({ type: "layout", ...fields });
  };
  const resized = new ResizeObserver(showLayout);
  resized.observe(frame);
  const unsubscribeLayout = layout.subscribe(showLayout);
  const loaded = () => { told = ""; showLayout(); };
  frame.addEventListener("load", loaded);

  const onMessage = (event: MessageEvent) => {
    if (event.source !== frame.contentWindow) return;
    const message = event.data;
    if (!message || typeof message !== "object" || message.jarvis !== BRIDGE) return;
    if (message.type === "save") void save(message);
    else if (message.type === "listen") listen(Boolean(message.on));
    else if (message.type === "story.ready") { showCue(true); showState(); }
    else if (message.type === "layout.request") events.onLayoutRequest();
    else if (message.type === "layout.restore" && tookOver(packageId)) byHand("layout.restore", {});
    else if (message.type === "story.missing" && Array.isArray(message.targets)) {
      events.onStoryMissing({ words: () => t("main.package.story.notFound"),
                              detail: message.targets.map(String).join(", ") });
    }
  };

  window.addEventListener("message", onMessage);
  return () => {
    window.removeEventListener("message", onMessage);
    unsubscribe();
    unsubscribeState();
    unsubscribeLayout();
    resized.disconnect();
    frame.removeEventListener("load", loaded);
    stopListening?.();
  };
}
