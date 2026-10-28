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
import { jarvisQuiet, jarvisSpeaking, shown, userHeard, userTyped } from "./captions";
import type { Display } from "./sources";
import { reconnected } from "./liveRead";
import { captureChanged, captureQuiet, captureSpeaking, loadCapture,
         type CaptureMessage } from "./capture";
import { brainsMayHaveChanged } from "./brains";
import { conversationChanged } from "./conversation";
import { inputStatus, micLevel, micMuted, micOff } from "./mic";
import { sendTyped, statusText, stopSpeaking, voiceState } from "./voiceStatus";
import { countVoiceMessage, notice } from "./voiceHealth";
import { startSessionsSocket } from "./sessionsSocket";

export function startJarvis(orb: Orb): void {
  // ---------------------------------------------------------------------------
  // State machine
  // ---------------------------------------------------------------------------

  type State = "idle" | "listening" | "thinking" | "speaking" | "compacting";
  let currentState: State = "idle";
  let isMuted = false;
  let isOff = false;

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
  startSessionsSocket();

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

  // Mute and mic off only stop the microphone; they have no say in the orb's
  // state.
  function micFollowsState() {
    if (isMuted || isOff) return;
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
      // The turn's id goes with it, so a capture made from it can name it.
      const id = showHeard(text, true);
      socket.send({ type: "transcript", text, isFinal: true, ...(id === null ? {} : { id }) });
    },
    (text: string) => {
      showHeard(text, false);
      socket.send({ type: "interim", text });
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

  // ── typing ─────────────────────────────────────────────────────────────
  // What Tim types into the white box goes the way a final transcript does,
  // marked typed: the server neither corrects it nor takes it for echo.
  sendTyped.current = (text: string) => {
    const id = userTyped(text);
    socket.send({ type: "transcript", text, isFinal: true, typed: true, id });
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
    } else if (type === "conversation") {
      // Something went on the conversation's record, or a message moved:
      // the history reads it again (conversation.ts).
      conversationChanged.set(conversationChanged.get() + 1);
    } else if (type === "display") {
      // Shown, never spoken: a Sources item in the conversation (SourcesChip.tsx).
      shown(msg as unknown as Display);
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
      // A chunk TTS could not voice: kept in Messages instead of lost.
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

  // At once: there is no recogniser to wait for, and the microphone is
  // taken as soon as the browser allows.
  voiceInput.start();
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

  // The mute button (MuteButton.tsx) sets this: nothing is sent while
  // muted, and the microphone stays open.
  micMuted.subscribe(() => {
    isMuted = micMuted.get();
    if (isMuted) {
      voiceInput.mute();           // the meter reads zero and nothing is sent
    } else {
      voiceInput.unmute();
      micFollowsState();
    }
  });

  // The mic-off button (MicOffButton.tsx) sets this: the microphone itself
  // is released.
  micOff.subscribe(() => {
    isOff = micOff.get();
    if (isOff) {
      voiceInput.micOff();
    } else {
      voiceInput.micOn();
      micFollowsState();
    }
  });

}
