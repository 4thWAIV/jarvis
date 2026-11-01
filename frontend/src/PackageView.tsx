/**
 * A page work package in the centre area: its entry HTML in a frame, opened
 * by the page's Open page or the brain's `open_package_page` (packages.ts).
 *
 * The frame is `sandbox="allow-scripts"` and never `allow-same-origin`:
 * its files are served from JARVIS's own origin, so with the same origin
 * the package's scripts could drive every one of JARVIS's routes. Without
 * it the frame has no origin, and its server headers sandbox it the same
 * way if it is opened on its own (web_auth.PACKAGE_PAGE_HEADERS). What it
 * may do (save a result, hear the microphone) goes through the bridge
 * (packageBridge.ts).
 */

import { useEffect, useRef, useState } from "react";
import { connectBridge } from "./packageBridge";
import { entryUrl, openPackage, type PackageInfo } from "./packages";

export function PackageView({ info }: { info: PackageInfo }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [said, setSaid] = useState("");
  const [listening, setListening] = useState(false);
  useEffect(() => {
    if (!frame.current) return undefined;
    return connectBridge(frame.current, info.id, { onSaved: setSaid, onListening: setListening });
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
      <iframe ref={frame} className="package-frame" title={info.name} src={entryUrl(info)}
              sandbox="allow-scripts" />
      <p className="package-status" role="status">
        {listening ? "Listening through JARVIS's microphone. " : ""}{said}
      </p>
    </div>
  );
}
