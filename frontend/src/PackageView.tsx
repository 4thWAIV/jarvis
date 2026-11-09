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
 * Registered for pointing at as `packageView` (its head, its frame, Done,
 * Close, and the story's controls); Close runs `package.close`, as the
 * brain does.
 */

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { CENTRE_SOCKET } from "./ConversationView";
import { SaidText, useT, type Said } from "./locale";
import { connectBridge } from "./packageBridge";
import { answerInbox, signOff } from "./packageInbox";
import { entryUrl, packageFileUrl, storyRunning, storyState, tellStory, type PackageInfo, type PageVote } from "./packages";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { TakeoverButton } from "./TakeoverBar";

export function PackageView({ info }: { info: PackageInfo }) {
  const t = useT();
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [listening, setListening] = useState(false);
  // Made for the wider layout, or its page asked for room: Take over is lit.
  const [asked, setAsked] = useState(false);
  const offered = info.layout === "wide" || asked;
  const ui = useUiTarget({ ref: { region: "main", view: "main.package", kind: "packageView", key: info.id },
                           label: info.name, socket: CENTRE_SOCKET });
  useEffect(() => {
    if (!frame.current) return undefined;
    return connectBridge(frame.current, info.id, {
      onSaved: setSaid, onListening: setListening, onStoryMissing: setSaid, onLayoutRequest: () => setAsked(true),
    });
  }, [info.id]);
  return (
    <div id="package-view" data-package={info.id} ref={ui.whole}>
      <section className="package-head" ref={ui.part("header")}>
        <div className="package-head-words">
          <h2>{info.communication?.title ?? info.name}</h2>
          <p>{info.purpose}</p>
          {offered && <p className="package-wide" role="status">{t("main.package.wide")}</p>}
        </div>
        {info.vote && <VoteControls id={info.id} vote={info.vote} onSaid={setSaid} part={ui.part} />}
        <DoneButton id={info.id} onSaid={setSaid} target={ui.part("done")} />
        <TakeoverButton partRef={ui.part("takeover")} lit={offered} />
        <button type="button" className="workflow-close" title={t("main.common.close")}
                aria-label={t("main.common.close")} ref={ui.part("close")}
                {...bindingProps(bind("package.close", {}))}>×</button>
      </section>
      {info.communication?.kind === "architecture" && <section className="package-canonical" aria-label={t("main.package.canonical")}>
        <p>{t("main.package.canonicalState", { revision: info.communication.source_revision?.slice(0, 12) ?? "" })}</p>
        <a href={packageFileUrl(info, "source.yaml")} target="_blank" rel="noopener noreferrer" ref={ui.part("source")} data-ui-action="domain">{t("main.package.source")}</a>{" · "}
        <a href={packageFileUrl(info, "check.json")} target="_blank" rel="noopener noreferrer" ref={ui.part("check")} data-ui-action="domain">{t("main.package.check")}</a>
        {info.communication.readable && <> {" · "}
          <a href={packageFileUrl(info, "UNDERSTANDING.md")} target="_blank" rel="noopener noreferrer" ref={ui.part("readable")} data-ui-action="domain">{t("main.package.readable")}</a>
        </>}
      </section>}
      {info.story && <StoryControls info={info} onSaid={setSaid} part={ui.part} />}
      <iframe ref={(el) => { frame.current = el; ui.part("frame")(el); }} className="package-frame"
              title={info.name} src={entryUrl(info)} sandbox="allow-scripts" />
      <p className="package-status" role="status">
        {listening ? `${t("main.package.listening")} ` : ""}
        {said && <SaidText said={said} className="package-said" />}
      </p>
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
function VoteControls({ id, vote, onSaid, part }: {
  id: string; vote: PageVote; onSaid: (s: Said | null) => void;
  part: (name: VotePart) => (el: Element | null) => void }) {
  const t = useT();
  const [answer, setAnswer] = useState(vote.answer);
  const [asking, setAsking] = useState(false);
  const [words, setWords] = useState(vote.note);
  const [busy, setBusy] = useState(false);
  const record = async (option: "got-it" | "questions", note = "") => {
    setBusy(true);
    const problem = await answerInbox(id, vote.question, option, note);
    setBusy(false);
    if (problem) {
      onSaid({ original: problem });
      return;
    }
    setAnswer(option);
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
