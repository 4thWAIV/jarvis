/**
 * One open decision, answered on the page: the question large, its options
 * as big buttons with the recommended one lit, and a box for any other
 * answer. A workflow's decision (WorkflowDetail.tsx) and a work item's
 * (WorkDetail.tsx) are drawn by this one card; each gives its own `answer`,
 * which records his answer and resolves with a refusal's words, or null.
 *
 * Answering is his own act (or the brain's acting tool on his word), never
 * navigation: the brain can point at the options and the answer box, not
 * press them. The question and options were written by another session,
 * and are drawn as plain text.
 */

import { useState, type ReactNode } from "react";
import { SaidText, useT, type Said } from "./locale";

type ElementRef = (el: Element | null) => void;

export function DecisionCard({ question, options, recommendation, answer, ui, dataKey, icon, more }: {
  question: string;
  options: string[];
  recommendation: string | null;
  answer: (value: string) => Promise<string | null>;
  /** The decision's registered target: its row, options and answer parts. */
  ui: { whole: ElementRef; part: (name: string) => ElementRef };
  dataKey: string;
  icon?: ReactNode;
  /** Anything the decision brings with it, under the recommendation (a work
   *  item's package to open). */
  more?: ReactNode;
}) {
  const t = useT();
  const [text, setText] = useState("");
  const [refused, setRefused] = useState<Said | null>(null);
  const [sending, setSending] = useState(false);
  // A recommendation may say why after the option's own words.
  const isRecommended = (option: string) =>
    !!recommendation && (recommendation === option || recommendation.startsWith(option));
  const send = (value: string) => {
    if (!value.trim() || sending) return;
    setSending(true);
    setRefused(null);
    // The refusal is the server's own words, shown as they came.
    answer(value.trim()).then((why) => {
      setSending(false);
      setRefused(why ? { original: why } : null);
      if (!why) setText("");
    }, (e: Error) => { setSending(false); setRefused({ original: e.message }); });
  };
  return (
    <li className="workflow-decision" data-key={dataKey} ref={(el) => { ui.whole(el); ui.part("row")(el); }}>
      {icon}
      <p className="workflow-decision-question">{question}</p>
      {recommendation && <p className="workflow-recommended">★ {recommendation}</p>}
      {more}
      {options.length > 0 && (
        <div className="workflow-options" ref={ui.part("options")}>
          {options.map((option) => (
            <button data-ui-action="domain" key={option} type="button" disabled={sending}
                    className={`workflow-option${isRecommended(option) ? " recommended" : ""}`}
                    onClick={() => send(option)}>{option}</button>
          ))}
        </div>
      )}
      <div className="workflow-answer" ref={ui.part("answer")}>
        <input data-ui-action="input" type="text" value={text} placeholder={t("main.workflow.anotherAnswer")}
               onChange={(e) => setText(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") send(text); }} />
        <button data-ui-action="domain" type="button" disabled={sending || !text.trim()}
                onClick={() => send(text)}>{t("main.workflow.answer")}</button>
      </div>
      {refused && <SaidText said={refused} as="p" className="read-failure workflow-refused" />}
    </li>
  );
}
