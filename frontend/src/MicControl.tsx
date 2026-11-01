/**
 * The microphone control, directly below the orb: the level meter (a red
 * "Muted" or "Mic off" in its place while nothing is sent), mute, mic off, and
 * the health dot while voice input has a problem (loading, blocked, busy in
 * another tab, interrupted, unavailable, or the recogniser falling behind).
 * The words are in the dot's title and in the Diagnostics view's Messages,
 * which clicking the dot opens; nothing is written under the orb.
 *
 * The dot runs the registered `voice.diagnostics.open`, as the brain's
 * request does. The whole control and its meter, dot, mute and off are a
 * target the brain can point at; mute and off stay his own switches.
 */

import { useSyncExternalStore } from "react";
import { codeWords, useT, type Translate } from "./locale";
import { inputStatus, micMuted, micOff } from "./mic";
import { MicMeter } from "./MicMeter";
import { MicOffButton } from "./MicOffButton";
import { MuteButton } from "./MuteButton";
import { bind, bindingProps } from "./uiNavigation";
import { useUiTarget } from "./uiRegistry";
import { health } from "./voiceHealth";
import type { InputStatus } from "./voiceInput";

const QUIET_STATES = new Set(["listening", "paused", "muted", "off"]);
const LOST_STATES = new Set(["interrupted", "unavailable", "permission"]);

/** What the dot says, in the language in force, and whether words were
 *  lost, or null when all is well. */
export function inputProblem(t: Translate, status: InputStatus, h: { state: string; cause: string } | null):
    { text: string; bad: boolean } | null {
  if (!QUIET_STATES.has(status.state) && status.words) {
    return { text: status.words.say(t), bad: LOST_STATES.has(status.state) };
  }
  if (h && h.state !== "healthy") {
    // healthWords' words, by a state that may be one this page does not know.
    const words = codeWords("main.voice.health", h.state) ?? h.state;
    return { text: h.cause ? t("left.mic.healthCause", { state: words, cause: h.cause }) : words,
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

/** The meter, or what stands in for it while nothing is sent. */
function MicLevel({ partRef }: { partRef: PartRef }) {
  const t = useT();
  const muted = useSyncExternalStore(micMuted.subscribe, micMuted.get);
  const off = useSyncExternalStore(micOff.subscribe, micOff.get);
  if (!muted && !off) return <MicMeter partRef={partRef} />;
  return <span id="mic-silenced" ref={partRef}>{t(off ? "left.mic.off" : "left.mic.muted")}</span>;
}

export function MicControl() {
  const ui = useUiTarget({ ref: { region: "left", view: "left.fixed", kind: "mic" }, label: "Microphone",
                           socket: { rail: "rail.left", side: "left" } });
  return (
    <div id="mic-control" ref={ui.whole}>
      <MicLevel partRef={ui.part("meter")} />
      <MuteButton partRef={ui.part("mute")} />
      <MicOffButton partRef={ui.part("off")} />
      <HealthDot partRef={ui.part("health")} />
    </div>
  );
}
