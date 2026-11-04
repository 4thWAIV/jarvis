// Negative fixture for tests/ui_coverage_rules.mts: targets the contract
// does not declare, a part it does not declare, a part named at run time,
// and a key made from a row's position. Never imported by the page.
import { useUiTarget } from "../../../../frontend/src/uiRegistry";

export function OrbOnTheRight() {
  // The orb is presented in the left column only.
  const ui = useUiTarget({ ref: { region: "right", view: "right.network", kind: "orb" }, label: "◯",
                           socket: { rail: "rail.right", side: "right" } });
  return <div ref={ui.whole} />;
}

export function Account({ name, index }: { name: string; index: number }) {
  const ui = useUiTarget({ ref: { region: "left", view: "left.fixed", kind: "brainAccount", key: `row-${index}` },
                           label: name, socket: { rail: "rail.left", side: "left" } });
  return (
    <section ref={ui.whole}>
      <span ref={ui.part("balance")}>{name}</span>
      <span ref={ui.part(name)}>{name}</span>
    </section>
  );
}
