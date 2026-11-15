/**
 * A page work package in the centre area: its entry HTML in a frame, opened
 * by the page's Open page or Open package or the brain's `open_package_page`
 * or `open_local_package` (packages.ts). The server holds it, so a reload
 * shows it again until its × is pressed.
 *
 * The frame is `sandbox="allow-scripts"` and never `allow-same-origin`:
 * its files are served from JARVIS's own origin, so with the same origin
 * the package's scripts could drive every one of JARVIS's routes. Without
 * it the frame has no origin, and its server headers sandbox it the same
 * way if it is opened on its own (web_auth.PACKAGE_PAGE_HEADERS). What it
 * may do (save a result, hear the microphone, be pointed at while its
 * story is told) goes through the bridge (packageBridge.ts).
 *
 * A package with a story has Tell the story, a slide to start from, and
 * Stop: the same `_package_story` the brain's `tell_package_story` calls.
 *
 * Done in its head signs it off (DoneButton), for every package. A page
 * that carries the standard vote (not a deck, which asks on its own slide)
 * has Got it and I have questions beside it (VoteControls), outside the
 * frame: the inbox's answer route, his click, the same answer as his voice
 * and the Work tab give.
 *
 * Reading: a package takes over the page as it opens (packages.ts), and
 * while it has, its head and tools are one thin bar along the top of its
 * document (`.package-bar`), in a row of its own so it covers nothing of
 * the document, that fades back (dimmed, every control still where it is
 * and still taking his click) after a few seconds without the pointer and
 * comes back when the pointer reaches it or keyboard focus enters it; with
 * reduced motion or Steady it stays. Restored, the head and
 * tools stand above the document as before.
 *
 * Focus (PackageFocus.tsx): when a question is his (the story waits on it,
 * or he chose Show in the document beside it in the side column), the part
 * of the document it is about (the waiting slide's targets, the story
 * outline's `focus`) stays lit and the rest dims, and the question's card
 * is presented beside it: the same question, words and answer as the side
 * column's (QuestionItem, usePackageQuestions: one answer route). While a
 * beat is told, its targets are lit, the rest dims lightly, and JARVIS's
 * words for it are shown beside them with Pause (the reading skipped: the
 * story pauses there) and Stop; paused, with Continue. Where a part is
 * comes from the frame's helper (`story.focus` through the bridge), never
 * from the frame's DOM. Escape leaves focus; the side column stays for
 * every question.
 *
 * Registered for pointing at as `packageView` (its head, its frame, Done,
 * Close, the story's controls, the bar, the focus card, its Leave and
 * Pause); Close runs `package.close`, as the brain does.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type FocusEvent, type ReactNode } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { SaidText, useT, type Said } from "./locale";
import { connectBridge, type Bridge, type FrameRect } from "./packageBridge";
import { answerInbox, signOff } from "./packageInbox";
import { markdown } from "./dashboard/markdown";
import { entryUrl, packageFileUrl, packageVote, readPackageText, storyCue, storyRunning, storyState, tellStory,
         type HeaderQuestion, type PackageInfo, type PageVote, type StoryCue } from "./packages";
import { DIM, PackageFocus } from "./PackageFocus";
import { bind, bindingProps, layout } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { TakeoverButton } from "./TakeoverBar";
import { skipReading } from "./voiceStatus";

// How long the bar stays without the pointer before it fades, and how long
// the page waits for the frame to say where a question's part is before it
// presents the question over the whole document.
const BAR_FADES_AFTER_MS = 3000;
const FOCUS_WAIT_MS = 700;

export function PackageView({ info }: { info: PackageInfo }) {
  const t = useT();
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [listening, setListening] = useState(false);
  // Made for the wider layout, or its page asked for room: Take over is lit.
  const [asked, setAsked] = useState(false);
  const offered = info.layout === "wide" || asked;
  const asking = (info.vote?.asks?.length ?? 0) > 0;
  // The author's explanation, read in place beside the page.
  const [reading, setReading] = useState(false);
  useEffect(() => setReading(false), [info.id]);
  const ui = useUiTarget({ ref: { region: "main", view: "main.package", kind: "packageView", key: info.id },
                           label: info.name, socket: CENTRE_SOCKET });
  // Where the frame says each focused part is, by key ("beat:<beat>", or
  // "q:<question>" for a question's part); missing: not said yet.
  const [where, setWhere] = useState<Record<string, FrameRect | null>>({});
  const bridge = useRef<Bridge | null>(null);
  useEffect(() => {
    if (!frame.current) return undefined;
    setWhere({});
    const connected = connectBridge(frame.current, info.id, {
      onSaved: setSaid, onListening: setListening, onStoryMissing: setSaid, onLayoutRequest: () => setAsked(true),
      onFocus: (key, rect) => setWhere((now) => ({ ...now, [key]: rect })),
    });
    bridge.current = connected;
    return () => { bridge.current = null; connected.close(); };
  }, [info.id]);

  const questions = usePackageQuestions(info.id, info.vote?.asks ?? [], setSaid);
  const [voteAnswer, setVoteAnswer] = useState({ answer: info.vote?.answer ?? null, note: info.vote?.note ?? "" });
  useEffect(() => setVoteAnswer({ answer: info.vote?.answer ?? null, note: info.vote?.note ?? "" }), [info.id]);

  // ── focus: the question that is his now, or the beat being told ──────
  const story = useSyncExternalStore(storyState.subscribe, storyState.get);
  const cue = useSyncExternalStore(storyCue.subscribe, storyCue.get);
  const mine = story?.package === info.id ? story : null;
  const [picked, setPicked] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => { setPicked(null); setDismissed(null); }, [info.id]);
  const waitingOn = mine?.state === "waiting" ? mine.question ?? null : null;
  useEffect(() => { if (!waitingOn) setDismissed(null); }, [waitingOn]);
  const presentable = (id: string | null) => !!id && (id === info.vote?.question ||
    questions.shown.some((q) => q.id === id && q.applies));
  const current = picked && presentable(picked) ? picked
    : waitingOn !== dismissed && presentable(waitingOn) ? waitingOn : null;
  const targets = current ? info.story?.focus?.[current] ?? [] : [];
  const focusKey = current ? `q:${current}` : null;
  const [waited, setWaited] = useState<string | null>(null);
  useEffect(() => {
    // Asked afresh: where the part was before counts for nothing now.
    setWaited(null);
    if (focusKey) setWhere((now) => { const fresh = { ...now }; delete fresh[focusKey]; return fresh; });
    bridge.current?.focus(focusKey && targets.length ? focusKey : null, targets);
    if (!focusKey || !targets.length) return undefined;
    const late = setTimeout(() => setWaited(focusKey), FOCUS_WAIT_MS);
    return () => clearTimeout(late);
  }, [focusKey, targets.join("\u0000")]);   // eslint-disable-line react-hooks/exhaustive-deps

  // The beat being told, and the last one while the story is paused on it.
  const beat = cue?.package === info.id ? cue : null;
  const lastBeat = useRef<StoryCue | null>(null);
  if (beat) lastBeat.current = beat;
  const running = storyRunning(mine, info.id);
  const [narrationOff, setNarrationOff] = useState(false);
  useEffect(() => { if (!running) { setNarrationOff(false); lastBeat.current = null; } }, [running]);
  const paused = mine?.state === "paused" ? lastBeat.current : null;
  const told = !current && !narrationOff ? beat ?? paused : null;

  // Escape leaves focus, before anything else hears it (Escape also skips
  // a reading; leaving focus is all it does here).
  const leave = useCallback(() => {
    if (current) {
      setPicked(null);
      if (waitingOn === current) setDismissed(current);
    } else if (told) setNarrationOff(true);
  }, [current, waitingOn, told]);
  useEffect(() => {
    if (!current && !told) return undefined;
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      leave();
    };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, [current, told, leave]);

  // ── reading: the bar over the document while it has taken over ──────
  const arranged = useSyncExternalStore(layout.subscribe, layout.get);
  const bar = arranged.mode === "takeover" && arranged.view === "package" && arranged.key === info.id;
  const fading = useFadingBar(bar);
  const voteInCard = current !== null && current === info.vote?.question;

  const tools = (info.story || info.communication?.kind === "architecture") && <div className="package-tools">
        {info.story && <StoryControls info={info} onSaid={setSaid} part={ui.part} />}
        {info.communication?.kind === "architecture" && <section className="package-canonical" aria-label={t("main.package.canonical")}
                 title={t("main.package.canonicalState", { revision: info.communication.source_revision?.slice(0, 12) ?? "" })}>
          <span className="package-canonical-state">{t("main.package.canonicalState", { revision: info.communication.source_revision?.slice(0, 12) ?? "" })}</span>{" "}
          <a href={packageFileUrl(info, "source.yaml")} target="_blank" rel="noopener noreferrer" ref={ui.part("source")} data-ui-action="domain">{t("main.package.source")}</a>{" · "}
          <a href={packageFileUrl(info, "check.json")} target="_blank" rel="noopener noreferrer" ref={ui.part("check")} data-ui-action="domain">{t("main.package.check")}</a>
          {info.communication.readable && <> {" · "}
            <button type="button" className="link-button" ref={ui.part("readable")} data-ui-action="domain"
                    aria-expanded={reading} aria-controls="package-explanation"
                    onClick={() => setReading(!reading)}>
              {reading ? t("main.package.readableHide") : t("main.package.readable")}</button>
          </>}
        </section>}
      </div>;
  const head = (
    <section className="package-head" ref={ui.part("header")}>
      <div className="package-head-words">
        <h2 title={info.communication?.title ?? info.name}>{info.communication?.title ?? info.name}</h2>
        <p title={info.purpose}>{info.purpose}</p>
        {offered && <p className="package-wide" role="status">{t("main.package.wide")}</p>}
      </div>
      {bar && tools}
      {info.vote?.question && !voteInCard &&
        <VoteControls id={info.id} vote={info.vote} now={voteAnswer} onAnswer={setVoteAnswer}
                      onSaid={setSaid} part={ui.part} />}
      <DoneButton id={info.id} onSaid={setSaid} target={ui.part("done")} />
      <TakeoverButton partRef={ui.part("takeover")} lit={offered} />
      <button type="button" className="workflow-close" title={t("main.common.close")}
              aria-label={t("main.common.close")} ref={ui.part("close")}
              {...bindingProps(bind("package.close", {}))}>×</button>
    </section>
  );

  let focus: ReactNode = null;
  if (current) {
    const rect = targets.length ? where[focusKey!] : null;
    if (rect !== undefined || waited === focusKey) {
      const q = questions.shown.find((one) => one.id === current);
      focus = (
        <PackageFocus frame={frame.current} rect={rect ?? null} kind="decision" takeFocus
                      dim={rect ? DIM.decision : DIM.whole} cardRef={ui.part("focus")}
                      label={q?.question ?? t("main.package.vote.label")}>
          <LeaveFocus onLeave={leave} target={ui.part("focusClose")} />
          {q ? <QuestionItem q={q} questions={questions} lead
                             onAnswered={() => { if (picked === q.id) setPicked(null); }} />
            : info.vote && <VoteControls id={info.id} vote={info.vote} now={voteAnswer} onAnswer={setVoteAnswer}
                                         onSaid={setSaid} part={ui.part} />}
        </PackageFocus>
      );
    }
  } else if (told) {
    const lit = beat && beat.targets.length ? where[`beat:${beat.beat}`] : null;
    if (lit !== undefined) {
      focus = (
        <PackageFocus frame={frame.current} rect={lit} kind="narration" takeFocus={false}
                      dim={lit ? DIM.narration : 0} cardRef={ui.part("focus")}
                      label={t("main.package.focus.narration")}>
          <LeaveFocus onLeave={leave} target={ui.part("focusClose")} />
          <Narration info={info} beat={told} paused={!beat} slide={mine?.slide ?? told.slide}
                     onSaid={setSaid} pauseRef={ui.part("pause")} />
        </PackageFocus>
      );
    }
  }

  return (
    <div id="package-view" data-package={info.id} data-layout={bar ? "bar" : "page"} ref={ui.whole}>
      {bar
        ? <div className="package-bar" data-faded={fading.faded ? "true" : "false"}
               ref={ui.part("bar")} {...fading.props}>{head}</div>
        : <>{head}{tools}</>}
      <div className="package-body">
        <iframe ref={(el) => { frame.current = el; ui.part("frame")(el); }} className="package-frame"
                title={info.name} src={entryUrl(info)} sandbox="allow-scripts" />
        {(asking || (reading && info.communication?.readable)) && <aside className="package-side">
          {asking &&
            <HeaderQuestions questions={questions} target={ui.part("asks")} focused={current}
                             onFocus={setPicked} />}
          {reading && info.communication?.readable &&
            <Explanation key={`${info.id}:${info.communication.revision ?? ""}`} info={info}
                         target={ui.part("explanation")} />}
        </aside>}
      </div>
      <p className="package-status" role="status">
        {listening ? `${t("main.package.listening")} ` : ""}
        {said && <SaidText said={said} className="package-said" />}
      </p>
      {focus}
    </div>
  );
}

/** The bar's fading: shown at first, faded (dimmed, never gone: it stays
 *  where it is and every control in it still takes his click) once the
 *  pointer has been away from it for BAR_FADES_AFTER_MS, back as soon as
 *  the pointer is over it or keyboard focus is in it. Never faded with
 *  reduced motion or Steady. */
function useFadingBar(active: boolean) {
  const [faded, setFaded] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const held = useRef({ pointer: false, focus: false });
  const later = useCallback(() => {
    clearTimeout(timer.current);
    const steady = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ||
      document.documentElement.dataset.motion === "steady";
    if (held.current.pointer || held.current.focus || steady) return;
    timer.current = setTimeout(() => setFaded(true), BAR_FADES_AFTER_MS);
  }, []);
  const show = useCallback(() => { clearTimeout(timer.current); setFaded(false); }, []);
  useEffect(() => {
    if (!active) { clearTimeout(timer.current); setFaded(false); return undefined; }
    later();
    return () => clearTimeout(timer.current);
  }, [active, later]);
  return {
    faded: active && faded,
    props: {
      onPointerEnter: () => { held.current.pointer = true; show(); },
      onPointerMove: show,
      onPointerLeave: () => { held.current.pointer = false; later(); },
      onFocus: () => { held.current.focus = true; show(); },
      onBlur: (e: FocusEvent<HTMLElement>) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        held.current.focus = false;
        later();
      },
    },
  };
}

/** The focus card's Leave: the same as Escape. */
function LeaveFocus({ onLeave, target }: { onLeave: () => void; target: (el: Element | null) => void }) {
  const t = useT();
  return (
    <button type="button" className="workflow-close package-focus-leave" data-ui-action="domain"
            title={t("main.package.focus.leave")} aria-label={t("main.package.focus.leave")}
            ref={target} onClick={onLeave}>×</button>
  );
}

/** JARVIS's words for the beat being told, with Pause (the reading
 *  skipped, as the playback bar's Skip does: a slide not heard in full
 *  pauses the story there) and Stop; paused, Continue tells it again from
 *  that slide. */
function Narration({ info, beat, paused, slide, onSaid, pauseRef }: {
  info: PackageInfo; beat: StoryCue; paused: boolean; slide: number;
  onSaid: (s: Said | null) => void; pauseRef: (el: Element | null) => void }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const act = async (action: "tell" | "stop") => {
    setBusy(true);
    onSaid(await tellStory(info.id, action, action === "tell" ? slide : undefined));
    setBusy(false);
  };
  return (
    <div className="package-narration" data-beat={beat.beat}>
      <p className="package-narration-head">
        {paused ? t("main.package.story.paused", { slide, slides: info.story?.slides.length ?? slide })
          : t("main.package.focus.saying")}
      </p>
      <p className="package-narration-words">{beat.say ?? ""}</p>
      <div className="package-ask-options">
        {paused
          ? <button type="button" className="settings-btn primary" data-ui-action="domain" disabled={busy}
                    onClick={() => void act("tell")}>{t("main.package.story.continue")}</button>
          : <button type="button" className="settings-btn" data-ui-action="speech" ref={pauseRef}
                    onClick={() => skipReading.current()}>{t("main.package.focus.pause")}</button>}
        <button type="button" className="settings-btn" data-ui-action="domain" disabled={busy}
                onClick={() => void act("stop")}>{t("main.package.story.stop")}</button>
      </div>
    </div>
  );
}

/** Done: he signs the package off, whatever kind it is (packageInbox.ts
 *  `signOff`, the Queue row's Done and the brain's sign_off_package too).
 *  It sits in the head, outside the package's frame, so no package can hide
 *  or press it. Once signed off the server closes this view on every page. */
function DoneButton({ id, onSaid, target }: {
  id: string; onSaid: (s: Said | null) => void; target: (el: Element | null) => void }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  return (
    <button type="button" className="settings-btn primary package-done" data-ui-action="domain"
            disabled={busy} ref={target} title={t("main.package.doneTitle")}
            onClick={() => {
              setBusy(true);
              void signOff(id).then((why) => { setBusy(false); onSaid(why); });
            }}>
      {t("main.package.done")}</button>
  );
}

type VotePart = "vote" | "gotIt" | "questions" | "words" | "send";

/** The standard vote in the head: Got it records at once; I have questions
 *  opens a field for his words, recorded with Send. Each goes through the
 *  inbox's answer route (answerInbox), which takes only his click. */
function VoteControls({ id, vote, now, onAnswer, onSaid, part }: {
  id: string; vote: PageVote; now: { answer: PageVote["answer"]; note: string };
  onAnswer: (now: { answer: PageVote["answer"]; note: string }) => void; onSaid: (s: Said | null) => void;
  part: (name: VotePart) => (el: Element | null) => void }) {
  const t = useT();
  const answer = now.answer;
  const [asking, setAsking] = useState(false);
  const [words, setWords] = useState(now.note);
  const [busy, setBusy] = useState(false);
  const record = async (option: "got-it" | "questions", note = "") => {
    setBusy(true);
    const problem = await answerInbox(id, vote.question ?? "understood", option, note);
    setBusy(false);
    if (problem) {
      onSaid({ original: problem });
      return;
    }
    onAnswer({ answer: option, note });
    setAsking(false);
    onSaid(null);
  };
  return (
    <div className="package-vote" role="group" aria-label={t("main.package.vote.label")} ref={part("vote")}
         data-answer={answer ?? undefined}>
      <button type="button" className="settings-btn" data-ui-action="domain" disabled={busy}
              aria-pressed={answer === "got-it"} ref={part("gotIt")} onClick={() => void record("got-it")}>
        {t("main.package.vote.gotIt")}</button>
      <button type="button" className="settings-btn" data-ui-action="domain" disabled={busy}
              aria-pressed={answer === "questions"} aria-expanded={asking} ref={part("questions")}
              onClick={() => setAsking(!asking)}>
        {t("main.package.vote.questions")}</button>
      {asking && (
        <>
          <input type="text" className="package-vote-words" data-ui-action="input" value={words}
                 placeholder={t("main.package.vote.words")} aria-label={t("main.package.vote.words")}
                 ref={part("words")} maxLength={500} onChange={(e) => setWords(e.target.value)} />
          <button type="button" className="settings-btn primary" data-ui-action="domain"
                  disabled={busy || !words.trim()} ref={part("send")}
                  onClick={() => void record("questions", words.trim())}>
            {t("main.package.vote.send")}</button>
        </>
      )}
      {!asking && answer && (
        <span className="package-vote-recorded" role="status">
          {answer === "got-it" ? t("main.package.vote.gotItRecorded") : t("main.package.vote.questionsRecorded")}
        </span>
      )}
    </div>
  );
}

type Questions = ReturnType<typeof usePackageQuestions>;

/** The questions a package's story declares, and his answers: one record
 *  for the side column and the focus card alike. Each answer goes through
 *  the inbox's one answer route (answerInbox), as his voice records it
 *  too; the server is asked again after each, since a follow-up may apply
 *  once its parent is answered. */
function usePackageQuestions(id: string, asks: HeaderQuestion[], onSaid: (s: Said | null) => void) {
  const [shown, setShown] = useState(asks);
  const [words, setWords] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => { setShown(asks); setWords({}); }, [id]);   // eslint-disable-line react-hooks/exhaustive-deps
  const record = async (question: HeaderQuestion, option: string): Promise<boolean> => {
    setBusy(true);
    const note = (words[question.id] ?? "").trim();
    const problem = await answerInbox(id, question.id, option, note);
    if (problem) {
      setBusy(false);
      onSaid({ original: problem });
      return false;
    }
    const now = await packageVote(id).catch(() => null);
    setShown((was) => now?.asks ?? was.map((q) => q.id === question.id ? { ...q, answer: option, note } : q));
    setBusy(false);
    onSaid(null);
    return true;
  };
  return { shown, words, setWords, busy, record };
}

/** The questions the story declares, in the side column: each that applies
 *  yet, his approval first, with Show in the document, which presents it in
 *  the focus card beside the part it is about. */
function HeaderQuestions({ questions, target, focused, onFocus }: {
  questions: Questions; target: (el: Element | null) => void;
  focused: string | null; onFocus: (id: string) => void }) {
  const t = useT();
  const { shown } = questions;
  return (
    <section className="package-asks" aria-label={t("main.package.asks.label")} ref={target}>
      {/* His approval of the design first: it is the decision the package is for. */}
      {[...shown.filter((q) => q.kind === "approval"), ...shown.filter((q) => q.kind !== "approval")]
        .filter((q) => q.applies).map((q) => (
          <QuestionItem key={q.id} q={q} questions={questions}>
            <button type="button" className="link-button package-ask-show" data-ui-action="domain"
                    aria-pressed={focused === q.id} onClick={() => onFocus(q.id)}>
              {t("main.package.focus.show")}</button>
          </QuestionItem>
        ))}
    </section>
  );
}

/** One question: its words, its options as buttons (his click records it at
 *  once, with his words if he typed any) and what is recorded. In the focus
 *  card (`lead`) it opens with why it is his and the evidence. */
function QuestionItem({ q, questions, lead = false, onAnswered, children }: {
  q: HeaderQuestion; questions: Questions; lead?: boolean; onAnswered?: () => void; children?: ReactNode }) {
  const t = useT();
  const { words, setWords, busy, record } = questions;
  const approval = q.kind === "approval";
  const typed = (words[q.id] ?? "").trim();
  return (
    <div className={approval ? "package-ask package-approval" : "package-ask"}
         role="group" aria-label={q.question} data-question={q.id}
         data-kind={q.kind ?? "question"} data-answer={q.answer ?? undefined}
         data-stale={q.stale ? "true" : undefined}>
      {approval && (
        <p className="package-approval-head">
          {t("main.package.approval.head", { revision: (q.revision ?? "").slice(0, 12) })}
        </p>
      )}
      {lead ? <h3 className="package-ask-question">{q.question}</h3>
        : <p className="package-ask-question">{q.question}</p>}
      {lead && q.reason && (
        <p className="package-ask-why"><strong>{t("main.package.focus.why")}</strong> {q.reason}</p>
      )}
      {q.context && (lead
        ? <p className="package-ask-context"><strong>{t("main.package.focus.evidence")}</strong> {q.context}</p>
        : <p className="package-ask-context">{q.context}</p>)}
      <div className="package-ask-options">
        {q.options.map((o) => {
          const needsWords = q.note_required?.includes(o.id) ?? false;
          return (
            <button key={o.id} type="button" className="settings-btn" data-ui-action="domain"
                    data-option={o.id} data-recommended={q.recommended === o.id ? "true" : undefined}
                    disabled={busy || (needsWords && !typed)}
                    aria-pressed={q.answer === o.id && !q.stale}
                    title={needsWords && !typed ? t("main.package.approval.needsWords") : o.says}
                    onClick={() => void record(q, o.id).then((ok) => { if (ok) onAnswered?.(); })}>
              {o.name}{q.recommended === o.id ? ` (${t("main.package.asks.recommended")})` : ""}
            </button>
          );
        })}
        <input type="text" className="package-vote-words" data-ui-action="input"
               value={words[q.id] ?? ""} maxLength={500}
               placeholder={approval ? t("main.package.approval.words") : t("main.package.asks.words")}
               aria-label={approval ? t("main.package.approval.words") : t("main.package.asks.words")}
               onChange={(e) => setWords({ ...words, [q.id]: e.target.value })} />
      </div>
      {q.answer && q.stale && (
        <span className="package-approval-stale" role="status">
          {t("main.package.approval.stale", { revision: (q.answered_revision ?? "").slice(0, 12) })}
        </span>
      )}
      {q.answer && !q.stale && (
        <span className="package-vote-recorded" role="status">
          {approval
            ? t(q.answer === "approve" ? "main.package.approval.approved" : "main.package.approval.changes",
                { revision: (q.answered_revision ?? "").slice(0, 12) })
            : t("main.package.asks.recorded", {
                choice: q.options.find((o) => o.id === q.answer)?.name ?? q.answer })}
        </span>
      )}
      {children}
    </div>
  );
}

/** The author's explanation (UNDERSTANDING.md), read in place: fetched
 *  through the package's own file route, which for an architecture package
 *  is the canonical gate at its exact revision, and drawn by the app's one
 *  Markdown renderer (text nodes only; links only to http, https and
 *  mailto). Keyed by package and revision, so a replaced package never
 *  shows the one before it. */
function Explanation({ info, target }: { info: PackageInfo; target: (el: Element | null) => void }) {
  const t = useT();
  const body = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<"loading" | "shown" | "failed">("loading");
  const [why, setWhy] = useState("");
  useEffect(() => {
    const stop = new AbortController();
    void readPackageText(info, "UNDERSTANDING.md", stop.signal).then((text) => {
      if (stop.signal.aborted || !body.current) return;
      body.current.replaceChildren(markdown(text, "md package-explanation-text",
                                            { links: true, tables: true }));
      setState("shown");
    }, (e: Error) => {
      if (stop.signal.aborted) return;
      setWhy(e.message);
      setState("failed");
    });
    return () => stop.abort();
  }, [info]);
  return (
    <section id="package-explanation" className="package-explanation" ref={target}
             aria-label={t("main.package.readable")} aria-busy={state === "loading"}>
      {state === "loading" && <p role="status">{t("main.package.explanation.loading")}</p>}
      {state === "failed" && <p role="alert">{t("main.package.explanation.failed", { why })}</p>}
      <div ref={body} />
    </section>
  );
}

type StoryPart = "story" | "tell" | "fromSlide" | "resume" | "stop";

function StoryControls({ info, onSaid, part }: {
  info: PackageInfo; onSaid: (s: Said | null) => void; part: (name: StoryPart) => (el: Element | null) => void }) {
  const t = useT();
  const state = useSyncExternalStore(storyState.subscribe, storyState.get);
  const [slide, setSlide] = useState(1);
  const [busy, setBusy] = useState(false);
  const running = storyRunning(state, info.id);
  const mine = state?.package === info.id ? state : null;
  const slides = info.story?.slides ?? [];
  const act = async (action: "tell" | "resume" | "stop") => {
    setBusy(true);
    const problem = await tellStory(info.id, action, action === "tell" ? slide : undefined);
    setBusy(false);
    onSaid(problem);
  };
  return (
    <section className="package-story" aria-label={t("main.package.story.label")} ref={part("story")}>
      <button data-ui-action="domain" type="button" className="folder-button" disabled={busy}
              ref={part("tell")} onClick={() => void act("tell")}>
        {running ? t("main.package.story.tellFrom") : t("main.package.story.tell")}
      </button>
      <label>
        {t("main.package.story.fromSlide")}{" "}
        <select data-ui-action="input" value={slide} ref={part("fromSlide")}
                onChange={(e) => setSlide(Number(e.target.value))}>
          {slides.map((title, n) => <option key={n} value={n + 1}>{n + 1}. {title}</option>)}
        </select>
      </label>
      {!running && (info.story?.asks?.length ?? 0) > 0 && (
        <button data-ui-action="domain" type="button" className="folder-button" disabled={busy}
                ref={part("resume")} onClick={() => void act("resume")}>{t("main.package.story.continue")}</button>
      )}
      <button data-ui-action="domain" type="button" className="folder-button" disabled={busy || !running}
              ref={part("stop")} onClick={() => void act("stop")}>{t("main.package.story.stop")}</button>
      <span className="package-story-now" role="status">
        {mine?.state === "telling" ? t("main.package.story.telling", { slide: mine.slide, slides: mine.slides })
          : mine?.state === "waiting" ? t("main.package.story.waiting", { slide: mine.slide, slides: mine.slides })
            : mine?.state === "paused" ? t("main.package.story.paused", { slide: mine.slide, slides: mine.slides })
              : mine?.state === "finished" ? t("main.package.story.told")
                : mine?.state === "stopped" ? t("main.package.story.stopped") : ""}
      </span>
    </section>
  );
}
