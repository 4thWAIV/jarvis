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
 * The presentation first: the icon border (PackageBorder.tsx: the story's
 * controls, chapters, More, Done, Take over and Close) is the only bar, and
 * the package's own page takes the whole width of the view below it. For a
 * deck it is the one navigation too: its count follows the slide the deck
 * says it shows (`slide.shown`), and its Previous, Next and Chapters open a
 * slide in the deck (`slide.open`), which draws no bar of its own here. With
 * nothing open, nothing covers it. A package takes over the page as it
 * opens (packages.ts), and while it has, the border fades back (dimmed,
 * every control still where it is and still taking his click) after a few
 * seconds without the pointer and comes back when the pointer reaches it
 * or keyboard focus enters it; with reduced motion or Steady it stays.
 *
 * Done in the border signs it off (DoneButton), for every package. The
 * vote row under the border holds the standard vote on the package itself
 * (a deck's too, beside its own slide), Approve and Reject (VoteControls): the inbox's
 * answer route, his click, the same answer as his voice and the Work tab
 * give; and, when the story declares questions, Questions, which opens the
 * first one he has not answered.
 *
 * Focus (PackageFocus.tsx): every question is asked in a focus card. When
 * the story waits on one, or he opens one from the vote row, the part of
 * the document it is about (the waiting slide's targets, the story
 * outline's `focus`) stays lit and the rest dims, and the card is
 * presented beside it; a question about no part gets the card centred over
 * the whole view. The card carries the question, why it is his, the
 * evidence, its options, his words and what is recorded, and "Question N
 * of M" with Back and Next through the questions (the standard vote last).
 * While a beat is told, its targets are lit, the rest dims lightly, and
 * JARVIS's words for it are shown beside them with Pause (the reading
 * skipped: the story pauses there) and Stop; paused, with Continue. An
 * architecture package's explanation opens as a card centred over the
 * whole view, scrolling within it. Where a part is comes from the frame's
 * helper (`story.focus` through the bridge), never from the frame's DOM.
 * Escape closes whatever card is open, and keyboard focus goes back where
 * it was.
 *
 * An architecture view set (several checked views of one system,
 * `info.views`) shows one view at a time in the frame: the story's beat
 * shows the view its slide is told over, and the border's chapters, while
 * no story is told, open the chapter's view. The element a beat points at
 * (or the one he clicks in a view) stays lit in every view that shows it,
 * and the chapters say which views show it. The source and check links
 * follow the view shown.
 *
 * Registered for pointing at as `packageView` (its border, its frame, Done,
 * Close, the story's controls, the vote, Questions, the focus card, its
 * Leave, Back, Next and Pause); Close runs `package.close`, as the brain
 * does.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type FocusEvent, type ReactNode } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { SaidText, useT, type Said } from "./locale";
import { connectBridge, type Bridge, type FrameRect } from "./packageBridge";
import { answerInbox, signOff } from "./packageInbox";
import { markdown } from "./dashboard/markdown";
import { declarationsIn, entryUrl, navigation, packageFileUrl, packageVote, readPackageText, storyCue, storyRunning, storyState, tellStory,
         viewFolder, viewShown, type HeaderQuestion, type PackageInfo, type PageVote, type StoryCue } from "./packages";
import { useUiTarget } from "./uiRegistry";
import { layout } from "./uiNavigation";
import { PackageBorder } from "./PackageBorder";
import { PackageIcon } from "./PackageIcons";
import { DIM, PackageFocus } from "./PackageFocus";
import { skipReading } from "./voiceStatus";

// How long the border stays without the pointer before it fades, and how
// long the page waits for the frame to say where a question's part is
// before it presents the question over the whole document.
const BAR_FADES_AFTER_MS = 3000;
const FOCUS_WAIT_MS = 700;

// The standard vote's question (package_inbox.understood): any other id on
// the vote is an answer given before Got it was retired, only read back.
const STANDARD_VOTE = "approve-package";

export function PackageView({ info }: { info: PackageInfo }) {
  const t = useT();
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [listening, setListening] = useState(false);
  const [presentation, setPresentation] = useState(false);
  // Made for the wider layout, or its page asked for room: Take over is lit.
  const [asked, setAsked] = useState(false);
  const offered = info.layout === "wide" || asked;
  // The author's explanation, opened as a card over the whole view.
  const [reading, setReading] = useState(false);
  // The story slide the deck says it shows, counted each time it says so;
  // null until a page reports one (only such a page is moved by the border).
  const [shown, setShown] = useState<{ slide: number; seq: number } | null>(null);
  const outline = useRef(info);
  outline.current = info;
  // A view set: the view the frame shows (null: the first), and the
  // elements lit in every view that shows them.
  const [chosenView, setChosenView] = useState<string | null>(null);
  const [lit, setLit] = useState<string[]>([]);
  const view = viewShown(info, chosenView);
  useEffect(() => { setReading(false); setPresentation(false); setShown(null); setChosenView(null); setLit([]); }, [info.id]);
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
      onPresentationReady: () => setPresentation(true),
      onPresentationReturnFocus: () => document.querySelector<HTMLButtonElement>("#package-view [data-package-menu=more]")?.focus(),
      onFocus: (key, rect) => setWhere((now) => ({ ...now, [key]: rect })),
      onSlideShown: (opener) => {
        const pages = outline.current?.pages;
        const slide = pages?.length ? pages.findIndex((p) => p.opener === opener) + 1
          : (outline.current?.story?.openers ?? []).findIndex((list) => list.includes(opener)) + 1;
        if (slide) setShown((was) => ({ slide, seq: (was?.seq ?? 0) + 1 }));
      },
      onSelected: (declaration) => { if (outline.current?.views?.length) setLit([declaration]); },
    });
    bridge.current = connected;
    return () => { bridge.current = null; connected.close(); };
  }, [info.id]);

  const questions = usePackageQuestions(info.id, info.vote?.asks ?? [], setSaid);
  const [voteAnswer, setVoteAnswer] = useState({ answer: info.vote?.answer ?? null, note: info.vote?.note ?? "" });
  useEffect(() => setVoteAnswer({ answer: info.vote?.answer ?? null, note: info.vote?.note ?? "" }), [info.id]);
  const standardVote = info.vote?.question === STANDARD_VOTE;

  // Every question the card steps through, in order: his approval of a
  // design first (it is the decision the package is for), then the story's
  // other questions that apply yet, then the standard vote.
  const applying = questions.shown.filter((q) => q.applies);
  const sequence = [...applying.filter((q) => q.kind === "approval").map((q) => q.id),
                    ...applying.filter((q) => q.kind !== "approval").map((q) => q.id),
                    ...(standardVote ? [STANDARD_VOTE] : [])];
  const answered = (id: string) => id === STANDARD_VOTE ? !!voteAnswer.answer
    : !!questions.shown.find((q) => q.id === id)?.answer;
  const firstOpen = sequence.find((id) => !answered(id)) ?? sequence[0] ?? null;

  // ── focus: the question that is his now, or the beat being told ──────
  const story = useSyncExternalStore(storyState.subscribe, storyState.get);
  const cue = useSyncExternalStore(storyCue.subscribe, storyCue.get);
  const mine = story?.package === info.id ? story : null;
  const [picked, setPicked] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  useEffect(() => { setPicked(null); setDismissed(null); }, [info.id]);
  const waitingOn = mine?.state === "waiting" ? mine.question ?? null : null;
  useEffect(() => { if (!waitingOn) setDismissed(null); }, [waitingOn]);
  const presentable = (id: string | null) => !!id && sequence.includes(id);
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

  // A question opened (from the vote row, or by Back and Next) closes the
  // explanation: one card at a time.
  const open = (id: string | null) => { setPicked(id); if (id) setReading(false); };

  // The beat being told, and the last one while the story is paused on it.
  const beat = cue?.package === info.id ? cue : null;
  // A view set: the beat shows its slide's view, and what it points at
  // precisely stays lit as the views change.
  useEffect(() => {
    if (!beat || !info.views?.length) return;
    if (beat.view) setChosenView(beat.view);
    const named = declarationsIn(beat.targets);
    if (named.length) setLit(named);
  }, [beat?.beat, info.views]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { bridge.current?.light(lit); }, [lit]);
  const lastBeat = useRef<StoryCue | null>(null);
  if (beat) lastBeat.current = beat;
  const running = storyRunning(mine, info.id);
  const [narrationOff, setNarrationOff] = useState(false);
  useEffect(() => { if (!running) { setNarrationOff(false); lastBeat.current = null; } }, [running]);
  const paused = mine?.state === "paused" ? lastBeat.current : null;
  const explaining = !current && reading && !!info.communication?.readable;
  const told = !current && !explaining && !narrationOff ? beat ?? paused : null;

  // Escape closes the card that is open, before anything else hears it
  // (Escape also skips a reading; closing the card is all it does here).
  const leave = useCallback(() => {
    if (current) {
      setPicked(null);
      if (waitingOn === current) setDismissed(current);
    } else if (explaining) setReading(false);
    else if (told) setNarrationOff(true);
  }, [current, waitingOn, explaining, told]);
  useEffect(() => {
    if (!current && !explaining && !told) return undefined;
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      leave();
    };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, [current, explaining, told, leave]);

  // ── reading: the border fades while the package has taken over ───────
  const arranged = useSyncExternalStore(layout.subscribe, layout.get);
  const tookOver = arranged.mode === "takeover" && arranged.view === "package" && arranged.key === info.id;
  const fading = useFadingBar(tookOver);
  const voteInCard = current === STANDARD_VOTE;

  let focus: ReactNode = null;
  if (current) {
    const rect = targets.length ? where[focusKey!] : null;
    if (rect !== undefined || waited === focusKey) {
      const q = questions.shown.find((one) => one.id === current);
      const at = sequence.indexOf(current);
      // Next past the last question closes the card.
      const step = (by: number) => open(sequence[at + by] ?? null);
      focus = (
        <PackageFocus frame={frame.current} rect={rect ?? null} kind="decision" takeFocus
                      dim={rect ? DIM.decision : DIM.whole} cardRef={ui.part("focus")}
                      label={q?.question ?? (info.vote?.words || t("main.package.vote.label"))}>
          <LeaveFocus onLeave={leave} target={ui.part("focusClose")} />
          <p className="package-focus-position">
            {t("main.package.questions.position", { n: at + 1, m: sequence.length })}</p>
          {/* Answered, the card stays on the question with what is recorded;
              Next moves on (the story, waiting on it, moves on by itself). */}
          {q ? <QuestionItem q={q} questions={questions} />
            : info.vote && <VoteControls id={info.id} vote={info.vote} now={voteAnswer} onAnswer={setVoteAnswer}
                                         onSaid={setSaid} part={ui.part} lead />}
          <div className="package-focus-steps">
            <button type="button" className="settings-btn" data-ui-action="domain" ref={ui.part("questionBack")}
                    disabled={at <= 0} onClick={() => step(-1)}>{t("main.package.questions.back")}</button>
            <button type="button" className="settings-btn" data-ui-action="domain" ref={ui.part("questionNext")}
                    onClick={() => step(1)}>{t("main.package.questions.next")}</button>
          </div>
        </PackageFocus>
      );
    }
  } else if (explaining) {
    focus = (
      <PackageFocus frame={frame.current} rect={null} kind="explanation" takeFocus dim={DIM.whole}
                    cardRef={ui.part("focus")} label={t("main.package.readable")}>
        <LeaveFocus onLeave={leave} target={ui.part("focusClose")} />
        <Explanation key={`${info.id}:${info.communication!.revision ?? ""}`} info={info}
                     target={ui.part("explanation")} />
      </PackageFocus>
    );
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

  const unanswered = sequence.filter((id) => id !== STANDARD_VOTE && !answered(id)).length;
  const asking = sequence.some((id) => id !== STANDARD_VOTE);
  return (
    <div id="package-view" data-package={info.id} ref={ui.whole}>
      <PackageBorder info={info} offered={offered} onSaid={setSaid} part={ui.part} presentation={presentation}
        fade={fading} shown={shown} lit={lit}
        openSlide={view ? ((slide) => {
          const named = navigation(info)[slide - 1]?.view;
          if (named) setChosenView(named);
        }) : shown && ((slide) => {
          const opener = navigation(info)[slide - 1]?.opener;
          if (opener) bridge.current?.openSlide(opener);
        })}
        command={(type, value) => frame.current?.contentWindow?.postMessage({jarvis: "package", type, enabled: value}, "*")}
        done={<DoneButton id={info.id} onSaid={setSaid} target={ui.part("done")} />} />
      {(asking || (info.vote?.question && !voteInCard)) &&
        <div className="package-vote-row">
          {asking &&
            <button type="button" className="settings-btn package-questions-open" data-ui-action="domain"
                    ref={ui.part("asks")} aria-pressed={current !== null && current !== STANDARD_VOTE}
                    onClick={() => open(firstOpen)}>
              {unanswered ? t("main.package.questions.open", { left: unanswered })
                : t("main.package.questions.allAnswered")}</button>}
          {info.vote?.question && !voteInCard &&
            <VoteControls id={info.id} vote={info.vote} now={voteAnswer} onAnswer={setVoteAnswer}
                          onSaid={setSaid} part={ui.part} />}
        </div>}
      {/* Architecture links stay compact under the border; the explanation
          opens as a card over the whole view. */}
      {info.communication?.kind === "architecture" && <div className="package-tools">
        <section className="package-canonical" aria-label={t("main.package.canonical")}
                 title={t("main.package.canonicalState", { revision: info.communication.source_revision?.slice(0, 12) ?? "" })}>
          <span className="package-canonical-state">{t("main.package.canonicalState", { revision: info.communication.source_revision?.slice(0, 12) ?? "" })}</span>{" "}
          {view && info.views && <><span className="package-view-now" data-view={view.id}>
            {t("main.package.view.position", { n: info.views.indexOf(view) + 1, m: info.views.length, title: view.title })}</span>{" · "}</>}
          <a href={packageFileUrl(info, viewFolder(view) + "source.yaml")} target="_blank" rel="noopener noreferrer" ref={ui.part("source")} data-ui-action="domain">{t("main.package.source")}</a>{" · "}
          <a href={packageFileUrl(info, viewFolder(view) + "check.json")} target="_blank" rel="noopener noreferrer" ref={ui.part("check")} data-ui-action="domain">{t("main.package.check")}</a>
          {info.communication.readable && <> {" · "}
            <button type="button" className="link-button" ref={ui.part("readable")} data-ui-action="domain"
                    aria-expanded={explaining} aria-controls="package-explanation"
                    onClick={() => { if (explaining) setReading(false); else { setPicked(null); setReading(true); } }}>
              {explaining ? t("main.package.readableHide") : t("main.package.readable")}</button>
          </>}
        </section>
      </div>}
      <div className="package-body">
        <iframe ref={(el) => { frame.current = el; ui.part("frame")(el); }} className="package-frame"
                title={info.name} src={view ? packageFileUrl(info, view.entry) : entryUrl(info)}
                data-view={view?.id} sandbox="allow-scripts" />
      </div>
      {(listening || said) && <p className="package-status" role="status">
        {listening ? `${t("main.package.listening")} ` : ""}
        {said && <SaidText said={said} className="package-said" />}
      </p>}
      {focus}
    </div>
  );
}

export interface Fading {
  faded: boolean;
  props: {
    onPointerEnter(): void; onPointerMove(): void; onPointerLeave(): void;
    onFocus(): void; onBlur(e: FocusEvent<HTMLElement>): void;
  };
}

/** The border's fading: shown at first, faded (dimmed, never gone: it
 *  stays where it is and every control in it still takes his click) once
 *  the pointer has been away from it for BAR_FADES_AFTER_MS, back as soon
 *  as the pointer is over it or keyboard focus is in it. Never faded with
 *  reduced motion or Steady. */
function useFadingBar(active: boolean): Fading {
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
    <button type="button" className="package-icon package-done" data-ui-action="domain"
            disabled={busy} ref={target} title={t("main.package.doneTitle")} aria-label={t("main.package.done")}
            onClick={() => {
              setBusy(true);
              void signOff(id).then((why) => { setBusy(false); onSaid(why); });
            }}>
      <PackageIcon name="done"/></button>
  );
}

type VotePart = "vote" | "approve" | "reject" | "words" | "send";

/** The standard vote: Approve records at once; Reject opens a field for
 *  his words saying what to change, recorded with Send (never without
 *  them). Each goes through the inbox's answer route (answerInbox), which
 *  takes only his click. An answer given before Got it was retired is only
 *  read back. In the vote row, or in the focus card (`lead`: the question
 *  itself above it) when the card steps to it; one answer for both. */
function VoteControls({ id, vote, now, onAnswer, onSaid, part, lead = false }: {
  id: string; vote: PageVote; now: { answer: PageVote["answer"]; note: string };
  onAnswer: (now: { answer: PageVote["answer"]; note: string }) => void; onSaid: (s: Said | null) => void;
  part: (name: VotePart) => (el: Element | null) => void; lead?: boolean }) {
  const t = useT();
  const answer = now.answer;
  const [asking, setAsking] = useState(false);
  const [words, setWords] = useState(now.note);
  const [busy, setBusy] = useState(false);
  const current = vote.question === STANDARD_VOTE;
  const record = async (option: "approve" | "reject", note = "") => {
    setBusy(true);
    const problem = await answerInbox(id, vote.question ?? STANDARD_VOTE, option, note);
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
      {lead && <h3 className="package-ask-question">{vote.words || t("main.package.vote.label")}</h3>}
      {lead && vote.reason && (
        <p className="package-ask-why"><strong>{t("main.package.focus.why")}</strong> {vote.reason}</p>
      )}
      {current && (
        <>
          <button type="button" className="settings-btn" data-ui-action="domain" disabled={busy}
                  aria-pressed={answer === "approve"} ref={part("approve")}
                  onClick={() => void record("approve")}>
            {t("main.package.vote.approve")}</button>
          <button type="button" className="settings-btn" data-ui-action="domain" disabled={busy}
                  aria-pressed={answer === "reject"} aria-expanded={asking} ref={part("reject")}
                  onClick={() => setAsking(!asking)}>
            {t("main.package.vote.reject")}</button>
        </>
      )}
      {asking && (
        <>
          <input type="text" className="package-vote-words" data-ui-action="input" value={words}
                 placeholder={t("main.package.vote.words")} aria-label={t("main.package.vote.words")}
                 ref={part("words")} maxLength={500} onChange={(e) => setWords(e.target.value)} />
          <button type="button" className="settings-btn primary" data-ui-action="domain"
                  disabled={busy || !words.trim()} ref={part("send")}
                  onClick={() => void record("reject", words.trim())}>
            {t("main.package.vote.send")}</button>
        </>
      )}
      {!asking && answer && (
        <span className="package-vote-recorded" role="status">
          {answer === "approve" ? t("main.package.vote.approveRecorded")
            : answer === "reject" ? t("main.package.vote.rejectRecorded")
            : t("main.package.vote.recordedBefore", { answer: vote.answer_name ?? answer })}
        </span>
      )}
    </div>
  );
}

type Questions = ReturnType<typeof usePackageQuestions>;

/** The questions a package's story declares, and his answers: one record
 *  for every card. Each answer goes through the inbox's one answer route
 *  (answerInbox), as his voice records it too; the server is asked again
 *  after each, since a follow-up may apply once its parent is answered. */
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

/** One question in its card: its words, why it is his and the evidence,
 *  its options as buttons (his click records it at once, with his words if
 *  he typed any) and what is recorded. */
/** Revisions as he reads them: each one's first twelve characters, a view
 *  set's joined with " + ". */
function shortRevisions(list?: (string | null)[] | null, one?: string | null): string {
  return (list?.length ? list : [one ?? ""]).map((r) => (r ?? "").slice(0, 12)).join(" + ");
}

function QuestionItem({ q, questions }: { q: HeaderQuestion; questions: Questions }) {
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
          {q.revisions?.length
            ? t("main.package.approval.headSet", { views: q.revisions.length, revision: shortRevisions(q.revisions) })
            : t("main.package.approval.head", { revision: (q.revision ?? "").slice(0, 12) })}
        </p>
      )}
      <h3 className="package-ask-question">{q.question}</h3>
      {q.reason && (
        <p className="package-ask-why"><strong>{t("main.package.focus.why")}</strong> {q.reason}</p>
      )}
      {q.context && (
        <p className="package-ask-context"><strong>{t("main.package.focus.evidence")}</strong> {q.context}</p>
      )}
      <div className="package-ask-options">
        {q.options.map((o) => {
          const needsWords = q.note_required?.includes(o.id) ?? false;
          return (
            <button key={o.id} type="button" className="settings-btn" data-ui-action="domain"
                    data-option={o.id} data-recommended={q.recommended === o.id ? "true" : undefined}
                    disabled={busy || (needsWords && !typed)}
                    aria-pressed={q.answer === o.id && !q.stale}
                    title={needsWords && !typed ? t("main.package.approval.needsWords") : o.says}
                    onClick={() => void record(q, o.id)}>
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
          {t("main.package.approval.stale", { revision: shortRevisions(q.answered_revisions, q.answered_revision) })}
        </span>
      )}
      {q.answer && !q.stale && (
        <span className="package-vote-recorded" role="status">
          {approval
            ? t(q.answer === "approve" ? "main.package.approval.approved" : "main.package.approval.changes",
                { revision: shortRevisions(q.answered_revisions, q.answered_revision) })
            : t("main.package.asks.recorded", {
                choice: q.options.find((o) => o.id === q.answer)?.name ?? q.answer })}
        </span>
      )}
    </div>
  );
}

/** The author's explanation (UNDERSTANDING.md), read in its card: fetched
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
