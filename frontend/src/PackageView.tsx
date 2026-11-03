/**
 * A page work package in the centre area: its entry HTML in a frame, opened
 * by the page's Open page or the brain's `open_package_page` (packages.ts).
 *
 * The frame is `sandbox="allow-scripts"` and never `allow-same-origin`:
 * its files are served from JARVIS's own origin, so with the same origin
 * the package's scripts could drive every one of JARVIS's routes. Without
 * it the frame has no origin, and its server headers sandbox it the same
 * way if it is opened on its own (web_auth.PACKAGE_PAGE_HEADERS). What it
 * may do (save a result, hear the microphone, be pointed at while its
 * story is told) goes through the bridge (packageBridge.ts).
 *
 * A package with a story has Tell the story, a slide to start from, and
 * Stop: the same `_package_story` the brain's `tell_package_story` calls.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { connectBridge } from "./packageBridge";
import { entryUrl, openPackage, storyState, tellStory, type PackageInfo } from "./packages";

export function PackageView({ info }: { info: PackageInfo }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [said, setSaid] = useState("");
  const [listening, setListening] = useState(false);
  useEffect(() => {
    if (!frame.current) return undefined;
    return connectBridge(frame.current, info.id, {
      onSaved: setSaid, onListening: setListening, onStoryMissing: setSaid,
    });
  }, [info.id]);
  return (
    <div id="package-view" data-package={info.id}>
      <section className="package-head">
        <div className="package-head-words">
          <h2>{info.name}</h2>
          <p>{info.purpose}</p>
        </div>
        <button type="button" className="workflow-close" title="Close" aria-label="Close"
                onClick={() => openPackage.set(null)}>×</button>
      </section>
      {info.story && <StoryControls info={info} onSaid={setSaid} />}
      <iframe ref={frame} className="package-frame" title={info.name} src={entryUrl(info)}
              sandbox="allow-scripts" />
      <p className="package-status" role="status">
        {listening ? "Listening through JARVIS's microphone. " : ""}{said}
      </p>
    </div>
  );
}

function StoryControls({ info, onSaid }: { info: PackageInfo; onSaid: (s: string) => void }) {
  const state = useSyncExternalStore(storyState.subscribe, storyState.get);
  const [slide, setSlide] = useState(1);
  const [busy, setBusy] = useState(false);
  const telling = state?.package === info.id && state.state === "telling";
  const slides = info.story?.slides ?? [];
  const act = async (action: "tell" | "stop") => {
    setBusy(true);
    const problem = await tellStory(info.id, action, action === "tell" ? slide : undefined);
    setBusy(false);
    onSaid(problem ?? "");
  };
  return (
    <section className="package-story" aria-label="The story">
      <button type="button" className="folder-button" disabled={busy}
              onClick={() => void act("tell")}>
        {telling ? "Tell from this slide" : "Tell the story"}
      </button>
      <label>
        From slide{" "}
        <select value={slide} onChange={(e) => setSlide(Number(e.target.value))}>
          {slides.map((title, n) => <option key={n} value={n + 1}>{n + 1}. {title}</option>)}
        </select>
      </label>
      <button type="button" className="folder-button" disabled={busy || !telling}
              onClick={() => void act("stop")}>Stop</button>
      <span className="package-story-now" role="status">
        {telling ? `Telling slide ${state.slide} of ${state.slides}.`
          : state?.package === info.id && state.state === "finished" ? "The story is told."
            : state?.package === info.id && state.state === "stopped" ? "Stopped." : ""}
      </span>
    </section>
  );
}
