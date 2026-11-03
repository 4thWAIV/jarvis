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
 *
 * Registered for pointing at as `packageView` (its head, its frame and
 * Close); Close runs `package.close`, as the brain does.
 */

import { useEffect, useRef, useState } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { SaidText, useT, type Said } from "./locale";
import { connectBridge } from "./packageBridge";
import { entryUrl, type PackageInfo } from "./packages";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";

export function PackageView({ info }: { info: PackageInfo }) {
  const t = useT();
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [listening, setListening] = useState(false);
  const ui = useUiTarget({ ref: { region: "main", view: "main.package", kind: "packageView", key: info.id },
                           label: info.name, socket: CENTRE_SOCKET });
  useEffect(() => {
    if (!frame.current) return undefined;
    return connectBridge(frame.current, info.id, { onSaved: setSaid, onListening: setListening });
  }, [info.id]);
  return (
    <div id="package-view" data-package={info.id} ref={ui.whole}>
      <section className="package-head" ref={ui.part("header")}>
        <div className="package-head-words">
          <h2>{info.name}</h2>
          <p>{info.purpose}</p>
        </div>
        <button type="button" className="workflow-close" title={t("main.common.close")}
                aria-label={t("main.common.close")} ref={ui.part("close")}
                {...bindingProps(bind("package.close", {}))}>×</button>
      </section>
      <iframe ref={(el) => { frame.current = el; ui.part("frame")(el); }} className="package-frame"
              title={info.name} src={entryUrl(info)} sandbox="allow-scripts" />
      <p className="package-status" role="status">
        {listening ? `${t("main.package.listening")} ` : ""}
        {said && <SaidText said={said} className="package-said" />}
      </p>
    </div>
  );
}
