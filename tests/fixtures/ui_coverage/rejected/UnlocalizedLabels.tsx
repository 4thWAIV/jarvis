// Negative fixture for tests/ui_coverage_rules.mts: words written into a
// component in English instead of coming from the catalog. Each marked line
// must be rejected; the rest show what is not words to translate. Never
// imported by the page.
import { useT } from "../../../../frontend/src/locale";

export function UnlocalizedLabels({ n, name, busy }: { n: number; name: string; busy: boolean }) {
  const t = useT();
  const tab = { id: "x", title: "Workflows" };                       // rejected: a title property
  return (
    <div className="unlocalized-labels">
      <p>Nothing here yet.</p>
      <button aria-label="Close the view">×</button>
      <input placeholder={busy ? "Saving…" : t("menu.settings")} />
      <span>{`${n} agents working`}</span>
      <span>{name ?? "Unnamed"}</span>
      <span title={tab.title}>{t("tabs.right.agents")} · {name} · JARVIS · Claude Code · 612b878b113047d9</span>
    </div>
  );
}
