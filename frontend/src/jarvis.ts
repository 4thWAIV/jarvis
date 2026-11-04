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
import { jarvisQuiet, jarvisSpeaking, shown, userHeard, userTyped, jarvisText, jarvisReply, jarvisReconnected, jarvisDisconnected } from "./captions";
import type { Display } from "./sources";
import { reconnected } from "./liveRead";
import { captureChanged, captureQuiet, captureSpeaking, loadCapture,
         type CaptureMessage } from "./capture";
import { brainsMayHaveChanged } from "./brains";
import { conversationChanged } from "./conversation";
import { inputStatus, micLevel, quiet, quietBy, startListening, voiceInputSettings } from "./mic";
import { loadVoiceInputSettings } from "./voiceSettings";
import { CLIENT_ID } from "./uiClient";
import { offerDraft } from "./conversation/Composer";
import { sendTyped, stopSpeaking, voiceState, voiceConnected, conversationJump } from "./voiceStatus";
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
      onDiscard: () => { socket.send({ type: "turn_discard", clientId: CLIENT_ID }); },
      // Kept words that could not be finished: never sent, an unsent draft.
      onDraft: offerDraft,
    }
  );

  // ── stopping him ──────────────────────────────────────────────────────────
  // Escape, or the button that appears while he is talking. Not a spoken word:
  // his voice comes back through the microphone garbled, and a mis-hear that
  // looked like "stop" would cut him off at random. A keystroke cannot be
  // misheard.

  let readingUtt: number | null = null;
  let readingReply = false;
  let skippedReading: number | null = null;
  const skipped = new Set<number>();
  const finishedReplies = new Set<number>();
  const knownReplies = new Set<number>();
  function rememberSkipped(utt: number) {
    skipped.add(utt);
    if (skipped.size > 64) skipped.delete(skipped.values().next().value!);
    for (const key of chunkText.keys()) if (key.startsWith(`${utt}:`)) chunkText.delete(key);
  }
  function skipReading(utt: number) {
    rememberSkipped(utt);
    skippedReading = utt;
    jarvisQuiet(); captureQuiet();
    transition(finishedReplies.has(utt) ? "listening" : "thinking");
    audioPlayer.cancelUtterance(utt);
  }
  function hush() {
    if (currentState !== "speaking") return;
    if (readingReply && readingUtt !== null) {
      const target = readingUtt;
      skipReading(target);
      socket.send({ type: "skip_to_end", utt: target });
      conversationJump.set(conversationJump.get() + 1);
      return;
    }
    // Locally first: the round trip is real and silence should be instant.
    audioPlayer.stop();
    jarvisQuiet();
    readingUtt = null; readingReply = false; chunkText.clear();
    chunkCue.clear();
    // The turn he stopped also stops pointing: its UI requests are
    // withdrawn and every white signal cleared.
    uiClient.interrupt();
    storyCue.set(null);
    socket.send({ type: "hush" });
    transition("listening");
  }

  // The Stop button in the status control (StatusControl.tsx).
  stopSpeaking.current = hush;
  window.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); hush(); }
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
  const chunkText = new Map<string, { text: string; capture?: number; reply: boolean }>();
  // A story beat's cue, by chunk (packages.ts): shown in the package's frame
  // as the beat's words START playing, and ended as its last words end.
  const chunkCue = new Map<string, StoryCue>();
  audioPlayer.onStarted((utt, idx, seconds) => {
    readingUtt = utt; skippedReading = null;
    transition("speaking");
    const key = `${utt}:${idx}`;
    // Kept until the chunk is played: onPlayed ends the beat at its last chunk.
    const cue = chunkCue.get(key);
    if (cue) storyCue.set(cue);
    else storyCue.set(null);
    const chunk = chunkText.get(key);
    chunkText.delete(key);
    if (!chunk) return;
    readingReply = chunk.reply;
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
    readingUtt = null; readingReply = false; skippedReading = null;
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

  // End of speech is the server's call (`status: idle` after every chunk is
  // acked); a transient empty queue mid-utterance must not flip the UI.
  // The highlight goes when nothing is playing.
  audioPlayer.onFinished(() => {
    readingUtt = null; readingReply = false;
    jarvisQuiet(); captureQuiet(); storyCue.set(null);
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
      jarvisReply(utt, String(msg.text ?? ""), Boolean(msg.done), typeof msg.at === "number" ? msg.at : undefined);
      if (msg.done && currentState === "thinking") transition("listening");
    } else if (type === "reading_skipped") {
      const utt = Number(msg.utt);
      if (readingUtt === utt) skipReading(utt);
      else { rememberSkipped(utt); audioPlayer.cancelUtterance(utt); }
    } else if (type === "audio") {
      if (skipped.has(Number(msg.utt))) return;
      const data = msg.data as string;
      if (data) {
        if (readingUtt === null) {
          readingUtt = Number(msg.utt);
          readingReply = knownReplies.has(readingUtt) && typeof msg.capture !== "number";
        }
        skippedReading = null;
        if (currentState !== "speaking") transition("speaking");
        if (msg.text) {
          chunkText.set(`${msg.utt}:${msg.idx}`, {
            text: String(msg.text),
            reply: knownReplies.has(Number(msg.utt)) && typeof msg.capture !== "number",
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
      readingUtt = null; readingReply = false; chunkText.clear();
      audioPlayer.stop();
      chunkCue.clear();
      storyCue.set(null);
      jarvisQuiet();
      transition("listening");
    } else if (type === "drop_queued") {
      audioPlayer.dropQueued();
    } else if (type === "status") {
      const state = msg.state as string;
      if (state === "thinking") transition("thinking");
      else if (state === "speaking" && skippedReading === null) transition("speaking");
      else if (state === "compacting") transition("compacting");
      else if (state === "idle") transition("listening");
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
