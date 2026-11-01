// Negative fixture for tests/ui_coverage_rules.mts: controls that change
// what is shown without a registered command, and none classified. Every
// element here must be rejected. Never imported by the page.
import { useState } from "react";
import { menuOpen } from "../../../../frontend/src/MenuControl";

export function UnregisteredControls({ rows }: { rows: string[] }) {
  const [tab, setTab] = useState("a");
  const [open, setOpen] = useState(false);
  return (
    <div>
      {/* A tab of its own, outside the registry: control-unclassified. */}
      <button onClick={() => setTab("b")}>{tab}</button>
      {/* A keyboard handler counts as much as a click. */}
      <div role="tab" tabIndex={0} onKeyDown={(e) => { if (e.key === "Enter") setTab("c"); }}>C</div>
      {/* A control drawn only sometimes is still a control. */}
      {open && <button onClick={() => setOpen(false)}>×</button>}
      {/* A classification that was never reviewed. */}
      <button data-ui-action="navigation-ish" onClick={() => setTab("d")}>D</button>
      {/* Reading route state is fine; it is not a write. */}
      <span>{String(menuOpen.get())}</span>
      {rows.map((r) => <span key={r}>{r}</span>)}
    </div>
  );
}
