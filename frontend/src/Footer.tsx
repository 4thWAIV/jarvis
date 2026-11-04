/**
 * The bottom of the voice page: the JARVIS label, a target the brain can
 * point at from the footer rail. The label is the footer's one object, so
 * the target is drawn by it (the footer box itself is the page's full
 * width, with no edge for a socket).
 */

import "./ui-left.css";
import { useT } from "./locale";
import { useUiTarget } from "./uiRegistry";

export function Footer() {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "footer", view: "footer.fixed", kind: "footer" }, label: t("targets.footer"),
                           socket: { rail: "rail.footer", side: "left" } });
  return (
    <footer id="footer">
      <div id="jarvis-label" ref={(el) => { ui.whole(el); ui.part("label")(el); }}>JARVIS</div>
    </footer>
  );
}
