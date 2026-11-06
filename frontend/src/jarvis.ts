/**
 * JARVIS — the voice page's behaviour.
 *
 * Wires together the orb visualization, WebSocket communication,
 * speech recognition, and audio playback into a single experience.
 * The page itself is rendered by React (App.tsx); this runs once it is on
 * screen, with the orb the page created.
 */

import type { Orb, OrbState } from "./orb";
import { createVoiceInput, createAudioPlayer } from "./voice";
import { createSocket } from "./ws";
import { jarvisPaused, jarvisQuiet, jarvisSpeaking, shown, userHeard, userTyped, jarvisText, jarvisReply,
         jarvisReading, jarvisReconnected, jarvisDisconnected } from "./captions";
import type { Display } from "./sources";
import { reconnected } from "./liveRead";
import { captureChanged, captureQuiet, captureSpeaking, loadCapture,
         type CaptureMessage } from "./capture";
import { brainsMayHaveChanged } from "./brains";
import { conversationChanged } from "./conversation";
import { chooseQuiet, inputStatus, micLevel, quiet, quietBy, startListening, voiceInputSettings } from "./mic";
import { loadVoiceInputSettings } from "./voiceSettings";
import { CLIENT_ID } from "./uiClient";
import { offerDraft } from "./conversation/Composer";
import { cueTest, sendTyped, skipReading, stopSpeaking, turnAction, voiceState, voiceConnected, voiceTurn,
         conversationJump } from "./voiceStatus";
import { CUE_NAMES, isPauseCue, PLAYBACK_FRAME, REPLY_FAILURES, REPLY_OUTCOMES, type CueName,
         type PlaybackReport, type ReplyOutcome } from "./voiceControl";
import { countVoiceMessage, notice } from "./voiceHealth";
import { startLocale } from "./locale";
import { startSessionsSocket } from "./sessionsSocket";
import { startUiClient } from "./uiClient";
import { showPackage, storyCue, storyState, type PackageInfo, type StoryCue,
         type StoryState } from "./packages";
import { showMember } from "./bundles";
import { BUNDLE_VIEW_FRAME } from "./bundleContract";

export function startJarvis(orb: Orb): void {
  // ---------------------------------------------------------------------------
  // State machine
  // ---------------------------------------------------------------------------

  type State = "idle" | "listening" | "thinking" | "speaking" | "compacting";
  let currentState: State = "idle";

  // ---------------------------------------------------------------------------
  // Init components
  // ---------------------------------------------------------------------------

  const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const WS_URL = `${wsProto}//${window.location.host}/ws/voice`;
  const socket = createSocket(WS_URL);
  startSessionsSocket();
  // The interface's language: the one in force on the server (locale.ts).
  startLocale();
  // The brain's UI requests come to this page on the same socket.
  const uiClient = startUiClient(socket);

  const audioPlayer = createAudioPlayer();
  orb.setAnalyser(audioPlayer.getAnalyser());

  let muteMicDuringSpeech = false;

  function transition(newState: State) {
    if (newState === currentState) return;
    currentState = newState;
    voiceState.set(newState);
    orb.setState(newState as OrbState);
    micFollowsState();
  }

  // Mute and mic off only stop the microphone; they have no say in the orb's
  // state.
  function micFollowsState() {
    if (quiet.get() !== "none") return;
    if (currentState === "speaking" && muteMicDuringSpeech) {
      voiceInput.pause();
    } else {
      voiceInput.resume();
    }
  }

  // ---------------------------------------------------------------------------
  // Voice input
  // ---------------------------------------------------------------------------

  // ── the capture cues ──────────────────────────────────────────────────────
  // Whether this page is listening now (the capture fact the start and
  // stop cues follow): a pause cue plays only while it is, never while
  // muted, off or stopped.
  let capturing = false;
  /** A cue, as his settings have it: silent with capture sounds or that
   *  cue off. */
  function playCue(name: CueName) {
    const settings = voiceInputSettings.get();
    if (settings?.sounds && settings.cues[name].on) {
      audioPlayer.cue(settings.cues[name].tone, settings.volume);
    }
  }
  /** Test: the cue once, its tone at the cue volume, on or off. His click
   *  may unlock audio for it; the brain's request (play_cue) cannot. */
  function testCue(name: CueName, clicked: boolean) {
    const settings = voiceInputSettings.get();
    if (settings) audioPlayer.cue(settings.cues[name].tone, settings.volume, clicked);
  }
  cueTest.current = (name) => testCue(name, true);

  // What the server has gathered from him and not yet committed as a turn
  // (its `turn` frames: only words it accepted as his, never echo). If the
  // voice socket drops it is not sent but kept as an unsent draft; a turn
  // the recogniser could not finish waits for his Retry, Send now or Keep
  // as draft (VoiceTurnBar.tsx), and kept as a draft it comes back here.
  const unsentText = () => voiceTurn.get()?.text ?? "";
  const keepUnsent = (text: string) => { if (text.trim()) offerDraft(text); voiceTurn.set(null); };
  socket.onMessage((msg) => {
    if (msg.type === "cue_test") {
      if ((CUE_NAMES as readonly unknown[]).includes(msg.cue)) testCue(msg.cue as CueName, false);
      return;
    }
    if (msg.type !== "turn") return;
    const turnId = typeof msg.turnId === "number" ? msg.turnId : null;
    const text = String(msg.text ?? "");
    if (msg.state === "gathering" || msg.state === "commit-pending" || msg.state === "stalled") {
      voiceTurn.set({ state: msg.state, turnId, text });
    } else if (msg.state === "pause") {
      // A pause cue the server gave this page, for this turn: shown, and
      // heard now or never (only while this page is listening).
      if (!isPauseCue(msg.stage)) return;
      const stage = msg.stage;
      voiceTurn.set({ state: "gathering", turnId, text, pause: stage });
      if (msg.sound === true && capturing) playCue(stage);
    } else if (msg.state === "draft") {
      keepUnsent(text);
    } else {
      voiceTurn.set(null);             // committed, or dropped (idle)
    }
  });
  socket.onClose(() => keepUnsent(unsentText()));
  const TURN_FRAMES = { send_now: "turn_send_now", retry: "turn_retry", keep_as_draft: "turn_keep_draft" } as const;
  turnAction.current = (action) => {
    if (!socket.isConnected()) return false;
    socket.send({ type: TURN_FRAMES[action] });
    return true;
  };
  // A listening page stops while the voice socket is down and resumes, in
  // this open page, once it is back (voiceInput.ts `connection`).
  socket.onClose(() => voiceInput.connection(false));
  socket.onOpen(() => voiceInput.connection(true));

  // Every word heard carries this page and the capture epoch it was heard
  // in (voice control contract, section 4).
  const voiceInput = createVoiceInput(
    (text: string, captureEpoch: number) => {
      // The server decides whether this is echo, a barge-in, or a new turn.
      // The turn's id goes with it, so a capture made from it can name it.
      const id = showHeard(text, true);
      socket.send({ type: "transcript", text, isFinal: true, clientId: CLIENT_ID, captureEpoch,
                    ...(id === null ? {} : { id }) });
    },
    (text: string, captureEpoch: number) => {
      showHeard(text, false);
      // He is talking again: the pause shown is over (the server, hearing
      // the same, cancels its cues).
      const turn = voiceTurn.get();
      if (turn?.pause) voiceTurn.set({ state: turn.state, turnId: turn.turnId, text: turn.text });
      socket.send({ type: "interim", text, clientId: CLIENT_ID, captureEpoch });
    },
    (event: string) => {
      // Mirror the microphone's lifecycle to the server log: a browser-side
      // failure the server cannot otherwise see, in a console nobody has open.
      socket.send({ type: "mic", text: event });
    },
    {
      // The meter (MicMeter.tsx) reads the same microphone stream that is
      // transcribed: if it moves when you speak, the words are being heard.
      onLevel: micLevel.set,
      // Shown as the health dot beside the meter (MicControl.tsx); the words
      // go to the Diagnostics view's Messages, not under the orb.
      onStatus: (status) => {
        inputStatus.set(status);
        if (status.message) notice("Input", status.message);
      },
      // A final heard while the voice socket is down would be dropped by
      // socket.send; it is reported as not sent instead, and not replayed.
      canDeliver: () => socket.isConnected(),
      // His two voice input settings (voiceSettings.ts): no listening until
      // they are read.
      settings: () => voiceInputSettings.get(),
      // Discarded under his stop setting: the server drops what it was
      // gathering from this page.
      onDiscard: (captureEpoch: number) => {
        voiceTurn.set(null);
        socket.send({ type: "turn_discard", clientId: CLIENT_ID, captureEpoch });
      },
      // Kept words that could not be finished, or finished after Mute: never
      // sent, an unsent draft.
      onDraft: offerDraft,
      // Muted: the server drops what it was gathering, and the words it had
      // become an unsent draft too.
      onMute: (captureEpoch: number) => {
        keepUnsent(unsentText());
        socket.send({ type: "turn_discard", clientId: CLIENT_ID, captureEpoch });
      },
      // Listening really started or stopped: its quiet cue, through the
      // same output as his speech, unless his capture sounds are off.
      onCapture: (now: boolean) => {
        capturing = now;
        playCue(now ? "start" : "stop");
      },
    }
  );


  // ── stopping him ──────────────────────────────────────────────────────────
  // Two different things, each a button and neither a spoken word (his voice
  // comes back through the microphone garbled, and a mis-hear that looked
  // like "stop" would cut him off at random; a keystroke cannot be misheard):
  //
  //   Skip the rest — the playback bar's Skip, and Escape: only the reading
  //   sounding now (a reply, a read-back, an announcement). Its text stays,
  //   its generation goes on, the readings after it still play.
  //   Stop speaking — the status control's Stop: all of his audio, now.
  //
  // Neither cancels a request or undoes anything a tool did.

  let readingUtt: number | null = null;
  let skippedReading: number | null = null;
  const skipped = new Set<number>();
  const finishedReplies = new Set<number>();
  const knownReplies = new Set<number>();
  function rememberSkipped(utt: number) {
    skipped.add(utt);
    if (skipped.size > 64) skipped.delete(skipped.values().next().value!);
    for (const key of chunkText.keys()) if (key.startsWith(`${utt}:`)) chunkText.delete(key);
    for (const key of chunkCue.keys()) if (key.startsWith(`${utt}:`)) chunkCue.delete(key);
  }
  function skipLocally(utt: number) {
    rememberSkipped(utt);
    jarvisReading(utt, "skipped");
    if (readingUtt === utt) {
      skippedReading = utt;
      readingUtt = null;
      jarvisQuiet(); captureQuiet(); storyCue.set(null);
      // A reply still being written goes back to thinking; anything else
      // has nothing more coming, so he is listening.
      transition(knownReplies.has(utt) && !finishedReplies.has(utt) ? "thinking" : "listening");
    }
    audioPlayer.cancelUtterance(utt);
  }
  function skip() {
    if (readingUtt === null) return;
    const target = readingUtt;
    skipLocally(target);
    socket.send({ type: "skip_to_end", utt: target });
    conversationJump.set(conversationJump.get() + 1);
  }
  function stopAll() {
    if (readingUtt !== null) jarvisReading(readingUtt, "stopped");
    audioPlayer.stop();
    jarvisQuiet();
    readingUtt = null; chunkText.clear();
    chunkCue.clear();
    storyCue.set(null);
  }
  function hush() {
    if (currentState !== "speaking") return;
    // Locally first: the round trip is real and silence should be instant.
    stopAll();
    // The turn he stopped also stops pointing: its UI requests are
    // withdrawn and every white signal cleared.
    uiClient.interrupt();
    socket.send({ type: "hush" });
    transition("listening");
  }

  stopSpeaking.current = hush;
  skipReading.current = skip;
  window.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Escape" && readingUtt !== null) { e.preventDefault(); skip(); }
  });

  // ── typing ─────────────────────────────────────────────────────────────
  // What Tim types into the white box goes the way a final transcript does,
  // marked typed: the server neither corrects it nor takes it for echo.
  sendTyped.current = (text: string) => {
    if (!socket.isConnected()) return false;
    const id = userTyped(text);
    socket.send({ type: "transcript", text, isFinal: true, typed: true, id });
    return true;
  };

  // ── captions ─────────────────────────────────────────────────────────
  // What the recogniser hears, while he talks. Not while JARVIS is speaking:
  // the microphone hears JARVIS too, and the box shows JARVIS's own words then.
  function showHeard(text: string, final: boolean): number | null {
    if (currentState === "speaking") return null;
    return userHeard(text, final);
  }

  // JARVIS's words, each sentence as it starts PLAYING rather than as it
  // arrives, so the captions keep pace with the voice and the highlight is
  // on the sentence being heard. A sentence reading a capture back lights
  // the capture's box instead, and is not added to the captions.
  const chunkText = new Map<string, { text: string; capture?: number }>();
  // A story beat's cue, by chunk (packages.ts): shown in the package's frame
  // as the beat's words START playing, and ended as its last words end.
  const chunkCue = new Map<string, StoryCue>();
  function report(utt: number, idx: number, outcome: PlaybackReport) {
    socket.send({ type: PLAYBACK_FRAME, utt, idx, outcome });
  }
  audioPlayer.onStarted((utt, idx, seconds) => {
    readingUtt = utt; skippedReading = null;
    report(utt, idx, "started");
    transition("speaking");
    const key = `${utt}:${idx}`;
    // Kept until the chunk is played: onPlayed ends the beat at its last chunk.
    const cue = chunkCue.get(key);
    if (cue) storyCue.set(cue);
    else storyCue.set(null);
    const chunk = chunkText.get(key);
    chunkText.delete(key);
    if (!chunk) return;
    if (chunk.capture !== undefined) {
      jarvisQuiet();
      captureSpeaking(chunk.capture, chunk.text, seconds);
    } else {
      captureQuiet();
      jarvisSpeaking(utt, chunk.text, seconds, idx);
    }
  });

  // The server's captures are the truth: read them again whenever the socket
  // (re)connects, so a restarted server's empty list clears the boxes.
  socket.onOpen(loadCapture);
  socket.onOpen(() => {
    // Scheduler ids belong to a connection epoch; a restarted backend reuses them.
    jarvisReconnected();
    skipped.clear(); finishedReplies.clear(); knownReplies.clear(); chunkText.clear(); chunkCue.clear();
    readingUtt = null; skippedReading = null;
    voiceConnected.set(true);
  });
  socket.onClose(() => {
    voiceConnected.set(false);
    audioPlayer.stop();
    jarvisDisconnected();
    captureQuiet(); transition("listening");
  });
  // Every live read of the server's state (liveRead.ts) reads again.
  socket.onOpen(() => reconnected.set(reconnected.get() + 1));

  audioPlayer.onPlayed((utt, idx) => {
    const key = `${utt}:${idx}`;
    if (chunkCue.get(key)?.last) storyCue.set(null);
    chunkCue.delete(key);
    socket.send({ type: "played", utt, idx });
  });

  // A chunk whose audio would not decode: failed, never played. The words
  // stay on screen; the turn says not all of it was read aloud.
  audioPlayer.onFailed((utt, idx) => {
    const key = `${utt}:${idx}`;
    if (chunkCue.get(key)?.last) storyCue.set(null);
    chunkCue.delete(key);
    const chunk = chunkText.get(key);
    chunkText.delete(key);
    if (chunk && chunk.capture === undefined) jarvisText(utt, chunk.text, idx);
    jarvisReading(utt, "failed");
    report(utt, idx, "failed");
  });

  // End of speech is the server's call (`status: idle` after every chunk is
  // acked); a transient empty queue mid-utterance must not flip the UI.
  // The highlight goes when nothing is playing, but the reading has not
  // ended: the timeline stays on it (MessageList.tsx) until `status: idle`,
  // a skip, a stop or another reading ends it.
  audioPlayer.onFinished(() => {
    readingUtt = null;
    jarvisPaused(); captureQuiet(); storyCue.set(null);
  });

  audioPlayer.onNeedsGesture(() => {
    notice("Playback", "Click anywhere to enable audio.");
  });

  // ---------------------------------------------------------------------------
  // WebSocket messages
  // ---------------------------------------------------------------------------

  socket.onMessage((msg) => {
    const type = msg.type as string;
    countVoiceMessage();

    if (type === "config") {
      muteMicDuringSpeech = Boolean(msg.muteMicDuringSpeech);
    } else if (type === "reply") {
      const utt = Number(msg.utt);
      knownReplies.add(utt);
      if (knownReplies.size > 64) knownReplies.delete(knownReplies.values().next().value!);
      if (msg.done) {
        finishedReplies.add(utt);
        if (finishedReplies.size > 64) finishedReplies.delete(finishedReplies.values().next().value!);
      }
      const outcome = REPLY_OUTCOMES.find(o => o === msg.outcome) as ReplyOutcome | undefined;
      const reason = REPLY_FAILURES.find(r => r === msg.reason);
      jarvisReply(utt, String(msg.text ?? ""), Boolean(msg.done),
                  typeof msg.at === "number" ? msg.at : undefined, outcome,
                  reason ? { reason, ...(typeof msg.resetsAt === "number" ? { resetsAt: msg.resetsAt } : {}) }
                         : undefined);
      if (msg.done && currentState === "thinking") transition("listening");
    } else if (type === "quiet") {
      // The brain's quiet_microphone: his Mute or Mic off, as if clicked.
      // Only ever quieter: Muted never undoes Mic off.
      if (msg.state === "off") chooseQuiet("off");
      else if (msg.state === "muted" && quiet.get() === "none") chooseQuiet("muted");
    } else if (type === "reading_skipped") {
      // Skipped here, or by the brain's skip_reading: the same either way.
      skipLocally(Number(msg.utt));
    } else if (type === "audio") {
      if (skipped.has(Number(msg.utt))) return;
      const data = msg.data as string;
      if (data) {
        if (readingUtt === null) readingUtt = Number(msg.utt);
        skippedReading = null;
        if (currentState !== "speaking") transition("speaking");
        if (msg.text) {
          chunkText.set(`${msg.utt}:${msg.idx}`, {
            text: String(msg.text),
            ...(typeof msg.capture === "number" ? { capture: msg.capture } : {}),
          });
        }
        if (msg.cue && typeof msg.cue === "object") {
          chunkCue.set(`${msg.utt}:${msg.idx}`, msg.cue as unknown as StoryCue);
        }
        audioPlayer.enqueue(data, Number(msg.utt), Number(msg.idx));
      }
      if (msg.text) console.log("[JARVIS]", msg.text);
    } else if (type === "brain_sign_in" || type === "brain_state") {
      // A brain finished signing in, or became ready, failed or was replaced:
      // the orb, the Settings and the Diagnostics views read the brains again.
      brainsMayHaveChanged();
    } else if (type === "capture") {
      // What Tim said to send: the capture box (CaptureBox.tsx).
      captureChanged(msg as unknown as CaptureMessage);
    } else if (type === "conversation") {
      // Something went on the conversation's record, or a message moved:
      // the history reads it again (conversation.ts).
      conversationChanged.set(conversationChanged.get() + 1);
    } else if (type === "display") {
      // Shown, never spoken: a Sources item in the conversation (SourcesChip.tsx).
      shown(msg as unknown as Display);
    } else if (type === "package_page") {
      // A page package to show in the centre area (PackageView.tsx), on the
      // page's Open page or the brain's open_package_page.
      if (msg.package) showPackage(msg.package as unknown as PackageInfo);
    } else if (type === BUNDLE_VIEW_FRAME) {
      // A file a teammate sent, to show in the centre area
      // (BundleMemberView.tsx), on the page's Open or the brain's
      // show_bundle_member. The frame carries ids only.
      if (typeof msg.message_id === "string" && typeof msg.attachment_id === "string"
          && typeof msg.member_id === "string") {
        showMember({ messageId: msg.message_id, attachmentId: msg.attachment_id,
                     memberId: msg.member_id });
      }
    } else if (type === "package_story") {
      // A story started, moved to a slide, finished or stopped
      // (story_teller.py): the package's Tell the story and Stop follow it.
      storyState.set(msg as unknown as StoryState);
    } else if (type === "stop") {
      // Stop speaking from anywhere: this page's Stop came back, the brain's
      // stop_speaking, or he talked over JARVIS.
      stopAll();
      transition("listening");
    } else if (type === "drop_queued") {
      audioPlayer.dropQueued();
    } else if (type === "status") {
      const state = msg.state as string;
      if (state === "thinking") transition("thinking");
      else if (state === "speaking" && skippedReading === null) transition("speaking");
      else if (state === "compacting") transition("compacting");
      else if (state === "idle") { jarvisQuiet(); transition("listening"); }
    } else if (type === "text") {
      // Text replies must remain visible even when voice output is unavailable.
      if (msg.text && typeof msg.capture !== "number") {
        jarvisText(Number(msg.utt ?? -1), String(msg.text), typeof msg.idx === "number" ? msg.idx : undefined);
      }
      console.log("[JARVIS]", msg.text);
      notice("Unvoiced speech", String(msg.text ?? ""));
    } else if (type === "notice") {
      // Never spoken. The server sends one when it is about to be busy for a
      // few seconds (a context rotation), and an empty string to clear it; the
      // orb shows the state, and the words go to Messages.
      const text = String(msg.text ?? "");
      if (text) notice("Assistant", text);
    }
  });

  // ---------------------------------------------------------------------------
  // Kick off
  // ---------------------------------------------------------------------------

  // Quiet: no microphone is asked for until he starts listening on this
  // page (MicControl.tsx), or unmutes or turns the microphone on. A reload
  // keeps Mute and Mic off (mic.ts) and never keeps listening.
  voiceInput.setQuiet(quiet.get(), false);
  startListening.current = () => { voiceInput.start(); };
  void loadVoiceInputSettings();
  transition("listening");

  // Resume AudioContext on ANY user interaction (browser autoplay policy)
  function ensureAudioContext() {
    const ctx = audioPlayer.getAnalyser().context as AudioContext;
    if (ctx.state === "suspended") {
      ctx.resume().then(() => console.log("[audio] context resumed"));
    }
  }
  document.addEventListener("click", ensureAudioContext);
  document.addEventListener("touchstart", ensureAudioContext);
  document.addEventListener("keydown", ensureAudioContext, { once: true });

  // Try to resume audio context on load
  ensureAudioContext();

  // ---------------------------------------------------------------------------
  // UI Controls
  // ---------------------------------------------------------------------------

  // The mute and mic-off buttons (MuteButton.tsx, MicOffButton.tsx) and
  // another tab of this browser set the quiet preference (mic.ts): muted
  // sends nothing and keeps the microphone open, off releases it. Only this
  // page's own Unmute or Mic on starts listening again.
  quiet.subscribe(() => {
    voiceInput.setQuiet(quiet.get(), quietBy === "here");
    micFollowsState();
  });

}
