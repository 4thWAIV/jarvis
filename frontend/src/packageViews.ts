/** Host-owned package mounts on the authenticated voice connection. */
import { mainView } from './uiNavigation';
import { visibilityOf } from './uiRegistry';
import { CLIENT_ID } from './uiClient';
import type { JarvisSocket } from './ws';
import { OBSERVATION_BYTES, OBSERVATION_SCHEMA, type ObserveResponse } from './packageContentContract';

// Validate the generated JSON schema rather than a second handwritten field list.
function valid(value: unknown, schema: any, root: any = OBSERVATION_SCHEMA): boolean {
  if (schema.$ref) return valid(value, root.$defs[schema.$ref.split('/').pop()], root);
  if (schema.anyOf) return schema.anyOf.some((s: any) => valid(value, s, root));
  if ('const' in schema && value !== schema.const) return false;
  if (schema.enum && !schema.enum.includes(value)) return false;
  if (schema.type === 'null') return value === null;
  if (schema.type === 'string') return typeof value === 'string' &&
    (schema.maxLength === undefined || Array.from(value).length <= schema.maxLength) &&
    (schema.minLength === undefined || Array.from(value).length >= schema.minLength);
  if (schema.type === 'number' || schema.type === 'integer') return typeof value === 'number' &&
    Number.isFinite(value) && (schema.type !== 'integer' || Number.isInteger(value)) &&
    (schema.minimum === undefined || value >= schema.minimum) &&
    (schema.maximum === undefined || value <= schema.maximum);
  if (schema.type === 'boolean') return typeof value === 'boolean';
  if (schema.type === 'array') return Array.isArray(value) &&
    (schema.maxItems === undefined || value.length <= schema.maxItems) &&
    value.every(v => valid(v, schema.items, root));
  if (schema.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const data = value as Record<string, unknown>;
    if ((schema.required ?? []).some((key: string) => !(key in data))) return false;
    return Object.entries(data).every(([key, v]) => schema.properties?.[key]
      ? valid(v, schema.properties[key], root) : schema.additionalProperties !== false);
  }
  return true;
}

export function validObservation(value: unknown): value is ObserveResponse {
  try { return new TextEncoder().encode(JSON.stringify(value)).length <= OBSERVATION_BYTES && valid(value, OBSERVATION_SCHEMA); }
  catch { return false; }
}

type Mount = { frame: HTMLIFrameElement; packageId: string; snapshotId: string;
  mountId?: string; nonce?: string; sequence: number; stop?: () => void };
let socket: JarvisSocket | null = null;
const mounts = new Set<Mount>();
const post = (mount: Mount, message: object) => mount.frame.contentWindow?.postMessage({jarvis: 'package', ...message}, '*');
const state = (m: Mount) => ({focused: document.hasFocus(), visible: document.visibilityState === 'visible' && mainView.get() === 'package' && visibilityOf(m.frame) === 'visible'});
const open = (m: Mount) => socket?.send({type: 'package_view_mount', packageId: m.packageId,
  snapshotId: m.snapshotId, clientId: CLIENT_ID, ...state(m)});

export function startPackageViews(connection: JarvisSocket): void {
  socket = connection;
  connection.onOpen(() => {mounts.forEach(open); if (documentProvider) reportDocumentView(documentProvider(false));});
  connection.onClose(() => mounts.forEach(m => {m.mountId = undefined; m.nonce = undefined;}));
  connection.onMessage(message => {
    if (message.type === 'document_observe') {
      connection.send({type: 'document_view_response', requestId: message.requestId, state: documentProvider?.(true) ?? null});
    } else if (message.type === 'package_view_bound') {
      const m = [...mounts].find(v => v.packageId === message.packageId);
      if (!m) return;
      m.mountId = String(message.mountId); m.nonce = String(message.mountNonce); m.sequence = 0;
      post(m, {type: 'observe.init', protocol: 1, mountNonce: m.nonce});
    } else if (message.type === 'package_observe') {
      const m = [...mounts].find(v => v.mountId === message.mountId);
      if (m) post(m, message.request as object);
    }
  });
}

export function observeFrame(frame: HTMLIFrameElement, packageId: string, snapshotId?: string): () => void {
  if (!snapshotId) return () => {}; // An unpinned legacy mount has no verified revision.
  const m: Mount = {frame, packageId, snapshotId, sequence: 0};
  mounts.add(m);
  const loaded = () => {m.mountId = undefined; m.nonce = undefined; m.sequence = 0; open(m);};
  const changed = () => {
    if (m.mountId) socket?.send({type: 'package_view_state', mountId: m.mountId, ...state(m)});
  };
  const messages = (event: MessageEvent) => {
    if (event.source !== frame.contentWindow) return;
    const value = event.data;
    if (!value || value.jarvis !== 'package') return;
    if (value.type === 'observe.ready') {
      if (m.nonce) post(m, {type: 'observe.init', protocol: 1, mountNonce: m.nonce});
      return;
    }
    if (!m.mountId || value.mountNonce !== m.nonce || value.protocol !== 1) return;
    if (value.type === 'observe.response' && validObservation(value) && value.sequence >= m.sequence) {
      m.sequence = value.sequence;
      socket?.send({type: 'package_view_observation', mountId: m.mountId, response: value});
    } else if (value.type === 'observe.changed' && Number.isInteger(value.sequence) && value.sequence > m.sequence) {
      m.sequence = value.sequence;
      socket?.send({type: 'package_view_changed', mountId: m.mountId, response: {
        jarvis: 'package', type: 'observe.changed', protocol: 1, mountNonce: m.nonce, sequence: value.sequence}});
    }
  };
  frame.addEventListener('load', loaded);
  window.addEventListener('message', messages);
  window.addEventListener('focus', changed); window.addEventListener('blur', changed);
  document.addEventListener('visibilitychange', changed);
  const resized = () => {changed(); requestAnimationFrame(changed);};
  const stopView = mainView.subscribe(resized);
  const resize = new ResizeObserver(changed); resize.observe(frame);
  open(m);
  return () => {
    socket?.send({type: 'package_view_close', mountId: m.mountId});
    mounts.delete(m); stopView(); resize.disconnect(); frame.removeEventListener('load', loaded);
    window.removeEventListener('message', messages); window.removeEventListener('focus', changed);
    window.removeEventListener('blur', changed); document.removeEventListener('visibilitychange', changed);
  };
}

/** Ordinary attachment viewer state uses the same authenticated connection. */
export function reportDocumentView(data: Record<string, unknown> | null): void {
  socket?.send({type: 'document_view', clientId: CLIENT_ID, state: data});
}

let documentProvider: ((selection: boolean) => Record<string, unknown>) | null = null;
export function registerDocumentView(provider: (selection: boolean) => Record<string, unknown>): () => void {
  documentProvider = provider;
  const report = () => reportDocumentView(provider(false));
  const stopView = mainView.subscribe(() => {report(); requestAnimationFrame(report);});
  const events = ['focus', 'blur', 'resize'];
  events.forEach(name => window.addEventListener(name, report));
  document.addEventListener('visibilitychange', report);
  report();
  return () => {
    if (documentProvider === provider) {documentProvider = null; reportDocumentView(null);}
    stopView();
    events.forEach(name => window.removeEventListener(name, report));
    document.removeEventListener('visibilitychange', report);
  };
}
