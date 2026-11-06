import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { conversation, reading, turnKey, type Turn } from "../captions";
import { type ConversationItem, type ConversationPage } from "../conversation";
import { useT } from "../locale";
import { bind, bindingProps } from "../uiNavigation";
import { REVEALING } from "../uiNavigationReveal";
import { dispatch } from "../uiRegistry";
import { conversationJump } from "../voiceStatus";

/** `spoken`: for a record of JARVIS's, the live turn it was, which may
 *  still be being read aloud. */
export type ConversationRow = { at: number; key: string; turn?: Turn; item?: ConversationItem;
  answer?: ConversationItem; restorable?: boolean; spoken?: Turn };
const words = (s: string) => s.replace(/\s+/g, " ").trim();

/** Durable records are authoritative. Live turns bridge only the interval
 * before their record arrives; never discard new records by page-open time. */
export function historyRows(turns: Turn[], items: ConversationItem[], _everything = false): ConversationRow[] {
  const matched = new Map<number, Turn>();
  const pending = turns.filter(t => {
    if (t.display) return true;
    const record = items.find(i => !matched.has(i.id) && (
      (t.speaker === "jarvis" && i.kind === "jarvis" && t.utt != null && i.detail?.utt === t.utt
        && (i.detail.reply_started_at != null ? i.detail.reply_started_at === t.at : Math.abs(i.at - (t.at ?? 0)) < 30))
      || (t.speaker === "user" && (i.kind === "heard" || i.kind === "typed") && i.detail?.client_turn_id === t.id && Math.abs(i.at - (t.at ?? 0)) < 30
        && words(i.text) === words(t.sentences.join(" ")))
      || (t.capture && i.kind === "capture" && i.capture_id === t.capture.id)
      || ((t.speaker === "jarvis" ? i.kind === "jarvis" : i.kind === (t.typed ? "typed" : "heard"))
        && Math.abs(i.at - (t.at ?? 0)) < 30 && words(i.text) === words(t.text ?? t.sentences.join(" ")))
    ));
    if (record) matched.set(record.id, t);
    return !record;
  });
  const asked = new Set(items.filter(i => i.kind === "to_agent").map(i => i.message_id));
  const answers = new Map(items.filter(i => i.kind === "answer" && i.message_id != null).map(i => [i.message_id, i]));
  const rows: ConversationRow[] = [
    ...pending.map(turn => ({ at: turn.at ?? 0, key: turnKey(turn), turn })),
    ...items.filter(i => !(i.kind === "answer" && asked.has(i.message_id)))
      .map(item => ({ at: item.at, key: `r${item.id}`, item,
        spoken: item.kind === "jarvis" ? matched.get(item.id) : undefined,
        answer: item.kind === "to_agent" ? answers.get(item.message_id) : undefined,
        restorable: item.detail?.capture_event === "cleared" && !items.some(j => j.id > item.id
          && j.capture_id === item.capture_id && j.detail?.capture_event === "restored") })),
  ];
  return rows.sort((a, b) => a.at - b.at);
}

function merge(a: ConversationItem[], b: ConversationItem[]) {
  return [...new Map([...a, ...b].map(i => [i.id, i])).values()].sort((a, b) => a.id - b.id);
}

// Keys that move a scrolled box, and of them those that move it up.
const SCROLL_KEYS = new Set(["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]);
const UP_KEYS = new Set(["ArrowUp", "PageUp", "Home"]);
// A scroll this soon after his wheel, touch or key is his.
const HIS_MS = 400;
// Back this near the bottom by his own scrolling, the list follows again.
const AT_BOTTOM = 4;
// While a reply is read aloud, the word being said is kept between this
// far from the box's top (px) and this far down it (of its height).
const READ_TOP = 8;
const READ_LINE = 0.75;

/** Where the list follows to: the bottom, or, while a reply is read aloud
 *  (a word marked `.spoken-word.now` by SpokenWords.tsx), wherever keeps
 *  that word in view. Still at the bottom (`pinned`) it stays there while
 *  the word would be in view there, so a reply that fits is followed as
 *  before. Otherwise it moves only as the word needs: down once the word
 *  passes the reading line, up only as far as the word when it is above
 *  the box, and not at all while it is in view, so words arriving or the
 *  box changing size never carry the list past it. Between sentences no
 *  word is marked, but the reading has not ended (`stillReading`,
 *  captions.ts `reading`): it stays where it is. */
function followTo(box: HTMLElement, content: HTMLElement, pinned: boolean, stillReading: boolean): number {
  const end = box.scrollHeight - box.clientHeight;
  const word = content.querySelector(".spoken-word.now");
  if (!word) return stillReading && !pinned ? box.scrollTop : end;
  const at = word.getBoundingClientRect();
  const top = at.top - box.getBoundingClientRect().top;
  const bottom = top + at.height;
  if (pinned && top - (end - box.scrollTop) >= READ_TOP) return end;
  if (top < READ_TOP) return box.scrollTop + top - READ_TOP;
  const line = box.clientHeight * READ_LINE;
  return bottom > line ? box.scrollTop + bottom - line : box.scrollTop;
}

/**
 * The one owner of where the history is scrolled. It follows the newest
 * words, whatever makes the list grow or the box shrink (a reply being
 * written, the bottom box growing, a capture opening); nothing else scrolls
 * it. While a reply is read aloud, following means keeping the word being
 * read in view (`followTo`), so a reply taller than the box is not passed
 * by; once the reading ends, is skipped or stopped, it settles at the
 * bottom. Only he stops it: a wheel, touch, key or scrollbar drag that takes the
 * list up from the bottom. A scroll the page made, or one the browser made
 * when the content or the box changed size, never counts, since a scroll
 * event says nothing of who moved it. A record brought into view on
 * purpose (REVEALING) counts as his. It follows again when he scrolls back
 * to the bottom, or on Jump to latest (`jump`); `pause` is his asking for
 * earlier pages.
 */
function useFollow(changed: unknown[], stillReading: boolean) {
  const [following, setFollowing] = useState(true);
  const follow = useRef(true);
  const his = useRef({ until: 0, holding: false });
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const set = (on: boolean) => {
    if (follow.current === on) return;
    follow.current = on;
    setFollowing(on);
  };
  // Read by `stick`, which the observers call too.
  const live = useRef(stillReading);
  live.current = stillReading;
  // Whether the list was left at the bottom when it last followed.
  const pinned = useRef(true);
  // Not while he holds the scrollbar or the list: it is his to move.
  const stick = () => {
    const box = scroll.current;
    if (!box || !content.current || !follow.current || his.current.holding) return;
    box.scrollTop = followTo(box, content.current, pinned.current, live.current);
    pinned.current = box.scrollHeight - box.scrollTop - box.clientHeight < AT_BOTTOM;
  };
  useLayoutEffect(stick, changed);         // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const box = scroll.current;
    if (!box) return;
    const mark = () => { his.current.until = performance.now() + HIS_MS; };
    const up = () => { mark(); if (box.scrollHeight > box.clientHeight) set(false); };
    const wheel = (e: WheelEvent) => { if (e.deltaY < 0) up(); else mark(); };
    const key = (e: KeyboardEvent) => {
      if (!SCROLL_KEYS.has(e.key)) return;
      if (UP_KEYS.has(e.key) || (e.key === " " && e.shiftKey)) up(); else mark();
    };
    // On the box itself, not its content: the scrollbar.
    const grab = (e: PointerEvent) => { if (e.target === box) { his.current.holding = true; mark(); } };
    const touch = () => { his.current.holding = true; mark(); };
    const release = () => { if (his.current.holding) { his.current.holding = false; mark(); } };
    const moved = () => {
      if (!his.current.holding && performance.now() > his.current.until) return;
      set(box.scrollHeight - box.scrollTop - box.clientHeight < AT_BOTTOM);
    };
    const resize = new ResizeObserver(stick);
    resize.observe(box);
    if (content.current) resize.observe(content.current);
    box.addEventListener("wheel", wheel, { passive: true });
    box.addEventListener("keydown", key);
    box.addEventListener(REVEALING, up);
    box.addEventListener("pointerdown", grab);
    box.addEventListener("touchstart", touch, { passive: true });
    box.addEventListener("scroll", moved, { passive: true });
    window.addEventListener("pointerup", release);
    window.addEventListener("touchend", release);
    window.addEventListener("touchcancel", release);
    return () => {
      resize.disconnect();
      box.removeEventListener("wheel", wheel);
      box.removeEventListener("keydown", key);
      box.removeEventListener(REVEALING, up);
      box.removeEventListener("pointerdown", grab);
      box.removeEventListener("touchstart", touch);
      box.removeEventListener("scroll", moved);
      window.removeEventListener("pointerup", release);
      window.removeEventListener("touchend", release);
      window.removeEventListener("touchcancel", release);
    };
  }, []);                                  // eslint-disable-line react-hooks/exhaustive-deps
  return {
    following, scroll, content,
    jump() { set(true); stick(); },
    pause() { set(false); },
  };
}

export function MessageList({ turns, older, earlier, live, partRef, renderRow }: {
  turns: Turn[];
  older: ConversationItem[];
  earlier: number | null;
  /** The record as read; a failed read is the conversation control's to
   *  say (controlState.ts, History unavailable). */
  live: { value: ConversationPage | null; error: string | null; refresh: () => void };
  partRef: (part: string) => (el: Element | null) => void;
  renderRow: (row: ConversationRow) => ReactNode;
}) {
  const t = useT();
  const jumpSignal = useSyncExternalStore(conversationJump.subscribe, conversationJump.get);
  const [retained, setRetained] = useState<ConversationItem[]>([]);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [earlierError, setEarlierError] = useState(false);
  const items = merge(merge(older, retained), live.value?.items ?? []);
  const rows = historyRows(turns, items);
  // The word being read moves with it: the list keeps it in view, and
  // settles at the bottom once the reading has ended.
  const speaking = useSyncExternalStore(conversation.subscribe, () => conversation.get().speaking);
  const stillReading = useSyncExternalStore(reading.subscribe, reading.get) !== null;
  const { following, scroll, content, jump, pause } = useFollow([items.length, turns, speaking, stillReading], stillReading);
  useEffect(() => {
    if (live.value) setRetained(old => merge(old, live.value!.items));
  }, [live.value]);
  useEffect(() => { if (jumpSignal) jump(); }, [jumpSignal]);
  async function loadEarlier() {
    pause();
    setLoadingEarlier(true);
    setEarlierError(false);
    try {
      const outcome = await dispatch("conversation.earlier", {}, { actor: "human" });
      setEarlierError(outcome.status !== "completed");
    } finally {
      setLoadingEarlier(false);
    }
  }
  const history = partRef("history");
  return <div className="conversation-thread">
    <div className="conversation-history" role="log" aria-live="off"
      aria-label={t("main.conversation.messageHistory")} data-ui-action="scroll"
      ref={el => { scroll.current = el; history(el); }}>
      <div className="conversation-messages" ref={content}>
        {earlier != null && <div className="conversation-earlier"><button type="button" className="history-earlier"
          ref={partRef("earlier")} disabled={loadingEarlier}
          {...bindingProps(bind("conversation.earlier", {}))} onClick={loadEarlier}>
          {t(loadingEarlier ? "main.conversation.loading" : "main.conversation.loadEarlier")}</button></div>}
        {earlierError && <p className="read-failure" role="alert">{t("main.conversation.earlierFailed")}</p>}
        {!live.value && !live.error && rows.length === 0 && <p className="conversation-empty" role="status">
          {t("main.conversation.loading")}</p>}
        {live.value && rows.length === 0 && <div className="conversation-empty"><span className="empty-mark" aria-hidden="true">J</span>
          <h2>{t("main.conversation.emptyHeading")}</h2><p>{t("main.conversation.emptyHint")}</p></div>}
        {rows.map(renderRow)}
      </div>
    </div>
    {!following && <button type="button" className="conversation-jump" data-ui-action="scroll" onClick={jump}>
      {t("main.conversation.jumpLatest")} <span aria-hidden="true">↓</span></button>}
  </div>;
}
