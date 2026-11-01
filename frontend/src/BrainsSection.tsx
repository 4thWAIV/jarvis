/**
 * The Settings view's Brains section: a card for each brain JARVIS has, and
 * a way to add the others.
 *
 * A card shows the brain's logo and plan, its model (from the brain's own
 * list), its setup checks as dots with what to do about one that is not
 * green, its account, a sign-in button for a brain that signs in from here
 * (Codex: the page opens ChatGPT's sign-in in a new tab, and the card turns
 * when Codex reports it finished), and "Start with this brain", one choice
 * across the cards, which takes effect at the next start. The brain running
 * now is marked. Switching the running brain is not here yet: it needs a
 * handover, because a new brain is a new conversation.
 */

import { useState } from "react";
import { addBrain, brainsChanged, brainsMayHaveChanged, listBrains, removeBrain, setDefault,
         setModel, startSignIn, type BrainCard, type BrainCheck } from "./brains";
import { useLiveRead } from "./liveRead";
import { useT, type Translate } from "./locale";
import { PROVIDERS } from "./providers";
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
          {others.map((card) => (
            <button data-ui-action="domain" key={card.name} className="settings-btn"
                    onClick={() => act(addBrain(card.name))}>
              {t("left.brains.add", { brain: card.title })}
            </button>
          ))}
        </div>
      )}
    </div>
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
    <div className={`brain-card${card.active ? " active" : ""}`} data-brain={card.name}>
      {card.active && <span className="brain-card-state">{t("left.brains.inUse")}</span>}
      <div className="brain-card-head">
        {logo && <img className="brain-card-logo" src={logo} alt="" />}
        <span className="brain-card-title">{card.title}</span>
        {card.account?.plan && <span className="brain-card-plan">{t("left.brains.plan", { plan: card.account.plan })}</span>}
      </div>

      <div className="settings-field">
        <label>{t("left.brains.model")}</label>
        <select data-ui-action="domain" value={card.model} onChange={(e) => act(setModel(card.name, e.target.value))}>
          {(card.models ?? [card.model]).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>

      <div className="brain-checks">
        {/* Signing in is one of each brain's own checks, so it is said once, there. */}
        {(card.checks ?? []).map((check) => <CheckRow key={check.name} check={check} />)}
      </div>

      {card.signs_in_here && !signedIn && (
        <button data-ui-action="sign-in" className="settings-btn primary" onClick={signIn} disabled={signing}>
          {t(signing ? "left.brains.signingIn" : "left.brains.signInChatGpt")}
        </button>
      )}

      <label className="brain-default">
        <input data-ui-action="domain" type="radio" name="brain-default" checked={card.default}
               onChange={() => act(setDefault(card.name))} />
        {t("left.brains.startWith")}
      </label>

      {!card.default && card.name !== "anthropic" && (
        <button data-ui-action="domain" className="settings-btn brain-remove" onClick={() => act(removeBrain(card.name))}>
          {t("left.brains.remove")}
        </button>
      )}
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
      {check.status !== "ok" && check.remedy && <p className="settings-hint" data-original-detail="">{check.remedy}</p>}
    </div>
  );
}
