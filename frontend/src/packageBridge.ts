/**
 * The bridge between a page package's frame (PackageView.tsx) and JARVIS.
 *
 * The frame is sandboxed with `allow-scripts` alone, so it has no origin:
 * it can reach none of JARVIS's routes, and Chrome will not give it the
 * microphone (getUserMedia fails "Invalid security origin" even with
 * `allow="microphone"`). What it may do, it asks for by postMessage to its
 * parent, and only messages from that frame's own window are heard. Every
 * message both ways carries `jarvis: "package"`; docs/work-packages.md has
 * the protocol.
 *
 *   save    {name, data, id?}  data a string (UTF-8), ArrayBuffer, typed
 *                              array or Blob; written as results/<name>
 *                              (packages_api.py), answered with `saved`
 *                              {id, name, ok, saved, bytes} or {.., ok:
 *                              false, error}
 *   listen  {on}               the page's own microphone (voiceInput.ts),
 *                              answered with `listening` {on, microphone,
 *                              sampleRate, channels, format}, then, while
 *                              on, one `audio` {pcm, sampleRate, ...} per
 *                              100 ms frame: PCM16 little-endian mono
 */

import { inputStatus } from "./mic";
import { saveResult } from "./packages";
import { listenToMicrophone, MICROPHONE_FORMAT } from "./voiceInput";

export const BRIDGE = "package";

export interface BridgeEvents {
  /** A save finished: its words for the view. */
  onSaved(words: string): void;
  onListening(on: boolean): void;
}

function asBlob(data: unknown): Blob | null {
  if (typeof data === "string" || data instanceof ArrayBuffer) return new Blob([data]);
  if (ArrayBuffer.isView(data)) {
    return new Blob([new Uint8Array(data.buffer, data.byteOffset, data.byteLength).slice()]);
  }
  return data instanceof Blob ? data : null;
}

/** Hear `frame`'s requests for package `packageId`; returns the way to stop. */
export function connectBridge(frame: HTMLIFrameElement, packageId: string,
                              events: BridgeEvents): () => void {
  let stopListening: (() => void) | null = null;
  const post = (message: Record<string, unknown>, transfer: Transferable[] = []) =>
    frame.contentWindow?.postMessage({ jarvis: BRIDGE, ...message }, "*", transfer);

  const listen = (on: boolean) => {
    stopListening?.();
    stopListening = on
      ? listenToMicrophone((pcm) => post({ type: "audio", pcm, ...MICROPHONE_FORMAT }, [pcm]))
      : null;
    post({ type: "listening", on, microphone: inputStatus.get().state, ...MICROPHONE_FORMAT });
    events.onListening(on);
  };

  const save = async (message: Record<string, unknown>) => {
    const name = message.name;
    const reply = (fields: Record<string, unknown>) =>
      post({ type: "saved", id: message.id, name, ...fields });
    const data = asBlob(message.data);
    if (typeof name !== "string" || !data) {
      const error = "A result needs a name and its data: a string, an ArrayBuffer, a typed " +
        "array or a Blob.";
      reply({ ok: false, error });
      events.onSaved(`Not saved: ${error}`);
      return;
    }
    try {
      const saved = await saveResult(packageId, name, data);
      reply({ ok: true, ...saved });
      events.onSaved(`Saved ${saved.saved}.`);
    } catch (e) {
      const error = (e as Error).message;
      reply({ ok: false, error });
      events.onSaved(`Not saved: ${error}`);
    }
  };

  const onMessage = (event: MessageEvent) => {
    if (event.source !== frame.contentWindow) return;
    const message = event.data;
    if (!message || typeof message !== "object" || message.jarvis !== BRIDGE) return;
    if (message.type === "save") void save(message);
    else if (message.type === "listen") listen(Boolean(message.on));
  };

  window.addEventListener("message", onMessage);
  return () => {
    window.removeEventListener("message", onMessage);
    stopListening?.();
  };
}
