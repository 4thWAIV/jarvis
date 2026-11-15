/**
 * The records move, shown over the workspace while it runs
 * (records_move.py): the first start after the update that moved JARVIS's
 * records copies jarvis.db into the knowledge engine, and the server
 * answers meanwhile. `RecordsMoveGate` draws the workspace at once and
 * reads `GET /api/records/move`: while the records move (or the move
 * failed) it covers the whole page with where the move is, the step, the
 * time left and that stopping is safe, or why it failed, and reads again
 * every two seconds; once they are moved the page loads again, so the
 * workspace starts on the records. A server with no move (or any answer
 * that is not a move) leaves the workspace as it is.
 *
 * The same failed view covers a start that could not open the records at
 * all (`cause` "engine": the knowledge engine did not answer) or that failed
 * once they were open ("start"), each with its own words. Every failed view
 * has Try again: MenuControl.postRestart, which the gate lets through from this
 * page while a start has failed (records_move.RETRY); the page then waits
 * for a new server (its `boot` changes) and loads again on it.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { guardedRead, useT, type PlainKey } from "./locale";
import { postRestart } from "./MenuControl";
import { spokenAge } from "./timeFormat";

export interface RecordsMove {
  readonly state: "moving" | "starting" | "ready" | "failed";
  readonly step: string | null;
  readonly steps: readonly string[];
  readonly done_steps: readonly string[];
  readonly left: number | null;
  readonly elapsed: number | null;
  readonly reason: string | null;
  readonly cause?: "move" | "engine" | "start" | null;
  readonly boot?: string;
}

const READ_EVERY_MS = 2000;
// After Try again: how often to look for the new server, and how long
// before the page loads again whatever it finds.
const RETRY_LOOK_MS = 1000;
const RETRY_GIVE_UP_MS = 5 * 60 * 1000;
const SHOWN: readonly string[] = ["moving", "starting", "failed"];

/** The move as the server says, or null when there is none to show (no
 *  move route, or the server not answering yet: the workspace says so). */
export async function readRecordsMove(): Promise<RecordsMove | null> {
  try {
    const res = await fetch("/api/records/move");
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<RecordsMove> | null;
    // Only a move under way, starting after it, or failed is shown; any
    // other answer (ready, or not this route's) draws the workspace.
    return body && SHOWN.includes(body.state as string) && Array.isArray(body.steps)
      ? (body as RecordsMove) : null;
  } catch {
    return null;
  }
}

async function readLanguage(): Promise<void> {
  try {
    const take = guardedRead();
    const res = await fetch("/api/settings/language");
    if (res.ok) take(((await res.json()) as { language?: unknown }).language);
  } catch {
    /* the default language */
  }
}

const STEP_WORDS: Record<string, PlainKey> = {
  space: "move.step.space",
  clear: "move.step.clear",
  copy: "move.step.copy",
  check: "move.step.check",
  check_back: "move.step.checkBack",
};

/** The boot id of the server answering now, or null while none answers
 *  (it is restarting). */
async function readBoot(): Promise<string | null> {
  try {
    const res = await fetch("/api/records/move");
    if (!res.ok) return null;
    const body = (await res.json()) as { boot?: unknown } | null;
    return typeof body?.boot === "string" ? body.boot : null;
  } catch {
    return null;
  }
}

/** Try again: ask the server to start again, then wait for a new one (its
 *  boot id changes) and load the page on it. Resolves with the refusal's
 *  words when the server did not take it; never resolves otherwise. */
export async function tryAgain(boot: string | undefined): Promise<string> {
  const refused = await postRestart();
  if (refused !== null) return refused;
  const until = Date.now() + RETRY_GIVE_UP_MS;
  for (;;) {
    await new Promise((r) => window.setTimeout(r, RETRY_LOOK_MS));
    const now = await readBoot();
    if ((now !== null && now !== boot) || Date.now() > until) {
      window.location.reload();
      return new Promise<string>(() => undefined);
    }
  }
}

export function RecordsMoveGate({ children }: { readonly children: ReactNode }) {
  const [move, setMove] = useState<RecordsMove | null>(null);
  // Set by Try again: the reads below stop, since the server going away to
  // start again is not the records being ready.
  const retrying = useRef(false);
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    let shown = false;
    const read = async () => {
      if (retrying.current) return;
      const now = await readRecordsMove();
      if (!live || retrying.current) return;
      if (!now && shown) {
        window.location.reload();          // moved: the workspace starts on the records
        return;
      }
      if (now && !shown) {
        shown = true;
        await readLanguage();
      }
      setMove(now);
      if (now) timer = window.setTimeout(read, READ_EVERY_MS);
    };
    void read();
    return () => { live = false; window.clearTimeout(timer); };
  }, []);
  return <>{children}{move ? <RecordsMoveView move={move} retrying={retrying} /> : null}</>;
}

export function RecordsMoveView({ move, retrying }: {
  readonly move: RecordsMove; readonly retrying?: { current: boolean };
}) {
  const t = useT();
  const [asked, setAsked] = useState(false);
  const [refused, setRefused] = useState<string | null>(null);
  if (move.state === "failed") {
    const cause = move.cause ?? "move";
    const retry = async () => {
      setAsked(true);
      setRefused(null);
      if (retrying) retrying.current = true;
      const error = await tryAgain(move.boot);
      if (retrying) retrying.current = false;
      setAsked(false);
      setRefused(error);
    };
    return (
      <main className={`records-move failed cause-${cause}`} id="records-move" role="alert">
        <h1>{t(cause === "engine" ? "move.engine.title" : "move.failed.title")}</h1>
        <p className="records-move-reason">{move.reason}</p>
        <p>{t(cause === "engine" ? "move.engine.kept" : cause === "start" ? "move.start.kept"
                                                                          : "move.failed.kept")}</p>
        <p>
          <button type="button" data-ui-action="restart" id="records-move-retry" className="records-move-retry"
                  disabled={asked} onClick={() => void retry()}>
            {t(asked ? "move.retrying" : "move.retry")}
          </button>
        </p>
        {refused && <p className="records-move-refused">{t("move.retryRefused", { error: refused })}</p>}
        {cause === "move" && <p>{t("move.failed.back")}</p>}
      </main>
    );
  }
  const shown = move.steps.filter((s) => s !== "space");
  const at = move.step === "space" ? 0 : Math.max(0, shown.indexOf(move.step ?? ""));
  const step = move.state === "starting" ? t("move.step.starting")
    : t("move.stepOf", { n: at + 1, total: shown.length,
                         step: t(STEP_WORDS[move.step ?? "space"] ?? "move.step.space") });
  const left = move.state === "starting" || move.left === null ? null
    : move.left > 0 ? t("move.left", { time: spokenAge(Math.max(1, move.left) * 1000) })
    : t("move.longer");
  return (
    <main className="records-move" id="records-move" aria-live="polite">
      <h1>{t("move.title")}</h1>
      <p className="records-move-step">{step}</p>
      <ol className="records-move-steps">
        {shown.map((s) => (
          <li key={s} className={move.done_steps.includes(s) ? "done" : s === move.step ? "now" : ""}>
            {t(STEP_WORDS[s] ?? "move.step.space")}
          </li>
        ))}
      </ol>
      {left && <p className="records-move-left">{left}</p>}
      <p className="records-move-safe">{t("move.safe")}</p>
    </main>
  );
}
