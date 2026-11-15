/** Compact frame for existing host actions. Selection never starts speech or writes an answer. */
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { useT, type Said } from "./locale";
import { storyRunning, storyState, tellStory, type PackageInfo } from "./packages";
import { PackageIcon } from "./PackageIcons";
import { TakeoverButton } from "./TakeoverBar";
import { bind, bindingProps } from "./uiNavigation";

type Part = "header" | "story" | "tell" | "stop" | "resume" | "fromSlide" | "takeover" | "close";
interface Props {
  info: PackageInfo;
  offered: boolean;
  done: ReactNode;
  onSaid(said: Said | null): void;
  part(name: Part): (el: Element | null) => void;
  presentation: boolean;
  command(type: "presentation.inspect" | "presentation.document" | "presentation.select", value?: number | boolean): void;
}
export function PackageBorder({ info, offered, done, onSaid, part, presentation, command }: Props) {
  const t = useT();
  const state = useSyncExternalStore(storyState.subscribe, storyState.get);
  const running = storyRunning(state, info.id);
  const mine = state?.package === info.id ? state : null;
  const status = mine?.state === "telling" ? t("main.package.story.telling", { slide: mine.slide, slides: mine.slides })
    : mine?.state === "waiting" ? t("main.package.story.waiting", { slide: mine.slide, slides: mine.slides })
      : mine?.state === "paused" ? t("main.package.story.paused", { slide: mine.slide, slides: mine.slides })
        : mine?.state === "finished" ? t("main.package.story.told")
          : mine?.state === "stopped" ? t("main.package.story.stopped") : "";
  const slides = info.story?.slides ?? [];
  const [slide, setSlide] = useState(1);
  const [busy, setBusy] = useState(false);
  const [menu, setMenu] = useState<"chapters" | "more" | null>(null);
  const [width, setWidth] = useState(800);
  const [linear, setLinear] = useState(false);
  const header = useRef<HTMLElement | null>(null);
  const dialog = useRef<HTMLElement | null>(null);
  const opener = useRef<HTMLElement | null>(null);
  useEffect(() => { setSlide(1); setMenu(null); setLinear(false); }, [info.id]);
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
  const select = (value: number) => { setSlide(value); if (presentation && !running) command("presentation.select", value); };
  const act = async (action: "tell" | "resume" | "stop") => {
    setBusy(true);
    try { onSaid(await tellStory(info.id, action, action === "tell" ? slide : undefined)); }
    finally { setBusy(false); }
  };
  const step = (direction: number) => select(Math.max(1, Math.min(slides.length, slide + direction)));
  const transport = running ? "stop" : "tell";
  const transportName = t(running ? "main.package.story.stop" : "main.package.story.tell");
  const title = info.communication?.title ?? info.story?.title ?? info.name;
  return <>
    <section className="package-head package-border" ref={el => { header.current = el; part("header")(el); }}>
      {info.story && <div className="package-story" ref={part("story")} aria-label={t("main.package.story.label")}>
        <button className="package-icon" type="button" title={t("main.package.chapters")} aria-label={t("main.package.chapters")} data-package-menu="chapters" aria-expanded={menu === "chapters"} onClick={() => open("chapters")}><PackageIcon name="chapters"/></button>
        {width >= 420 && <button className="package-icon" type="button" title={t("main.package.previous")} aria-label={t("main.package.previous")} disabled={slide === 1} onClick={() => step(-1)}><PackageIcon name="previous"/></button>}
        <button className="package-icon" type="button" data-ui-action="domain" data-story-action={transport} ref={part(transport)} title={transportName} aria-label={transportName} disabled={busy} onClick={() => void act(transport)}><PackageIcon name={transport}/></button>
        {width >= 420 && <button className="package-icon" type="button" title={t("main.package.next")} aria-label={t("main.package.next")} disabled={slide === slides.length} onClick={() => step(1)}><PackageIcon name="next"/></button>}
      </div>}
      <span className="package-story-now visually-hidden" role="status">{status}</span>
      <h2 className="package-frame-title" title={`${title}. ${info.purpose}`}>{title}</h2>
      {info.story && width >= 500 && <span className="package-position" aria-label={t("main.package.story.fromSlide")}>{slide} / {slides.length}</span>}
      <button className="package-icon" type="button" title={t("main.package.more")} aria-label={t("main.package.more")} data-package-menu="more" aria-expanded={menu === "more"} onClick={() => open("more")}><PackageIcon name="more"/></button>
      {done}
      {width >= 600 && <TakeoverButton partRef={part("takeover")} lit={offered} icon={<PackageIcon name="takeover"/>}/>}
      <button type="button" className="package-icon workflow-close" title={t("main.common.close")} aria-label={t("main.common.close")} ref={part("close")} {...bindingProps(bind("package.close", {}))}><PackageIcon name="close"/></button>
    </section>
    {menu && <section className="package-border-overlay" role="dialog" aria-modal="true" aria-label={t(menu === "chapters" ? "main.package.chapters" : "main.package.more")} ref={dialog} onKeyDown={event => {
      if (event.key === "Escape") { event.preventDefault(); setMenu(null); }
      if (event.key === "Tab") {
        const controls = [...dialog.current!.querySelectorAll<HTMLElement>("button:not(:disabled), select, a[href], input")];
        if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls[controls.length - 1]?.focus(); }
        else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) { event.preventDefault(); controls[0]?.focus(); }
      }
    }}>
      <button type="button" className="folder-button" onClick={() => setMenu(null)}>{t("main.package.return")}</button>
      <h2>{menu === "chapters" ? t("main.package.chapters") : title}</h2>
      {menu === "chapters" && <>
        <label>{t("main.package.story.fromSlide")} <select data-ui-action="input" value={slide} ref={part("fromSlide")} onChange={e => select(Number(e.target.value))}>{slides.map((name, index) => <option key={index} value={index + 1}>{index + 1}. {name}</option>)}</select></label>
        <div className="package-chapters">{slides.map((name, index) => <button type="button" key={index} aria-current={slide === index + 1 ? "step" : undefined} onClick={() => { select(index + 1); setMenu(null); }}>{index + 1}. {name}</button>)}</div>
      </>}
      {menu === "more" && <>
        <p>{info.purpose}</p>
        {status && <p>{status}</p>}
        {info.story && <p>{t("main.package.story.fromSlide")} {slide} / {slides.length}: {slides[slide - 1]}</p>}
        {!running && !!info.story?.asks?.length && <button type="button" className="folder-button" data-ui-action="domain" ref={part("resume")} disabled={busy} onClick={() => { setMenu(null); void act("resume"); }}>{t("main.package.story.continue")}</button>}
        {presentation && <>
          <button type="button" data-presentation-action="inspect" className="folder-button" onClick={() => { setMenu(null); command("presentation.inspect"); }}>{t("main.package.details")}</button>
          <button type="button" data-presentation-action="document" className="folder-button" aria-pressed={linear} onClick={() => { setLinear(!linear); command("presentation.document", !linear); setMenu(null); }}>{t("main.package.documentView")}</button>
        </>}
        {width < 600 && <TakeoverButton partRef={part("takeover")} lit={offered}/>}
        {offered && <p role="status">{t("main.package.wide")}</p>}
      </>}
    </section>}
  </>;
}
