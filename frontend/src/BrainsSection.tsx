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
import { PROVIDERS } from "./providers";
import { StatusDot } from "./StatusDot";

// How each setup check is named on its card.
const CHECK_LABEL: Record<string, string> = {
  claude_cli: "Claude Code", claude_login: "Signed in", anthropic_key_leftover: "No API key",
  codex_cli: "Codex", codex_login: "Signed in",
};

const DOT = { ok: "green", warn: "yellow", fail: "red" } as const;

export function BrainsSection() {
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
      {live.error ? `The brains could not be read (${live.error}); trying again.` : "Reading the brains…"}
    </p>;
  }
  const added = cards.filter((c) => c.added);
  const others = cards.filter((c) => !c.added);
  return (
    <div className="brains">
      {live.error && <p className="settings-hint read-failure">
        The brains could not be read again ({live.error}); what is shown may be out of date.</p>}
      {problem && <p className="settings-hint">{problem}</p>}
      <div className="brain-cards">
        {added.map((card) => (
          <BrainCardView key={card.name} card={card} act={act} onProblem={setProblem} />
        ))}
      </div>
      {others.length > 0 && (
        <div className="settings-actions brain-add">
          {others.map((card) => (
            <button key={card.name} className="settings-btn"
                    onClick={() => act(addBrain(card.name))}>
              + Add {card.title}
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
      {card.active && <span className="brain-card-state">in use</span>}
      <div className="brain-card-head">
        {logo && <img className="brain-card-logo" src={logo} alt="" />}
        <span className="brain-card-title">{card.title}</span>
        {card.account?.plan && <span className="brain-card-plan">{card.account.plan} plan</span>}
      </div>

      <div className="settings-field">
        <label>Model</label>
        <select value={card.model} onChange={(e) => act(setModel(card.name, e.target.value))}>
          {(card.models ?? [card.model]).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </div>

      <div className="brain-checks">
        {/* Signing in is one of each brain's own checks, so it is said once, there. */}
        {(card.checks ?? []).map((check) => <CheckRow key={check.name} check={check} />)}
      </div>

      {card.signs_in_here && !signedIn && (
        <button className="settings-btn primary" onClick={signIn} disabled={signing}>
          {signing ? "Finish signing in in the new tab…" : "Sign in with ChatGPT"}
        </button>
      )}

      <label className="brain-default">
        <input type="radio" name="brain-default" checked={card.default}
               onChange={() => act(setDefault(card.name))} />
        Start with this brain
      </label>

      {!card.default && card.name !== "anthropic" && (
        <button className="settings-btn brain-remove" onClick={() => act(removeBrain(card.name))}>
          Remove
        </button>
      )}
    </div>
  );
}

function CheckRow({ check }: { check: BrainCheck }) {
  return (
    <div className="check-row" title={check.remedy ?? ""}>
      <StatusDot status={DOT[check.status]} />
      <span>{CHECK_LABEL[check.name] ?? check.name}</span>
      <span className="status-detail">{check.message}</span>
      {check.status !== "ok" && check.remedy && <p className="settings-hint">{check.remedy}</p>}
    </div>
  );
}
