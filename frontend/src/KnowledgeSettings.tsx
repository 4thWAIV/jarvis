/** Knowledge controls use the same API and stores as the Knowledge view. */
import { useState, useSyncExternalStore } from "react";
import { knowledgeStatus, setKnowledgeEnabled } from "./knowledge";
import { Vectors } from "./KnowledgeView";
import { useT } from "./locale";
import { SettingsRow, useSettingsItem } from "./SettingsRow";
import { bind, bindingProps } from "./uiNavigation";

export function KnowledgeSettings() {
  const t = useT();
  const status = useSyncExternalStore(knowledgeStatus.subscribe, knowledgeStatus.get);
  const toggle = useSettingsItem("knowledgeEnabled", t("settings.knowledge.enable"));
  const vectors = useSettingsItem("knowledgeModel", t("main.knowledge.vectorsTitle"));
  const folders = useSettingsItem("knowledgeFolders", t("settings.knowledge.folders"));
  const details = useSettingsItem("knowledgeDetails", t("settings.knowledge.details"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const change = async (enabled: boolean) => {
    setBusy(true); setError(null);
    try { await setKnowledgeEnabled(enabled); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <>
    <p className="settings-hint">{t("settings.knowledge.help")}</p>
    <SettingsRow label={t("settings.knowledge.enable")} labelRef={toggle.part("label")} htmlFor="knowledge-enabled">
      <input id="knowledge-enabled" type="checkbox" data-ui-action="domain" ref={toggle.whole}
        checked={status.value?.enabled ?? false} disabled={busy || !status.value}
        onChange={e => change(e.target.checked)} />
    </SettingsRow>
    <p role="status">{status.value ? t("settings.knowledge.engine", { state: status.value.state }) : t("main.knowledge.reading")}</p>
    {(error || status.error) && <p role="alert" className="read-failure">{error || status.error}</p>}
    <Vectors partRef={vectors.whole} />
    <button className="settings-btn" ref={folders.whole} {...bindingProps(bind("folders.list", { view: "registered" }))}>
      {t("settings.knowledge.folders")}
    </button>
    <button className="settings-btn" ref={details.whole} {...bindingProps(bind("knowledge.open", {}))}>
      {t("settings.knowledge.details")}
    </button>
  </>;
}
