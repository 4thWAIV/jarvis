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
 * again), `story.missing` {beat, targets} for selectors it could not find,
 * and `story.ambiguous` {beat, targets} for precise targets (a declaration's
 * or a member's) that matched several elements, none of which it rang.
 * JARVIS also posts `story.state` {state, slide, question?} whenever the
 * story's state changes (telling, waiting for his answer, paused, finished,
 * stopped), so a deck can show which question is waiting for him.
 *
 * And focus (PackageFocus.tsx): the frame's helper reports where the part
 * being focused is, `story.focus` {key, rect}, in the frame's own
 * coordinates ({left, top, width, height}, or null when none of it is
 * visible): for each beat (key "beat:<beat>") as it is shown and whenever
 * it moves, and for the part a question is about, which JARVIS asks for
 * with `focus.show` {key, targets} (brought into view, never ringed there)
 * until `focus.clear`. The page never reads the frame's DOM: the frame has
 * no origin, so where a part is comes only from the frame itself.
 *
 * And where it is (a deck, examples/packages/decision-deck): the frame says
 * `slide.shown` {opener} whenever it shows a slide, however it got there (a
 * key, the story, a question JARVIS waits on), naming the control that opens
 * it ("#go-3"); the border counts itself on the story slide whose openers
 * include it. A frame that has said so is one the border moves: JARVIS
 * posts `slide.open` {opener} (the story helper clicks it) for Previous,
 * Next and Chapters, so the border is the deck's one navigation.
 *
 * And where he is (Ask and Return, story_teller.py): the frame's helper
 * says `place` {scroll, part, words, selection, selected, focused} whenever
 * what it shows changes (a scroll, his click, his selection, keyboard
 * focus): each scrolled part ({at: selector, "" for the page, x, y}), the
 * part he selected (a selector, its words, his selection in them as
 * character offsets, the words selected) and what has keyboard focus. With
 * the deck slide it last said it shows, that is the view an Ask keeps
 * (packages.ts `FrameView`). Going back (the server's `package_return`),
 * JARVIS posts `place.restore` {view, revealed}: the helper brings up what
 * the story had revealed (`revealed`: shows and clicks, in order), then the
 * slide shown, then the scrolling, his selection and keyboard focus, and
 * says `place.restored`; a frame that loads later (its page was closed) is
 * posted it once its helper is ready.
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
import { returnTo, saveResult, storyCue, storyState, type FrameView } from "./packages";
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
  /** Where a focused part is in the frame's coordinates, or null. */
  onFocus?(key: string, rect: FrameRect | null): void;
  onPresentationReady?(): void;
  onPresentationReturnFocus?(): void;
  /** The frame shows the slide `opener` opens. */
  onSlideShown?(opener: string): void;
  /** What the frame shows now, with the deck slide it last said it shows. */
  onPlace?(view: FrameView): void;
}

/** A box in the frame's own coordinates, as its helper reports it. */
export interface FrameRect { left: number; top: number; width: number; height: number }

/** What the view holds of a connected frame. */
export interface Bridge {
  /** Stop hearing the frame. */
  close(): void;
  /** Ask the frame to bring the part `targets` name into view and report
   *  it under `key`; null asks it to stop. */
  focus(key: string | null, targets?: string[]): void;
  /** Ask the frame to open the slide `opener` opens. */
  openSlide(opener: string): void;
}

const asRect = (value: unknown): FrameRect | null => {
  if (!value || typeof value !== "object") return null;
  const r = value as Record<string, unknown>;
  const n = (v: unknown) => typeof v === "number" && Number.isFinite(v);
  return n(r.left) && n(r.top) && n(r.width) && n(r.height)
    ? { left: Number(r.left), top: Number(r.top), width: Number(r.width), height: Number(r.height) } : null;
};

const text = (v: unknown, most: number) => typeof v === "string" ? v.slice(0, most) : "";
const selector = (v: unknown) => typeof v === "string" && v && v.length <= 400 ? v : null;

/** The frame's `place`, as a view: what it reported, bounded, with the deck
 *  slide `page`; the server checks it again (package_place.view_from). */
function viewOf(message: Record<string, unknown>, page: string | null): FrameView {
  const scroll = (Array.isArray(message.scroll) ? message.scroll : []).slice(0, 8).flatMap((s) => {
    const r = s as Record<string, unknown>;
    return typeof r?.x === "number" && typeof r?.y === "number" && Number.isFinite(r.x) && Number.isFinite(r.y)
      ? [{ at: selector(r.at) ?? "", x: Math.round(r.x), y: Math.round(r.y) }] : [];
  });
  const sel = message.selection;
  const selection = Array.isArray(sel) && sel.length === 2 && sel.every((n) => Number.isInteger(n) && n >= 0)
    && sel[0] <= sel[1] ? [sel[0], sel[1]] as [number, number] : null;
  return { page, scroll, part: selector(message.part), words: text(message.words, 600), selection,
           selected: text(message.selected, 600), focused: selector(message.focused) };
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

/** Hear `frame`'s requests for package `packageId`. */
export function connectBridge(frame: HTMLIFrameElement, packageId: string,
                              events: BridgeEvents): Bridge {
  // The focus asked for, posted again once the frame (re)loads its helper.
  let asked: { key: string; targets: string[] } | null = null;
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

  // Where he is: the frame's last `place`, and the deck slide it shows.
  let page: string | null = null;
  let place: Record<string, unknown> = {};
  const showPlace = () => events.onPlace?.(viewOf(place, page));
  // A place being gone back to: brought up once the helper can hear it.
  let restoring = 0;
  const restore = () => {
    const back = returnTo.get();
    if (!back || back.package !== packageId || back.seq === restoring) return;
    restoring = back.seq;
    // Keyboard focus goes back into the page when it had it there.
    if (back.view.focused) frame.focus();
    post({ type: "place.restore", view: back.view, revealed: back.revealed });
  };
  const unsubscribeReturn = returnTo.subscribe(restore);

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
  const loaded = () => { told = ""; showLayout(); post({ type: "presentation.probe" }); };
  frame.addEventListener("load", loaded);

  const onMessage = (event: MessageEvent) => {
    if (event.source !== frame.contentWindow) return;
    const message = event.data;
    if (!message || typeof message !== "object" || message.jarvis !== BRIDGE) return;
    if (message.type === "presentation.inspection.closed") events.onPresentationReturnFocus?.();
    if (message.type === "presentation.ready" && message.version === "presentation/1") events.onPresentationReady?.();
    else if (message.type === "save") void save(message);
    else if (message.type === "listen") listen(Boolean(message.on));
    else if (message.type === "story.ready") { showCue(true); showState(); askFocus(); restoring = 0; restore(); }
    else if (message.type === "place") { place = message; showPlace(); }
    else if (message.type === "place.restored") {
      if (returnTo.get()?.package === packageId) returnTo.set(null);
    }
    else if (message.type === "layout.request") events.onLayoutRequest();
    else if (message.type === "slide.shown" && typeof message.opener === "string") {
      page = message.opener;
      events.onSlideShown?.(message.opener);
      showPlace();
    }
    else if (message.type === "story.focus" && typeof message.key === "string") {
      events.onFocus?.(message.key, asRect(message.rect));
    }
    else if (message.type === "layout.restore" && tookOver(packageId)) byHand("layout.restore", {});
    else if (message.type === "story.missing" && Array.isArray(message.targets)) {
      events.onStoryMissing({ words: () => t("main.package.story.notFound"),
                              detail: message.targets.map(String).join(", ") });
    }
    else if (message.type === "story.ambiguous" && Array.isArray(message.targets)) {
      events.onStoryMissing({ words: () => t("main.package.story.ambiguous"),
                              detail: message.targets.map(String).join(", ") });
    }
  };

  const askFocus = () => {
    if (asked) post({ type: "focus.show", key: asked.key, targets: asked.targets });
  };

  window.addEventListener("message", onMessage);
  const close = () => {
    window.removeEventListener("message", onMessage);
    unsubscribe();
    unsubscribeState();
    unsubscribeReturn();
    unsubscribeLayout();
    resized.disconnect();
    frame.removeEventListener("load", loaded);
    stopListening?.();
  };
  return {
    close,
    openSlide(opener) { post({ type: "slide.open", opener }); },
    focus(key, targets = []) {
      if (key === null) {
        if (asked) post({ type: "focus.clear" });
        asked = null;
        return;
      }
      asked = { key, targets };
      askFocus();
    },
  };
}
