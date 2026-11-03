/**
 * The one-line bundle states the Queue shows elsewhere: a message's one-off
 * request and its primary file (QueueItemView.tsx), the owner's review
 * progress on a thread (its cues), and before sending, whether the
 * recipient's JARVIS should read the draft's files (MessageComposer.tsx).
 * Each says nothing while there is nothing out of the ordinary to say.
 * Their words are `bundleWords`', in the language in force. A message's
 * request line and a draft's request fields are each the `request` part of
 * the target they sit in (`target`).
 */

import { useEffect, useState, useSyncExternalStore } from "react";
import type { Readiness, ReviewProgress } from "./bundleContract";
import {
  bundlesChanged, bundleWords as W, readReadiness, readReview, setTask,
} from "./bundles";
import { useT } from "./locale";
import { LIMITS, type AttachmentRef, type Draft, type TaskContext } from "./messagingContract";
import type { OutcomeKind } from "./bundleContract";

/** Where a line registers as its target's part. */
type Target = (el: Element | null) => void;

/** What the sender asked for, and which file is the one to review. */
export function TaskLine({ task, attachments, target }: {
  task: TaskContext | null; attachments: AttachmentRef[]; target?: Target;
}) {
  useT();
  if (!task) return null;
  const primary = task.primary && attachments.find((a) => a.attachment_id === task.primary!.attachment_id
                                                        && a.sha256 === task.primary!.sha256);
  return (
    <p className="message-task" ref={target}>
      <span className="bundle-badge">{W.outcome[task.outcome_kind]}</span>{" "}
      <span className="message-task-words">{task.requested_outcome}</span>
      {primary && (
        <span className="message-task-primary" aria-label={W.primary}>
          {" · "}{task.primary!.member_path || primary.filename}
        </span>
      )}
    </p>
  );
}

/** A read that is asked again on every "bundles" hint and says nothing
 *  when it cannot be read. */
function useHinted<T>(read: () => Promise<T>, key: string): T | null {
  const hint = useSyncExternalStore(bundlesChanged.subscribe, bundlesChanged.get);
  const [value, setValue] = useState<T | null>(null);
  useEffect(() => {
    let live = true;
    read().then((v) => { if (live) setValue(v); }, () => { if (live) setValue(null); });
    return () => { live = false; };
  }, [key, hint]);  // eslint-disable-line react-hooks/exhaustive-deps -- `read` is keyed by `key`
  return value;
}

/** The owner's review progress on a thread, as one label. */
export function ReviewProgressLabel({ thread }: { thread: string }) {
  useT();
  const review = useHinted(() => readReview(thread), thread);
  const progress: ReviewProgress | null | undefined = review?.progress;
  if (!progress) return null;
  return <span className={`queue-cue bundle-progress progress-${progress}`}>{W.progress[progress]}</span>;
}

/** Before sending: shown only when the recipient may not read the files. */
export function ReadinessLine({ messageId, revision }: { messageId: string; revision: number }) {
  useT();
  const result = useHinted(() => readReadiness(messageId), `${messageId}:${revision}`);
  const readiness: Readiness | undefined = result?.readiness;
  if (!result || readiness === "ready") return null;
  const gaps = result.gaps.map((g) => `${g.requirement.path ?? g.requirement.format}: ${g.action}`);
  return (
    <p className={`composer-readiness readiness-${readiness}`} role="status"
       title={gaps.join("\n") || undefined}>
      {W.readiness[readiness!]}
    </p>
  );
}

/** On a draft with files: what the owner asks of the recipient, and which
 *  file is the one to review (a member inside an archive is named through
 *  the brain's set_message_task). Saved as the draft's task when changed;
 *  "No request" clears it. */
export function TaskFields({ draft, disabled, target }: { draft: Draft; disabled: boolean; target?: Target }) {
  useT();
  const task = draft.task;
  const [kind, setKind] = useState<OutcomeKind | "">(task?.outcome_kind ?? "");
  const [words, setWords] = useState(task?.requested_outcome ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    setKind(task?.outcome_kind ?? "");
    setWords(task?.requested_outcome ?? "");
  }, [task?.outcome_kind, task?.requested_outcome]);
  const save = async (next: { kind?: OutcomeKind | ""; words?: string; primary?: string }) => {
    const k = next.kind ?? kind;
    const outcome = (next.words ?? words).trim();
    const primaryId = next.primary ?? task?.primary?.attachment_id ?? draft.attachments[0]?.attachment_id;
    const file = draft.attachments.find((a) => a.attachment_id === primaryId);
    if (k && !outcome) return;                      // waiting for the words
    const value: TaskContext | null = !k ? null : {
      requested_outcome: outcome, outcome_kind: k,
      primary: file ? {
        attachment_id: file.attachment_id, sha256: file.sha256,
        member_path: task?.primary?.attachment_id === file.attachment_id ? task.primary.member_path : null,
      } : null,
      supporting: task?.supporting ?? [], reply_audience: "sender_only",
    };
    if (JSON.stringify(value) === JSON.stringify(task ?? null)) return;
    try {
      await setTask(draft.message_id, draft.draft_revision, value);
      setProblem(null);
    } catch (e) {
      setProblem((e as Error).message);
    }
  };
  return (
    <div className="composer-task" ref={target}>
      <select data-ui-action="input" aria-label={W.request} value={kind} disabled={disabled}
              onChange={(e) => { const k = e.target.value as OutcomeKind | ""; setKind(k); void save({ kind: k }); }}>
        <option value="">{W.noRequest}</option>
        {(Object.keys(W.outcome) as OutcomeKind[]).map((k) => <option key={k} value={k}>{W.outcome[k]}</option>)}
      </select>
      {kind && (
        <input data-ui-action="input" type="text" aria-label={W.request} placeholder={W.request} value={words}
               maxLength={LIMITS.TASK_OUTCOME_MAX_CHARS} disabled={disabled}
               onChange={(e) => setWords(e.target.value)}
               onBlur={() => void save({})} />
      )}
      {kind && draft.attachments.length > 1 && (
        <select data-ui-action="input" aria-label={W.primary} disabled={disabled || !task}
                value={task?.primary?.attachment_id ?? ""}
                onChange={(e) => void save({ primary: e.target.value })}>
          {draft.attachments.map((a) => <option key={a.attachment_id} value={a.attachment_id}>{a.filename}</option>)}
        </select>
      )}
      {problem && <span className="composer-readiness readiness-blocked" role="alert">{problem}</span>}
    </div>
  );
}
