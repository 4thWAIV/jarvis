/**
 * The Settings view's Brains section: a card for each brain JARVIS has, and
 * a way to add the others.
 *
 * A card shows the brain's logo and plan, its model (from the brain's own
 * list), any setup check that is not passing, with what to do about it
 * (passing checks are the ordinary state and are not listed), a sign-in button for a brain that signs in from here
 * (Codex: the page opens ChatGPT's sign-in in a new tab, and the card turns
 * when Codex reports it finished), and "Start with this brain", one choice
 * across the cards, which takes effect at the next start. The brain running
 * now is marked. Switching the running brain is not here yet: it needs a
 * handover, because a new brain is a new conversation.
 *
 * Each card is a target by its brain, with its model, checks, sign-in,
 * start choice and Remove as parts; each Add button is one too
 * (SettingsRow.tsx).
 *
 * Below the cards, which engine each kind of agent JARVIS starts goes on
 * (engine_choice.py, /api/settings/engines; the brain's engine_settings and
 * set_engine_setting are the same): a brain and one of its models, the same
 * as the agent asking, or the least in token stress, and whether a brain in
 * token stress gives way. Token stress is each brain's plus or minus against
 * its pace this week, shown beside it.
 */

import { useState } from "react";
import { addBrain, brainsChanged, brainsMayHaveChanged, changeEngines, ENGINE_PROVIDER, listBrains,
         readEngines, removeBrain, setDefault, setModel, startSignIn, type BrainCard, type BrainCheck,
         type Engine, type EngineKind, type EngineSetting, type EngineSettings } from "./brains";
import { useLiveRead } from "./liveRead";
import { useT, type Translate } from "./locale";
import { PROVIDERS } from "./providers";
import { SettingsRow, useSettingsItem, useSettingsRow, type SettingsItem } from "./SettingsRow";
import { StatusDot } from "./StatusDot";

// How each setup check is named on its card, by the check's name; a
// product's own name is kept as it is. A check's message and remedy are
// preflight's own words, shown as they came.
const CHECK_LABEL: Record<string, (t: Translate) => string> = {
  claude_cli: () => "Claude Code", claude_login: (t) => t("left.brains.check.signedIn"),
  anthropic_key_leftover: (t) => t("left.brains.check.noApiKey"),
  codex_cli: () => "Codex", codex_login: (t) => t("left.brains.check.signedIn"),
};

const DOT = { ok: "green", warn: "yellow", fail: "red" } as const;

export function BrainsSection() {
  const t = useT();
  // Read when Settings opens, on every reconnect and whenever the brains
  // change, and retried until read.
  const live = useLiveRead(() => listBrains(), [brainsChanged]);
  const cards = live.value;
  const [problem, setProblem] = useState<string | null>(null);

  // A change here is a change everywhere: the orb and Diagnostics read the
  // brains again too.
  const act = (work: Promise<unknown>) =>
    work.then(() => { setProblem(null); brainsMayHaveChanged(); })
      .catch((e: Error) => setProblem(e.message));

  if (!cards) {
    return <p className="settings-hint read-failure">
      {live.error ? t("left.brains.unreadable", { error: live.error }) : t("left.brains.reading")}
    </p>;
  }
  const added = cards.filter((c) => c.added);
  const others = cards.filter((c) => !c.added);
  return (
    <div className="brains">
      {live.error && <p className="settings-hint read-failure">
        {t("left.brains.staleRead", { error: live.error })}</p>}
      {problem && <p className="settings-hint">{problem}</p>}
      <div className="brain-cards">
        {added.map((card) => (
          <BrainCardView key={card.name} card={card} act={act} onProblem={setProblem} />
        ))}
      </div>
      {others.length > 0 && (
        <div className="settings-actions brain-add">
          {others.map((card) => <AddBrain key={card.name} card={card} act={act} />)}
        </div>
      )}
      <EngineChoices cards={added} />
    </div>
  );
}

const KIND_ITEM: Record<EngineKind, SettingsItem> = {
  package_author: "enginePackageAuthor", run: "engineRun", build: "engineBuild",
  agent_package: "engineAgentPackage",
};

/** A setting as one select's value, and back. */
function settingValue(setting: EngineSetting): string {
  return setting.pick === "fixed" ? `fixed:${setting.engine}:${setting.model ?? ""}` : setting.pick;
}

function settingOf(value: string): EngineSetting {
  if (!value.startsWith("fixed:")) return { pick: value as EngineSetting["pick"] };
  const [, engine, model] = value.split(":");
  return { pick: "fixed", engine: engine as Engine, model: model || null };
}

function EngineChoices({ cards }: { cards: BrainCard[] }) {
  const t = useT();
  const live = useLiveRead(() => readEngines(), [brainsChanged]);
  const [shown, setShown] = useState<EngineSettings | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const read = shown ?? live.value;
  // Until they are read the same rows are drawn, idle, so nothing below
  // moves when they arrive (a section scrolled to stays in view).
  const settings = read ?? waiting(cards);
  const overrideUi = useSettingsItem("engineOverride", t("left.brains.engines.override"));
  const title = (engine: Engine) =>
    cards.find((c) => c.name === ENGINE_PROVIDER[engine])?.title ?? engine;
  const change = (work: Promise<EngineSettings>) =>
    work.then((next) => { setShown(next); setProblem(null); })
      .catch((e: Error) => setProblem(e.message));
  return (
    <div className="brain-engines">
      <h4>{t("left.brains.engines.heading")}</h4>
      {problem && <p className="settings-hint read-failure">{problem}</p>}
      {settings.kinds_order.map((kind) => (
        <EngineKindRow key={kind} kind={kind} setting={settings.kinds[kind]} cards={cards}
                       title={title} disabled={!read}
                       onChange={(next) => change(changeEngines({ kind, ...next }))} />
      ))}
      <SettingsRow label={t("left.brains.engines.override")} labelRef={overrideUi.part("label")}
                   htmlFor="engine-override">
        <input id="engine-override" type="checkbox" data-ui-action="domain" ref={overrideUi.whole}
               disabled={!read}
               checked={settings.override_stressed}
               onChange={(e) => change(changeEngines({ override_stressed: e.target.checked }))} />
      </SettingsRow>
      <p className="settings-hint">
        {settings.engines.map((e) => e.ahead === null
          ? t("left.brains.engines.notMeasured", { brain: title(e.engine) })
          : t(e.stressed ? "left.brains.engines.stressed" : "left.brains.engines.pace",
              { brain: title(e.engine), ahead: (e.ahead > 0 ? "+" : "") + e.ahead.toFixed(1) }))
          .join(" ")}
      </p>
    </div>
  );
}

/** The settings' shape before they are read: every kind, every brain the
 *  cards show, nothing measured. */
function waiting(cards: BrainCard[]): EngineSettings {
  const kinds_order: EngineKind[] = ["package_author", "run", "build", "agent_package"];
  return {
    kinds: Object.fromEntries(kinds_order.map((k) => [k, { pick: "requester" }])) as
      Record<EngineKind, EngineSetting>,
    kinds_order, override_stressed: false, least_stressed: null,
    engines: (Object.keys(ENGINE_PROVIDER) as Engine[])
      .filter((engine) => cards.some((c) => c.name === ENGINE_PROVIDER[engine]))
      .map((engine) => ({ engine, provider: ENGINE_PROVIDER[engine], ahead: null, stressed: false })),
  };
}

function EngineKindRow({ kind, setting, cards, title, disabled, onChange }: {
  kind: EngineKind;
  setting: EngineSetting;
  cards: BrainCard[];
  title: (engine: Engine) => string;
  disabled: boolean;
  onChange: (setting: EngineSetting) => void;
}) {
  const t = useT();
  const label = t(`left.brains.engines.kind.${kind}`);
  const ui = useSettingsItem(KIND_ITEM[kind], label);
  const engines = (Object.keys(ENGINE_PROVIDER) as Engine[])
    .filter((engine) => cards.some((c) => c.name === ENGINE_PROVIDER[engine]));
  return (
    <SettingsRow label={label} labelRef={ui.part("label")} htmlFor={`engine-${kind}`}>
      <select id={`engine-${kind}`} data-ui-action="domain" ref={ui.whole} disabled={disabled}
              value={settingValue(setting)} onChange={(e) => onChange(settingOf(e.target.value))}>
        <option value="requester">{t("left.brains.engines.requester")}</option>
        <option value="least_stress">{t("left.brains.engines.leastStress")}</option>
        {engines.map((engine) => {
          const card = cards.find((c) => c.name === ENGINE_PROVIDER[engine]);
          return [
            <option key={`${engine}:`} value={`fixed:${engine}:`}>
              {t("left.brains.engines.defaultModel", { brain: title(engine) })}</option>,
            ...(card?.models ?? []).map((model) => (
              <option key={`${engine}:${model}`} value={`fixed:${engine}:${model}`}>
                {t("left.brains.engines.model", { brain: title(engine), model })}</option>)),
          ];
        })}
      </select>
    </SettingsRow>
  );
}

function AddBrain({ card, act }: { card: BrainCard; act: (work: Promise<unknown>) => void }) {
  const t = useT();
  const words = t("left.brains.add", { brain: card.title });
  const ui = useSettingsRow("brainAdd", card.name, words);
  return (
    <button data-ui-action="domain" className="settings-btn" ref={ui.whole}
            onClick={() => act(addBrain(card.name))}>{words}</button>
  );
}

function BrainCardView({ card, act, onProblem }: {
  card: BrainCard;
  act: (work: Promise<unknown>) => void;
  onProblem: (problem: string) => void;
}) {
  const t = useT();
  const logo = PROVIDERS[card.name]?.logo;
  const [signing, setSigning] = useState(false);
  const signedIn = card.account?.signed_in ?? false;
  const ui = useSettingsRow("brainCard", card.name, card.title);
  const failing = (card.checks ?? []).filter((check) => check.status !== "ok");

  const signIn = async () => {
    setSigning(true);
    try {
      const url = await startSignIn(card.name);
      window.open(url, "_blank", "noopener");
    } catch (e) {
      setSigning(false);
      onProblem((e as Error).message);
    }
  };

  return (
    <div className={`brain-card${card.active ? " active" : ""}`} data-brain={card.name} ref={ui.whole}>
      {card.active && <span className="brain-card-state">{t("left.brains.inUse")}</span>}
      <div className="brain-card-head">
        {logo && <img className="brain-card-logo" src={logo} alt="" />}
        <span className="brain-card-title">{card.title}</span>
        {card.account?.plan && <span className="brain-card-plan">{t("left.brains.plan", { plan: card.account.plan })}</span>}
      </div>

      <SettingsRow label={t("left.brains.model")} htmlFor={`brain-model-${card.name}`}>
        <select data-ui-action="domain" id={`brain-model-${card.name}`} value={card.model} ref={ui.part("model")}
                onChange={(e) => act(setModel(card.name, e.target.value))}>
          {(card.models ?? [card.model]).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </SettingsRow>

      {failing.length > 0 && (
        <div className="brain-checks" ref={ui.part("checks")}>
          {/* Signing in is one of each brain's own checks, so it is said once, there. */}
          {failing.map((check) => <CheckRow key={check.name} check={check} />)}
        </div>
      )}

      {card.signs_in_here && !signedIn && (
        <button data-ui-action="sign-in" className="settings-btn primary" onClick={signIn} disabled={signing}
                ref={ui.part("signIn")}>
          {t(signing ? "left.brains.signingIn" : "left.brains.signInChatGpt")}
        </button>
      )}

      <div className="brain-card-foot">
        <label className="brain-default" ref={ui.part("startWith")}>
          <input data-ui-action="domain" type="radio" name="brain-default" checked={card.default}
                 onChange={() => act(setDefault(card.name))} />
          {t("left.brains.startWith")}
        </label>
        {!card.default && card.name !== "anthropic" && (
          <button data-ui-action="domain" className="settings-btn brain-remove" ref={ui.part("remove")}
                  onClick={() => act(removeBrain(card.name))}>
            {t("left.brains.remove")}
          </button>
        )}
      </div>
    </div>
  );
}

function CheckRow({ check }: { check: BrainCheck }) {
  const t = useT();
  return (
    <div className="check-row" title={check.remedy ?? ""}>
      <StatusDot status={DOT[check.status]} />
      <span>{CHECK_LABEL[check.name]?.(t) ?? check.name}</span>
      <span className="status-detail" data-original-detail="">{check.message}</span>
      {check.remedy && <p className="settings-hint" data-original-detail="">{check.remedy}</p>}
    </div>
  );
}
