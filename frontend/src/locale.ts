/**
 * The interface's language: the one language JARVIS hears and speaks
 * (language_setting.py), never a second preference. The page keeps no copy
 * of its own (no localStorage): it shows whatever the server says is in
 * force, from three places, all through `reported`:
 *
 *   - the read of /api/settings/language when the page starts, again after
 *     every reconnect of the voice socket, and retried until it answers
 *     (`startLocale`, liveRead.ts);
 *   - the `language` frame on /ws/sessions that every change publishes, and
 *     the language on that socket's opening snapshot (sessionsSocket.ts);
 *   - the answer to the page's own change (LanguageControl.tsx).
 *
 * A pushed frame is always newer than a read that was already on its way,
 * so a read answered after a frame arrived is dropped (`guardedRead`). A
 * change the server refuses publishes nothing, so the interface stays in the
 * language still in force.
 *
 * A component gets its words with `useT()`: `const t = useT();` then
 * `t("tabs.right.agents")`, `t("language.downloading", { done, total })`, or
 * for a count `t("time.minutes", { count: 3 })`. The hook re-renders it when
 * the language changes; nothing is remounted, so views, selections, drafts
 * and focus stay as they were. Code outside a component calls `t` itself
 * and gets the language in force at that moment. Numbers, percentages and
 * dates go through the format helpers here, in the same language. The
 * document's `lang` follows the language.
 */

import { createElement, Fragment, useSyncExternalStore, type ReactNode } from "react";
import { liveRead } from "./liveRead";
import { en } from "./messages/en";
import { es } from "./messages/es";
import { createStore } from "./store";

// ── the catalogs' shape ──────────────────────────────────────────────────

/** A message that depends on a count: one form per plural category. */
export interface Plural { readonly one: string; readonly other: string; readonly many?: string }
export type Message = string | Plural;

export type MessageKey = keyof typeof en;

/** A translation of an English catalog (or one region's part of it):
 *  exactly its keys, each a string or plural forms as in English. */
export type CatalogOf<T> = { readonly [K in keyof T]: T[K] extends string ? string : Plural };
/** Every catalog has exactly the English catalog's keys (messages/es.ts is
 *  typed by it). */
export type Catalog = CatalogOf<typeof en>;

type Names<S> = S extends `${string}{${infer P}}${infer R}` ? P | Names<R> : never;
type NamesOf<M> = M extends string ? Names<M> : M extends Plural ? "count" | Names<M["one"]> | Names<M["other"]> : never;
/** The values a message needs, by name, from its English text. */
export type Params<K extends MessageKey> = { readonly [P in NamesOf<(typeof en)[K]>]: string | number };
type ParamArgs<K extends MessageKey> = [NamesOf<(typeof en)[K]>] extends [never] ? [] : [Params<K>];
/** A message with no named values: said with its key alone. */
export type PlainKey = { [K in MessageKey]: [NamesOf<(typeof en)[K]>] extends [never] ? K : never }[MessageKey];

// ── the language in force ────────────────────────────────────────────────

export const LANGUAGES = ["en", "es"] as const;
export type Language = (typeof LANGUAGES)[number];

const CATALOGS: Record<Language, Catalog> = { en, es };
/** The locale each language is formatted and pluralised in: Spanish is
 *  Latin American. */
const INTL: Record<Language, string> = { en: "en-US", es: "es-419" };

const current = createStore<Language>("en");
/** The language in force, as the server last said. Only `reported` sets it. */
export const language = { get: current.get, subscribe: current.subscribe };

const isLanguage = (value: unknown): value is Language => LANGUAGES.includes(value as Language);

// Moves on every pushed report: a read answered after one is stale.
let pushed = 0;

function apply(value: unknown): void {
  if (!isLanguage(value)) return;
  current.set(value);
  if (typeof document !== "undefined") document.documentElement.lang = INTL[value];
}

/** The server pushed the language in force (a `language` frame, or the
 *  sessions socket's opening snapshot): always the newest word. */
export function reported(value: unknown): void {
  pushed += 1;
  apply(value);
}

/** For a read of the language that is about to start: call the returned
 *  function with what it answers. It is applied unless a pushed report
 *  arrived while the read was on its way. */
export function guardedRead(): (value: unknown) => void {
  const at = pushed;
  return (value) => { if (pushed === at) apply(value); };
}

/** Read the language in force now, again after every reconnect, and
 *  retried until it answers (jarvis.ts calls this once). */
export function startLocale(): void {
  liveRead(async () => {
    const take = guardedRead();
    const res = await fetch("/api/settings/language");
    if (!res.ok) throw new Error(`${res.status}`);
    const body = (await res.json()) as { language?: unknown };
    take(body.language);
    return body.language;
  });
}

// ── words ────────────────────────────────────────────────────────────────

const missing = new Set<string>();
const warn = (what: string) => {
  if (missing.has(what)) return;
  missing.add(what);
  if ((import.meta as { env?: { DEV?: boolean } }).env?.DEV) console.warn(`[locale] ${what}`);
};

/** What a page asked for that its catalog could not give (a key no catalog
 *  has, or a value a message names but was not given), once each: the
 *  missing-translation diagnostic. Warned in the console while developing. */
export function missingTranslations(): string[] {
  return [...missing];
}

const plurals = new Map<Language, Intl.PluralRules>();
function pluralOf(lang: Language, count: number): "one" | "many" | "other" {
  let rules = plurals.get(lang);
  if (!rules) plurals.set(lang, rules = new Intl.PluralRules(INTL[lang]));
  const form = rules.select(count);
  return form === "one" || form === "many" ? form : "other";
}

function fill(lang: Language, key: string, text: string, params: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = params[name];
    if (value === undefined) {
      warn(`${lang} ${key}: no value for {${name}}`);
      return whole;
    }
    return typeof value === "number" ? formatNumber(value, lang) : value;
  });
}

/** One message in `lang`, its named values filled in. */
export function translate<K extends MessageKey>(lang: Language, key: K, ...args: ParamArgs<K>): string {
  const params = (args[0] ?? {}) as Record<string, string | number>;
  const message = (CATALOGS[lang] as Record<string, Message | undefined>)[key];
  if (message === undefined) {
    warn(`${lang}: no message ${String(key)}`);
    return String(key);
  }
  if (typeof message === "string") return fill(lang, key, message, params);
  const count = Number(params.count);
  const form = message[pluralOf(lang, count)] ?? message.other;
  return fill(lang, key, form, params);
}

/** One message in the language in force now. */
export function t<K extends MessageKey>(key: K, ...args: ParamArgs<K>): string {
  return translate(current.get(), key, ...args);
}

export type Translate = typeof t;

const BOUND: Record<Language, Translate> = {
  en: (key, ...args) => translate("en", key, ...args),
  es: (key, ...args) => translate("es", key, ...args),
};

/** The words of the language in force, for a component: it renders again
 *  when the language changes. */
export function useT(): Translate {
  return BOUND[useSyncExternalStore(current.subscribe, current.get)];
}

/** The language in force, for a component that formats by hand. */
export function useLanguage(): Language {
  return useSyncExternalStore(current.subscribe, current.get);
}

// ── numbers and dates, in the same language ──────────────────────────────

export function formatNumber(value: number, lang: Language = current.get(),
                             options?: Intl.NumberFormatOptions): string {
  return new Intl.NumberFormat(INTL[lang], options).format(value);
}

/** A fraction (0.42) as a percentage ("42%", "42 %"). */
export function formatPercent(fraction: number, lang: Language = current.get(), digits = 0): string {
  return new Intl.NumberFormat(INTL[lang], { style: "percent", maximumFractionDigits: digits }).format(fraction);
}

export function formatDateTime(when: Date | number, options: Intl.DateTimeFormatOptions,
                               lang: Language = current.get()): string {
  return new Intl.DateTimeFormat(INTL[lang], options).format(when);
}

// ── words kept by code, and words kept to be said later ──────────────────

/** Words by a record's own code, in the language in force whenever one is
 *  read: `WORDS[state]` stays the one way to say a state, and a component
 *  that renders it with `useT()` is drawn again when the language changes. */
export function wordsOf<C extends string>(codes: readonly C[], say: (code: C) => string): Readonly<Record<C, string>> {
  const words = {} as Record<C, string>;
  for (const code of codes) Object.defineProperty(words, code, { get: () => say(code), enumerable: true });
  return words;
}

/** A code's words (a status, a state, a refusal's code): the catalog's
 *  `<prefix>.<code>` in the language in force, with `values` put in, or
 *  null when the catalog has no words for that code (one this page does not
 *  know), for the caller to show the code or the server's own words
 *  instead. */
export function codeWords(prefix: string, code: string, values?: Record<string, unknown>): string | null {
  const key = `${prefix}.${code}`;
  return Object.prototype.hasOwnProperty.call(en, key)
    ? (t as (key: string, values?: Record<string, unknown>) => string)(key, values) : null;
}

/** Something to tell the person (a refusal, a result): the server's own
 *  words, shown as they came and marked as original detail, or the page's,
 *  worded in the language in force each time they are shown, with what
 *  another program said (`detail`) after them as it came. */
export type Said = { readonly original: string } | { readonly words: () => string; readonly detail?: string };

/** A line the server sent by code beside its English (`said` on a team
 *  refusal or status, a voice cause): the code, the values it names, and
 *  anything another program said, kept as it came. */
export interface CodedLine {
  code: string;
  values?: Record<string, string | number>;
  detail?: string;
}

/** A coded line in the person's words: the catalog's `<prefix>.<code>` with
 *  its values, in the language in force, and its detail after it; a line
 *  with no code (an older record) or one the catalog does not know is its
 *  `english`, shown as it came. */
export function codedSaid(prefix: string, line: CodedLine | null | undefined, english: string): Said {
  if (!line?.code || codeWords(prefix, line.code, line.values) === null) return { original: english };
  return { words: () => codeWords(prefix, line.code, line.values)!, detail: line.detail || undefined };
}

/** Its text, and the mark that says it is the server's original
 *  detail, for the element that shows it. Words with a detail are one text
 *  here; `SaidText` marks the detail on its own. */
export function saidShown(r: Said): { text: string; mark: { "data-original-detail"?: "" } } {
  if ("original" in r) return { text: r.original, mark: { "data-original-detail": "" } };
  return { text: r.detail ? t("said.withDetail", { message: r.words(), detail: r.detail }) : r.words(), mark: {} };
}

/** Words for the person, in an inline element: the server's own marked as
 *  original detail, or the page's in the language in force, with any
 *  detail after them marked as original detail. */
export function SaidText({ said, className, as = "span", role }: {
  said: Said; className: string; as?: "span" | "p" | "div"; role?: "alert" | "status" }): ReactNode {
  const tr = useT();
  if (!("original" in said) && said.detail) {
    const detail = createElement("span", { "data-original-detail": "" }, said.detail);
    return createElement(as, { className, role },
                         placed((mark) => tr("said.withDetail", { message: said.words(), detail: mark }), detail));
  }
  const { text, mark } = saidShown(said);
  return createElement(as, { className, role, ...mark }, text);
}

const MARK = "\u0001";

/** A message with one of its values drawn as an element (a bold figure, a
 *  name): `said` gives the message with `mark` in that value's place, so
 *  the words around it stay whole and in the language's own order; `text`
 *  draws the words on either side. */
export function placed(said: (mark: string) => string, element: ReactNode,
                       text: (words: string) => ReactNode = (words) => words): ReactNode {
  const [before, after = ""] = said(MARK).split(MARK);
  return createElement(Fragment, null, before && text(before), element, after && text(after));
}
