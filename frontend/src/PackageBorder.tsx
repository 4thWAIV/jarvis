/** Compact frame for existing host actions. Selection never starts speech or writes an answer.
 *  For a page that reports its slides (a deck), the border is its one navigation, over the deck's own
 *  slides (`navigation`: every slide and every question its own, numbered as the deck numbers them):
 *  the count follows the slide the page shows (`shown`) and selecting a slide opens it there
 *  (`openSlide`) while no story is told; telling starts at the story slide that holds it
 *  (`storySlideOf`). Any other story page only takes the slide to tell from.
 *  For an architecture view set each chapter names the view it is told over and whether that view
 *  shows the element lit (`lit`); selecting one opens its view (`openSlide`).
 *  Ask keeps exactly where he is (`onAsk`, the view its frame reports) and pauses the story's words;
 *  Return, shown while a place is kept, goes back to the last one (story_teller.py). Both are his clicks
 *  on the server's one service, which the brain's tell_package_story reaches too.
 *  While the package has taken over, the border fades back without the pointer (`fade`, PackageView.tsx). */
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { useT, type Said } from "./locale";
import { mountPackageControls, navigation, packageAsks, storyRunning, storyState, storySlideOf, tellStory, type PackageInfo } from "./packages";
import { PackageIcon } from "./PackageIcons";
import { TakeoverButton } from "./TakeoverBar";
import { bind, bindingProps } from "./uiNavigation";
import { byHand } from "./uiRegistry";
import type { Fading } from "./PackageView";

type Part = "chapters" | "previous" | "next" | "more" | "inspect" | "document" | "return" | "header" | "story" | "tell" | "stop" | "resume" | "fromSlide" | "takeover" | "close" | "ask" | "goBack" | "asks";
interface Props {
  info: PackageInfo;
  offered: boolean;
  done: ReactNode;
  onSaid(said: Said | null): void;
  part(name: Part): (el: Element | null) => void;
  presentation: boolean;
  command(type: "presentation.inspect" | "presentation.document", value?: boolean): void;
  fade?: Fading;
  /** The slide the page says it shows (a deck's own numbering, else the story's), and how many
   *  times it has said so. */
  shown?: { slide: number; seq: number } | null;
  /** Open slide `slide` in the page; missing: the page does not report its slides. */
  openSlide?: ((slide: number) => void) | null;
  /** A view set's elements lit, by id: each chapter says whether its view shows them. */
  lit?: string[];
  /** His Ask: keep where he is (what the frame shows) and pause the words. */
  onAsk(): Promise<Said | null>;
  /** The package's own questions (a story's declared questions, his approval of a design): the icon that
   *  opens their cards, shown only when it asks some. The standard vote is asked at Done. */
  questions?: { label: string; pressed: boolean; open(): void } | null;
}
export function PackageBorder({ info, offered, done, onSaid, part, presentation, command, fade, shown, openSlide, lit = [], onAsk, questions }: Props) {
  const t = useT();
  const state = useSyncExternalStore(storyState.subscribe, storyState.get);
  const kept = useSyncExternalStore(packageAsks.subscribe, packageAsks.get).length;
  const running = storyRunning(state, info.id);
  const mine = state?.package === info.id ? state : null;
  const status = mine?.state === "telling" ? t("main.package.story.telling", { slide: mine.slide, slides: mine.slides })
    : mine?.state === "waiting" ? t("main.package.story.waiting", { slide: mine.slide, slides: mine.slides })
      : mine?.state === "paused" ? t("main.package.story.paused", { slide: mine.slide, slides: mine.slides })
        : mine?.state === "finished" ? t("main.package.story.told")
          : mine?.state === "stopped" ? t("main.package.story.stopped") : "";
  // A deck's own slides (every slide and every question one), else the story's.
  const pages = navigation(info);
  const slides = pages.map(p => p.title);
  // A view set: the view each chapter is told over, and the lit elements it shows.
  const viewOf = (index: number) => info.views?.find(v => v.id === pages[index]?.view) ?? null;
  const showsLit = (index: number) => lit.filter(id => viewOf(index)?.declarations.includes(id));
  const [slide, setSlide] = useState(1);
  const [busy, setBusy] = useState(false);
  const [menu, setMenu] = useState<"chapters" | "more" | null>(null);
  const [width, setWidth] = useState(800);
  const [linear, setLinear] = useState(false);
  const header = useRef<HTMLElement | null>(null);
  const dialog = useRef<HTMLElement | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => { setSlide(1); setMenu(null); setLinear(false); }, [info.id]);
  useEffect(() => { if (shown) setSlide(shown.slide); }, [shown?.seq]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!header.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(header.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!menu) return;
    const frame = document.querySelector<HTMLIFrameElement>("#package-view .package-frame");
    if (frame) frame.inert = true;
    header.current!.inert = true;
    dialog.current?.querySelector<HTMLElement>("button, select")?.focus();
    return () => { if (frame) frame.inert = false; if (header.current) header.current.inert = false; opener.current?.focus(); };
  }, [menu]);
  const open = (which: "chapters" | "more") => { opener.current = document.activeElement as HTMLElement; setMenu(which); };
  const select = (value: number) => { setSlide(value); if (!running) openSlide?.(value); };
  const act = async (action: "tell" | "resume" | "stop" | "ask" | "return") => {
    setBusy(true);
    try {
      if (action === "ask") { const why = await onAsk(); onSaid(why ?? { words: () => t("main.package.asked") }); }
      else onSaid(await tellStory(info.id, action, action === "tell" ? storySlideOf(info, slide) : undefined));
    }
    finally { setBusy(false); }
  };
  useLayoutEffect(() => mountPackageControls({
    packageId: info.id, slides: slides.length, presentation,
    menu: which => { if (which === "closed") setMenu(null); else open(which); },
    select: (value, closeMenu) => { select(value); if (closeMenu) setMenu(null); },
    inspect: () => { setMenu(null); command("presentation.inspect"); },
    document: enabled => { setLinear(enabled); command("presentation.document", enabled); setMenu(null); },
  }));
  const transport = running ? "stop" : "tell";
  const transportName = t(running ? "main.package.story.stop" : "main.package.story.tell");
  const title = info.communication?.title ?? info.story?.title ?? info.name;
  return <>
    <section className="package-head package-border" ref={el => { header.current = el; part("header")(el); }}
      data-faded={fade?.faded ? "true" : "false"} {...fade?.props}>
      {info.story && <div className="package-story" ref={part("story")} aria-label={t("main.package.story.label")}>
        <button className="package-icon" type="button" title={t("main.package.chapters")} aria-label={t("main.package.chapters")} data-package-menu="chapters" aria-expanded={menu === "chapters"} ref={part("chapters")} {...bindingProps(bind("package.menu", {show: "chapters"}))}><PackageIcon name="chapters"/></button>
        {width >= 420 && <button className="package-icon" type="button" title={t("main.package.previous")} aria-label={t("main.package.previous")} data-package-step="previous" disabled={slide === 1} ref={part("previous")} {...bindingProps(bind("package.slide", {slide: slide - 1}))}><PackageIcon name="previous"/></button>}
        <button className="package-icon" type="button" data-ui-action="domain" data-story-action={transport} ref={part(transport)} title={transportName} aria-label={transportName} disabled={busy} onClick={() => void act(transport)}><PackageIcon name={transport}/></button>
        {width >= 420 && <button className="package-icon" type="button" title={t("main.package.next")} aria-label={t("main.package.next")} data-package-step="next" disabled={slide === slides.length} ref={part("next")} {...bindingProps(bind("package.slide", {slide: slide + 1}))}><PackageIcon name="next"/></button>}
      </div>}
      {questions && <button className="package-icon package-questions-open" type="button" data-ui-action="domain" ref={part("asks")} title={questions.label} aria-label={questions.label} aria-pressed={questions.pressed} onClick={questions.open}><PackageIcon name="questions"/></button>}
      <button className="package-icon" type="button" data-ui-action="domain" data-story-action="ask" ref={part("ask")} title={t("main.package.ask")} aria-label={t("main.package.ask")} disabled={busy} onClick={() => void act("ask")}><PackageIcon name="ask"/></button>
      {kept > 0 && <button className="package-icon package-go-back" type="button" data-ui-action="domain" data-story-action="return" ref={part("goBack")} title={t("main.package.goBack", { count: kept })} aria-label={t("main.package.goBack", { count: kept })} disabled={busy} onClick={() => void act("return")}><PackageIcon name="goBack"/><span className="package-go-back-count" aria-hidden="true">{kept}</span></button>}
      <span className="package-story-now visually-hidden" role="status">{status}</span>
      <h2 className="package-frame-title" title={`${title}. ${info.purpose}`}>{title}</h2>
      {info.story && width >= 500 && <span className="package-position" aria-label={t("main.package.story.fromSlide")}>{slide} / {slides.length}</span>}
      {info.runs === "page" && info.layout_checked === false && <span className="package-unchecked" title={t("main.package.layoutUnchecked.why")}>{t("main.package.layoutUnchecked")}</span>}
      <button className="package-icon" type="button" title={t("main.package.more")} aria-label={t("main.package.more")} data-package-menu="more" aria-expanded={menu === "more"} ref={part("more")} {...bindingProps(bind("package.menu", {show: "more"}))}><PackageIcon name="more"/></button>
      {done}
      {width >= 600 && <TakeoverButton partRef={part("takeover")} lit={offered} icon={<PackageIcon name="takeover"/>}/>}
      <button type="button" className="package-icon workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")} ref={part("close")} {...bindingProps(bind("package.close", {}))}><PackageIcon name="close"/></button>
    </section>
    {menu && <section className="package-border-overlay" role="dialog" aria-modal="true" aria-label={t(menu === "chapters" ? "main.package.chapters" : "main.package.more")} ref={dialog} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); byHand("package.menu", {show: "closed"}); }
      if (event.key === "Tab") {
        const controls = [...dialog.current!.querySelectorAll<HTMLElement>("button:not(:disabled), select, a[href], input")];
        if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls[controls.length - 1]?.focus(); }
        else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) { event.preventDefault(); controls[0]?.focus(); }
      }
    }}>
      <button type="button" className="folder-button" ref={part("return")} {...bindingProps(bind("package.menu", {show: "closed"}))}>{t("main.package.return")}</button>
      <h2>{menu === "chapters" ? t("main.package.chapters") : title}</h2>
      {menu === "chapters" && <>
        <label>{t("main.package.story.fromSlide")} <select value={slide} ref={part("fromSlide")} onChange={e => byHand("package.slide", {slide: Number(e.target.value)})}>{slides.map((name, index) => <option key={index} value={index + 1}>{index + 1}. {name}{pages[index].asks ? ` (${t("main.package.asks")})` : ""}</option>)}</select></label>
        <div className="package-chapters">{slides.map((name, index) => <button type="button" key={index} data-asks={pages[index].asks ?? undefined} aria-current={slide === index + 1 ? "step" : undefined} {...bindingProps(bind("package.slide", {slide: index + 1, closeMenu: true}))}>{index + 1}. {name}{pages[index].asks && <span className="package-chapter-asks"> · {t("main.package.asks")}</span>}{viewOf(index) && <span className="package-chapter-view" data-view={viewOf(index)!.id}> · {viewOf(index)!.title}</span>}{showsLit(index).length > 0 && <span className="package-chapter-lit"> · {t("main.package.view.shows", { element: showsLit(index).join(", ") })}</span>}</button>)}</div>
      </>}
      {menu === "more" && <>
        <p>{info.purpose}</p>
        {info.story?.scenario && <p className="package-scenario">{t("main.package.scenario", { scenario: info.story.scenario })}</p>}
        {status && <p>{status}</p>}
        {info.story && <p>{t("main.package.story.fromSlide")} {slide} / {slides.length}: {slides[slide - 1]}</p>}
        {!running && !!info.story?.asks?.length && <button type="button" className="folder-button" data-ui-action="domain" ref={part("resume")} disabled={busy} onClick={() => { setMenu(null); void act("resume"); }}>{t("main.package.story.continue")}</button>}
        {presentation && <>
          <button type="button" data-presentation-action="inspect" className="folder-button" ref={part("inspect")} {...bindingProps(bind("package.inspect", {}))}>{t("main.package.details")}</button>
          <button type="button" data-presentation-action="document" className="folder-button" aria-pressed={linear} ref={part("document")} {...bindingProps(bind("package.document", {enabled: !linear}))}>{t("main.package.documentView")}</button>
        </>}
        {width < 600 && <TakeoverButton partRef={part("takeover")} lit={offered}/>}
        {offered && <p role="status">{t("main.package.wide")}</p>}
      </>}
    </section>}
  </>;
}
