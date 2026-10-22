/**
 * JARVIS — the voice page's behaviour.
 *
 * Wires together the orb visualization, WebSocket communication,
 * speech recognition, and audio playback into a single experience.
 * The page itself is rendered by React (App.tsx); this runs once it is on
 * screen, with the orb the page created.
 */

import type { Orb, OrbState } from "./orb";
import { createVoiceInput, createAudioPlayer, createMicMonitor } from "./voice";
import { createSocket } from "./ws";
import { jarvisQuiet, jarvisSpeaking, userHeard } from "./captions";
import { addDisplay, type Display } from "./screen";
import { reconnected } from "./liveRead";
import { captureChanged, captureQuiet, captureSpeaking, loadCapture,
         type CaptureMessage } from "./capture";
import { brainsMayHaveChanged } from "./brains";
import { micLevel, micMuted } from "./mic";
import { statusText, stopSpeaking, voiceState } from "./voiceStatus";

export function startJarvis(orb: Orb): void {
  // ---------------------------------------------------------------------------
  // State machine
  // ---------------------------------------------------------------------------

  type State = "idle" | "listening" | "thinking" | "speaking" | "compacting";
  let currentState: State = "idle";
  let isMuted = false;

  const errorEl = document.getElementById("error-text")!;

  function showError(msg: string) {
    errorEl.textContent = msg;
    errorEl.style.opacity = "1";
    setTimeout(() => {
      errorEl.style.opacity = "0";
    }, 5000);
  }

  function updateStatus(state: State) {
    const labels: Record<State, string> = {
      idle: "",
      listening: "listening...",
      thinking: "thinking...",
      speaking: "",
      compacting: "",          // the notice banner carries the words; the orb carries the state
    };
    statusText.set(labels[state]);
  }

  // ---------------------------------------------------------------------------
  // Init components
  // ---------------------------------------------------------------------------

  const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const WS_URL = `${wsProto}//${window.location.host}/ws/voice`;
  const socket = createSocket(WS_URL);

  const audioPlayer = createAudioPlayer();
  orb.setAnalyser(audioPlayer.getAnalyser());

  let muteMicDuringSpeech = false;

  function transition(newState: State) {
    if (newState === currentState) return;
    currentState = newState;
    voiceState.set(newState);
    orb.setState(newState as OrbState);
    updateStatus(newState);
    micFollowsState();
  }

  // Mute only turns the microphone off; it has no say in the orb's state.
  function micFollowsState() {
    if (isMuted) return;
    if (currentState === "speaking" && muteMicDuringSpeech) {
      voiceInput.pause();
    } else {
      voiceInput.resume();
    }
  }

  // ---------------------------------------------------------------------------
  // Voice input
  // ---------------------------------------------------------------------------

  const voiceInput = createVoiceInput(
    (text: string) => {
      // The server decides whether this is echo, a barge-in, or a new turn.
      micMonitor.sawSpeech();
      // The turn's id goes with it, so a capture made from it can name it.
      const id = showHeard(text, true);
      socket.send({ type: "transcript", text, isFinal: true, ...(id === null ? {} : { id }) });
    },
    (text: string) => {
      micMonitor.sawSpeech();
      showHeard(text, false);
      socket.send({ type: "interim", text });
    },
    (msg: string) => {
      showError(msg);
    },
    (event: string) => {
      // Mirror the recogniser's lifecycle to the server log. Going deaf is a
      // browser-side failure the server cannot otherwise see at all, and the
      // console it used to be confined to is never open when it happens.
      socket.send({ type: "mic", text: event });
    }
  );

  // A live meter for the microphone itself. If this moves when you speak, the
  // microphone is working — whatever else is or is not happening. It answers
  // "is it even hearing me?" without a log, a console or anyone to ask.
  // Drawn by the footer's meter (MicMeter.tsx).
  const micMonitor = createMicMonitor(
    micLevel.set,
    (event: string) => {
      socket.send({ type: "mic", text: event });
      // Proven deaf: sound going in, nothing coming out. Do not wait for the
      // rotation timer to happen along — measured once at 21 seconds, all of
      // it lost. Rebuild the recogniser now.
      if (event.startsWith("DEAF")) voiceInput.restart("deaf: audio in, no results");
    }
  );

  // ── stopping him ──────────────────────────────────────────────────────────
  // Escape, or the button that appears while he is talking. Not a spoken word:
  // his voice comes back through the microphone garbled, and a mis-hear that
  // looked like "stop" would cut him off at random. A keystroke cannot be
  // misheard.

  function hush() {
    if (currentState !== "speaking") return;
    // Locally first: the round trip is real and silence should be instant.
    audioPlayer.stop();
    jarvisQuiet();
    socket.send({ type: "hush" });
    transition("listening");
  }

  // The Stop button in the status control (StatusControl.tsx).
  stopSpeaking.current = hush;
  window.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); hush(); }
  });

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
  audioPlayer.onStarted((utt, idx, seconds) => {
    const key = `${utt}:${idx}`;
    const chunk = chunkText.get(key);
    chunkText.delete(key);
    if (!chunk) return;
    if (chunk.capture !== undefined) {
      jarvisQuiet();
      captureSpeaking(chunk.capture, chunk.text, seconds);
    } else {
      captureQuiet();
      jarvisSpeaking(utt, chunk.text, seconds);
    }
  });

  // The server's captures are the truth: read them again whenever the socket
  // (re)connects, so a restarted server's empty list clears the boxes.
  socket.onOpen(loadCapture);
  // Every live read of the server's state (liveRead.ts) reads again.
  socket.onOpen(() => reconnected.set(reconnected.get() + 1));

  audioPlayer.onPlayed((utt, idx) => {
    socket.send({ type: "played", utt, idx });
  });

  // End of speech is the server's call (`status: idle` after every chunk is
  // acked); a transient empty queue mid-utterance must not flip the UI.
  // The highlight goes when nothing is playing.
  audioPlayer.onFinished(() => { jarvisQuiet(); captureQuiet(); });

  audioPlayer.onNeedsGesture(() => {
    showError("Click anywhere to enable audio");
  });

  // ---------------------------------------------------------------------------
  // WebSocket messages
  // ---------------------------------------------------------------------------

  socket.onMessage((msg) => {
    const type = msg.type as string;

    if (type === "config") {
      muteMicDuringSpeech = Boolean(msg.muteMicDuringSpeech);
    } else if (type === "audio") {
      const data = msg.data as string;
      if (data) {
        if (currentState !== "speaking") transition("speaking");
        if (msg.text) {
          chunkText.set(`${msg.utt}:${msg.idx}`, {
            text: String(msg.text),
            ...(typeof msg.capture === "number" ? { capture: msg.capture } : {}),
          });
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
    } else if (type === "display") {
      // Shown, never spoken: the Screen tab (ScreenView.tsx).
      addDisplay(msg as unknown as Display);
    } else if (type === "stop") {
      audioPlayer.stop();
      jarvisQuiet();
      transition("listening");
    } else if (type === "drop_queued") {
      audioPlayer.dropQueued();
    } else if (type === "status") {
      const state = msg.state as string;
      if (state === "thinking") transition("thinking");
      else if (state === "speaking") transition("speaking");
      else if (state === "compacting") transition("compacting");
      else if (state === "idle") transition("listening");
    } else if (type === "text") {
      // A chunk TTS could not voice: show it instead of losing it
      console.log("[JARVIS]", msg.text);
      statusText.set(String(msg.text));
    } else if (type === "notice") {
      // Shown, never spoken. The server sends one when it is about to be busy
      // for a few seconds (a context rotation), and an empty string to clear it.
      // Without it the pause looks like a crash.
      const text = String(msg.text ?? "");
      statusText.set(text);
      if (text) console.log("[notice]", text);
    }
  });

  // ---------------------------------------------------------------------------
  // Kick off
  // ---------------------------------------------------------------------------

  // Start listening after a brief delay for the orb to render
  setTimeout(() => {
    voiceInput.start();
    if (currentState !== "speaking") transition("listening");
  }, 1000);

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

  // The mute button (MuteButton.tsx) sets this; muting only turns the
  // microphone off.
  micMuted.subscribe(() => {
    isMuted = micMuted.get();
    if (isMuted) {
      voiceInput.mute();
      micMonitor.close();          // the meter goes dark and nothing captures
    } else {
      micMonitor.open();
      micFollowsState();
    }
  });

}
