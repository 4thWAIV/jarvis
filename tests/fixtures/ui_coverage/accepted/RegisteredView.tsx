// Accepted fixture for tests/ui_coverage_rules.mts: a view written the way
// the rules ask. Its navigation runs registered commands, its other
// controls are classified, it writes only its own route state, and its
// target and parts are declared in the contract, keyed by the record's own
// identity, and its words come from the catalog. Never imported by the
// page.
import { useSyncExternalStore } from "react";
import { useT } from "../../../../frontend/src/locale";
import { createStore } from "../../../../frontend/src/store";
import { bind, bindingProps, type ReadStore } from "../../../../frontend/src/uiNavigation";
import { byHand, useUiTarget } from "../../../../frontend/src/uiRegistry";

/**
 * @routeState Whether the example's own panel is open.
 * @routeStateAlso coOwner.ts
 */
const open = createStore(false);
export const panelOpen: ReadStore<boolean> = open;

export function closePanel(): void {
  open.set(false);              // its owner may write it
}

export function RegisteredView({ provider }: { provider: string }) {
  const t = useT();
  const shown = useSyncExternalStore(open.subscribe, open.get);
  const ui = useUiTarget({ ref: { region: "left", view: "left.fixed", kind: "brainAccount", key: provider },
                           label: provider, socket: { rail: "rail.left", side: "left" } });
  const toggle = bindingProps(bind("menu.toggle", { open: !shown }));
  return (
    <section ref={ui.whole}>
      <span ref={ui.part("remaining")}>42</span>
      <button {...bindingProps(bind("settings.open", { section: "brains" }))}>{t("menu.settings")}</button>
      <button {...toggle} onClick={(e) => { e.stopPropagation(); toggle.onClick(); }}>{t("menu.button")}</button>
      <button onClick={() => byHand("network.open", { kind: "server" })}>{t("tabs.main.teamServer")}</button>
      {shown && <input data-ui-action="input" onChange={() => undefined} />}
      <button data-ui-action="microphone" onClick={() => undefined} title={`JARVIS · ${provider}`}>⏻</button>
    </section>
  );
}
