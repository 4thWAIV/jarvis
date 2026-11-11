/**
 * The records move, shown in place of the workspace while it runs
 * (records_move.py): the first start after the update that moved JARVIS's
 * records copies jarvis.db into the knowledge engine, and the server
 * answers meanwhile. `RecordsMoveGate` reads `GET /api/records/move` before
 * the workspace is drawn: while the records move (or the move failed) it
 * shows where the move is, the step, the time left and that stopping is
 * safe, or why it failed, and reads again every two seconds; once they are
 * moved it draws the workspace. A server with no move (or none to tell)
 * draws the workspace at once.
 */

import { useEffect, useState, type ReactNode } from "react";
import { guardedRead, useT, type PlainKey } from "./locale";
import { spokenAge } from "./timeFormat";

export interface RecordsMove {
  readonly state: "moving" | "starting" | "ready" | "failed";
  readonly step: string | null;
  readonly steps: readonly string[];
  readonly done_steps: readonly string[];
  readonly left: number | null;
  readonly elapsed: number | null;
  readonly reason: string | null;
}

const READ_EVERY_MS = 2000;

/** The move as the server says, or null when there is none to show (no
 *  move route, or the server not answering yet: the workspace says so). */
export async function readRecordsMove(): Promise<RecordsMove | null> {
  try {
    const res = await fetch("/api/records/move");
    if (!res.ok) return null;
    return (await res.json()) as RecordsMove;
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

export function RecordsMoveGate({ children }: { readonly children: ReactNode }) {
  const [move, setMove] = useState<RecordsMove | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const read = async () => {
      const now = await readRecordsMove();
      if (!live) return;
      setMove(now);
      if (now && now.state !== "ready") timer = window.setTimeout(read, READ_EVERY_MS);
    };
    void readLanguage().then(read);
    return () => { live = false; window.clearTimeout(timer); };
  }, []);
  if (move === undefined) return null;
  if (move === null || move.state === "ready") return <>{children}</>;
  return <RecordsMoveView move={move} />;
}

export function RecordsMoveView({ move }: { readonly move: RecordsMove }) {
  const t = useT();
  if (move.state === "failed") {
    return (
      <main className="records-move failed" id="records-move" role="alert">
        <h1>{t("move.failed.title")}</h1>
        <p className="records-move-reason">{move.reason}</p>
        <p>{t("move.failed.kept")}</p>
        <p>{t("move.failed.back")}</p>
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
