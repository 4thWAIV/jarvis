/**
 * The microphone control, at the lower right of the Conversation's message
 * input (Composer.tsx), right of Send, or, while the message input is not
 * on screen, in the same corner of the centre area (MicCorner): one
 * control, drawn where it is (MicPortal): the level meter (a red
 * "Muted", "Muted · microphone inactive" or "Mic off" in its place while
 * nothing is sent, "Finishing" while the words from before a stop are
 * finished, and Start listening while this page is not listening), mute,
 * mic off, drawn as the headset, the whole audio subsystem at a glance
 * (AudioHeadset.tsx); the speaker, what JARVIS says (SpeakerButton.tsx); and, in its own kept slot left of Stop (MicHealth), the health
 * dot while voice input has a problem (loading, blocked, busy in
 * another tab, interrupted, unavailable, or the recogniser falling behind).
 * The words are in the dot's title and in the Diagnostics view's Messages,
 * which clicking the dot opens; nothing is written under the orb.
 *
 * While a workflow or a page package has taken over the page, all of it,
 * Stop included, is drawn in the takeover bar (TakeoverBar.tsx) instead:
 * moved, never copied, so there is one of each wherever it is.
 *
 * The dot runs the registered `voice.diagnostics.open`, as the brain's
 * request does. The whole control and its meter, dot, mute and off are a
 * target the brain can point at; mute and off stay his own switches, and
 * Start listening is his alone: nothing else starts it (mic.ts), and it
 * waits until his voice input settings have been read.
 */

import { createContext, useContext, useLayoutEffect, useRef, useSyncExternalStore, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CENTRE_SOCKET } from "./ConversationView";
import { codeWords, saidShown, useT, type Translate } from "./locale";
import { inputStatus, quiet, startListening, voiceInputSettings } from "./mic";
import { createStore, type Store } from "./store";
import { MicMeter } from "./MicMeter";
import { MicOffButton } from "./MicOffButton";
import { MuteButton } from "./MuteButton";
import { SpeakerButton } from "./SpeakerButton";
import { StopControl } from "./StopControl";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { causeSaid, health, type Cause } from "./voiceHealth";
import type { InputStatus } from "./voiceInput";

const QUIET_STATES = new Set(["listening", "paused", "muted", "muted-inactive", "off", "finishing"]);
const LOST_STATES = new Set(["interrupted", "unavailable", "permission"]);

/** What the dot says, in the language in force, and whether words were
 *  lost, or null when all is well. */
export function inputProblem(t: Translate, status: InputStatus, h: ({ state: string; cause: string } & Cause) | null):
    { text: string; bad: boolean } | null {
  if (!QUIET_STATES.has(status.state) && status.words) {
    return { text: status.words.say(t), bad: LOST_STATES.has(status.state) };
  }
  if (h && h.state !== "healthy") {
    // healthWords' words, by a state that may be one this page does not know.
    const words = codeWords("main.voice.health", h.state) ?? h.state;
    return { text: h.cause ? t("left.mic.healthCause", { state: words, cause: saidShown(causeSaid(h)).text }) : words,
             bad: h.state === "no-frames" };
  }
  return null;
}

type PartRef = (el: Element | null) => void;

function HealthDot({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const status = useSyncExternalStore(inputStatus.subscribe, inputStatus.get);
  const h = useSyncExternalStore(health.subscribe, health.get);
  const problem = inputProblem(t, status, h);
  if (!problem) return null;
  return (
    <button id="health-dot" type="button" className={`health-dot${problem.bad ? " bad" : ""}`}
            data-state={status.state} title={problem.text} aria-label={problem.text} ref={partRef}
            {...bindingProps(bind("voice.diagnostics.open", {}))} />
  );
}

/** States in which this page is not listening and Start listening is offered. */
const STARTABLE = new Set(["disarmed", "busy"]);

/** What stands in for the meter while nothing is sent, by state, or null
 *  for the meter itself. */
function silencedKey(state: string): "left.mic.off" | "left.mic.muted" | "left.mic.mutedInactive"
                                     | "left.mic.finishing" | null {
  if (state === "off") return "left.mic.off";
  if (state === "muted") return "left.mic.muted";
  if (state === "muted-inactive") return "left.mic.mutedInactive";
  if (state === "finishing") return "left.mic.finishing";
  return null;
}

/** Start listening on this page: his click, and nothing else. */
function StartButton({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const settings = useSyncExternalStore(voiceInputSettings.subscribe, voiceInputSettings.get);
  const status = useSyncExternalStore(inputStatus.subscribe, inputStatus.get);
  const label = t(status.state === "busy" ? "left.mic.listenHere" : "left.mic.startListening");
  return (
    <button id="btn-listen" type="button" ref={partRef} data-ui-action="microphone"
            className="control-button small listen" disabled={!settings}
            title={settings ? label : t("left.mic.settingsUnread")} aria-label={label}
            onClick={(e) => { e.stopPropagation(); startListening.current(); }}>
      {label}
    </button>
  );
}

/** The meter, or what stands in for it while nothing is sent. */
function MicLevel({ partRef, listenRef }: { partRef: PartRef; listenRef: PartRef }) {
  const t = useT();
  const preference = useSyncExternalStore(quiet.subscribe, quiet.get);
  const status = useSyncExternalStore(inputStatus.subscribe, inputStatus.get);
  if (preference === "none" && STARTABLE.has(status.state)) return <StartButton partRef={listenRef} />;
  const key = preference === "off" ? "left.mic.off" : silencedKey(status.state)
    ?? (preference === "muted" ? "left.mic.muted" : null);
  if (!key) return <MicMeter partRef={partRef} />;
  return <span id="mic-silenced" data-state={status.state} ref={partRef}>{t(key)}</span>;
}

type MicTarget = ReturnType<typeof useUiTarget>;
const MicTargetContext = createContext<MicTarget | null>(null);

function useMicTarget(): MicTarget {
  const ui = useContext(MicTargetContext);
  if (!ui) throw new Error("the microphone's parts are drawn inside MicTarget");
  return ui;
}

/** Where the microphone is drawn: the takeover bar while a workflow or a
 *  page package has taken over, else the message input's lower right while
 *  the Conversation view is on screen, else the same corner of the centre
 *  area (MicCorner). Each place gives a host for the health dot and one for
 *  the rest; the message input and the bar give one for Stop too. */
type Place = "composer" | "corner" | "bar";
type Hosts = { health: Element | null; stop: Element | null; mic: Element | null };
const NO_HOSTS: Hosts = { health: null, stop: null, mic: null };
const hosts: Record<Place, Store<Hosts>> = {
  composer: createStore<Hosts>(NO_HOSTS),
  corner: createStore<Hosts>(NO_HOSTS),
  bar: createStore<Hosts>(NO_HOSTS),
};
// One callback per host, made once, so a re-render of what holds it never
// hands React a new ref (which would clear and set the host, and move the
// control out and back).
const setHost = (place: Place, which: keyof Hosts) => (el: Element | null) => {
  const now = hosts[place].get();
  if (now[which] !== el) hosts[place].set({ ...now, [which]: el });
};
const refsOf = (place: Place): Record<keyof Hosts, (el: Element | null) => void> =>
  ({ health: setHost(place, "health"), stop: setHost(place, "stop"), mic: setHost(place, "mic") });
const HOST_REFS: Record<Place, Record<keyof Hosts, (el: Element | null) => void>> =
  { composer: refsOf("composer"), corner: refsOf("corner"), bar: refsOf("bar") };
const hostRef = (place: Place, which: keyof Hosts) => HOST_REFS[place][which];
type MicView = "main.conversation" | "main.corner" | "main.takeover";
const VIEW: Record<Place, MicView> = { composer: "main.conversation", corner: "main.corner", bar: "main.takeover" };

/** A host for the microphone's health dot, Stop or the rest of it, in the
 *  message input or the takeover bar; it takes no box of its own. */
export function MicHost({ which, place = "composer" }: { which: keyof Hosts; place?: "composer" | "bar" }) {
  return <span className="mic-host" ref={hostRef(place, which)} />;
}

// The corner box's distance from the footer's gap, and the gap kept between
// it and the view above (style.css #centre-mic).
const CORNER_GAPS_PX = 12;

/** The centre area's lower-right corner, holding the microphone while the
 *  message input is not on screen (any centre view but Conversation). It
 *  sets --centre-corner-height to the room it takes, so every centre view
 *  stops above it and it covers nothing. */
export function MicCorner() {
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const root = document.documentElement.style;
    const fit = () => {
      if (el.offsetHeight > 0) root.setProperty("--centre-corner-height", `${el.offsetHeight + CORNER_GAPS_PX}px`);
      else root.removeProperty("--centre-corner-height");
    };
    fit();
    const watch = new ResizeObserver(fit);
    watch.observe(el);
    return () => {
      watch.disconnect();
      root.removeProperty("--centre-corner-height");
    };
  }, []);
  return <div id="centre-mic" ref={box}><span className="mic-host" ref={hostRef("corner", "health")} />
    <span className="mic-host" ref={hostRef("corner", "mic")} /></div>;
}

/** The one microphone control and its one target, drawn into the bar
 *  while it is shown, else the corner while it is shown, else the message
 *  input; and Stop, in the bar while it is shown, else the message input. */
export function MicPortal() {
  const bar = useSyncExternalStore(hosts.bar.subscribe, hosts.bar.get);
  const corner = useSyncExternalStore(hosts.corner.subscribe, hosts.corner.get);
  const composer = useSyncExternalStore(hosts.composer.subscribe, hosts.composer.get);
  const shown = (h: Hosts) => !!(h.health && h.mic);
  const place: Place = shown(bar) ? "bar" : shown(corner) ? "corner" : "composer";
  const at = { bar, corner, composer }[place];
  const stopPlace: Place = bar.stop ? "bar" : "composer";
  const stopAt = (stopPlace === "bar" ? bar : composer).stop;
  return <>
    {at.health && at.mic && <MicTarget key={place} view={VIEW[place]}>
      {createPortal(<MicHealth />, at.health)}
      {createPortal(<MicControl />, at.mic)}
    </MicTarget>}
    {stopAt && createPortal(<StopControl key={stopPlace} view={stopPlace === "bar" ? "main.takeover" : "main.conversation"} />,
                            stopAt)}
  </>;
}

/** The microphone's one target, shared by its two parts' places: the
 *  health dot (MicHealth), left of Stop, and the rest (MicControl), right
 *  of Send, or both in the centre's corner or the takeover bar. */
function MicTarget({ view, children }: { view: MicView; children: ReactNode }) {
  const t = useT();
  const ui = useUiTarget({ ref: { region: "main", view, kind: "mic" }, label: t("targets.microphone"),
                           socket: CENTRE_SOCKET });
  return <MicTargetContext.Provider value={ui}>{children}</MicTargetContext.Provider>;
}

/** The health dot's slot: kept at the dot's width while there is no
 *  problem, so nothing beside it moves when the dot comes or goes. */
function MicHealth() {
  const ui = useMicTarget();
  return <span id="health-slot"><HealthDot partRef={ui.part("health")} /></span>;
}

function MicControl() {
  const t = useT();
  const ui = useMicTarget();
  const status = useSyncExternalStore(inputStatus.subscribe, inputStatus.get);
  // The state as assistive technology reads it: beside the control, not in
  // it, so its (hidden) words are not something the pointer must avoid.
  return (<>
    <span id="mic-state" className="visually-hidden" role="status">{t(`left.mic.state.${status.state}`)}</span>
    <div id="mic-control" ref={ui.whole}>
      <MicLevel partRef={ui.part("meter")} listenRef={ui.part("listen")} />
      <MuteButton partRef={ui.part("mute")} />
      <MicOffButton partRef={ui.part("off")} />
      <SpeakerButton partRef={ui.part("speaker")} />
    </div>
  </>);
}
