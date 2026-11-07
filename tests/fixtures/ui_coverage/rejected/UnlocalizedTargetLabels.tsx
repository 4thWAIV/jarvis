// Negative fixture for tests/ui_coverage_rules.mts: target labels, which the
// brain's map reads, written in English or read from the catalog in one
// fixed language instead of the language in force. Each marked line must be
// rejected; the last shows a label as the rule asks. Never imported by the page.
import { translate, useT } from "../../../../frontend/src/locale";
import { useUiTarget } from "../../../../frontend/src/uiRegistry";

const SOCKET = { rail: "rail.left", side: "left" } as const;

export function UnlocalizedTargetLabels() {
  const t = useT();
  const orb = useUiTarget({ ref: { region: "left", view: "left.fixed", kind: "orb" }, label: "The orb", socket: SOCKET });   // rejected: English
  const status = useUiTarget({ ref: { region: "main", view: "main.conversation", kind: "status" },
                               label: translate("en", "targets.status"), socket: SOCKET });                                // rejected: one fixed language
  const mic = useUiTarget({ ref: { region: "main", view: "main.conversation", kind: "mic" }, label: t("targets.microphone"), socket: SOCKET });
  return <div ref={(el) => { orb.whole(el); status.whole(el); mic.whole(el); }} />;
}
