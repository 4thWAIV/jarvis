// The interface's catalogs and its locale store (frontend/src/locale.ts,
// frontend/src/messages/), run under node for
// tests/test_ui_locale.py:
//
//   - both catalogs have the same keys, each with the same placeholders (in
//     every plural form) and the same kind (a string, or plural forms), part
//     by part; no key is in two parts, and a region's keys start with it;
//   - nothing in Spanish is left as the English, but for the reviewed
//     entries that read the same in both (SAME_IN_BOTH);
//   - words are filled in by name, counts choose the plural form, numbers
//     are formatted in the language;
//   - a read answered after a pushed report does not undo it;
//   - a missing key or value is reported by the diagnostic, not hidden.
//
// Run: node --experimental-strip-types --no-warnings
//        --import ./tests/ui_ts_loader.mjs tests/ui_locale_cases.mts
import assert from "node:assert/strict";
import { en, PARTS as EN_PARTS } from "../frontend/src/messages/en.ts";
import { es, PARTS as ES_PARTS } from "../frontend/src/messages/es.ts";
import {
  formatNumber, formatPercent, guardedRead, language, missingTranslations, reported, t, translate,
  type Message, type MessageKey,
} from "../frontend/src/locale.ts";

/** Entries reviewed as reading the same in Spanish as in English. The team
 *  row's error is nothing but its value, itself worded by its own code. */
// left.updates.prName: "PR 42" is GitHub's own name for a pull request, in both.
// main.conversation.overlapLead: "· {second}", a mark before words worded by their own key.
const SAME_IN_BOTH = new Set<string>(["units.megabytes", "right.subsystem.said.teamError", "left.updates.prName",
                                      "main.conversation.overlapLead"]);

const done: string[] = [];
const check = (name: string, fn: () => void) => ({ name, fn });

const names = (m: Message): string[] => {
  const texts = typeof m === "string" ? [m] : Object.values(m).filter((v): v is string => typeof v === "string");
  const out = new Set<string>(typeof m === "string" ? [] : ["count"]);
  for (const text of texts) for (const [, n] of text.matchAll(/\{(\w+)\}/g)) out.add(n);
  return [...out].sort();
};

const cases = [
  check("both catalogs have the same keys, kinds and placeholders", () => {
    const enKeys = Object.keys(en).sort(), esKeys = Object.keys(es).sort();
    assert.deepEqual(esKeys, enKeys);
    for (const key of enKeys) {
      const a = (en as Record<string, Message>)[key], b = (es as Record<string, Message>)[key];
      assert.equal(typeof a, typeof b, key);
      assert.deepEqual(names(b), names(a), key);
      if (typeof a !== "string") {
        assert.ok(typeof b !== "string" && b.one && b.other, key);
        for (const form of Object.values(b)) assert.ok(form.includes("{count}"), `${key}: every form shows its count`);
      }
    }
  }),
  check("each region keeps its own keys, in both languages", () => {
    assert.deepEqual(Object.keys(ES_PARTS), Object.keys(EN_PARTS));
    const seen = new Map<string, string>();
    for (const [part, words] of Object.entries(EN_PARTS)) {
      assert.deepEqual(Object.keys((ES_PARTS as Record<string, object>)[part]).sort(), Object.keys(words).sort(), part);
      for (const key of Object.keys(words)) {
        assert.ok(!seen.has(key), `${key} is in ${seen.get(key)} and ${part}`);
        seen.set(key, part);
        if (part !== "shell") assert.ok(key.startsWith(`${part}.`), `${key} starts with ${part}.`);
        else assert.ok(!/^(right|main|left)\./.test(key), `${key} is a region's`);
      }
    }
  }),
  check("nothing in Spanish is left in English but the reviewed entries", () => {
    const left = Object.keys(en).filter((k) =>
      JSON.stringify((en as Record<string, Message>)[k]) === JSON.stringify((es as Record<string, Message>)[k])
      && !SAME_IN_BOTH.has(k));
    assert.deepEqual(left, []);
    for (const k of SAME_IN_BOTH) assert.ok(k in en, `${k} is still a key`);
  }),
  check("words are filled in by name, and counts choose the plural form", () => {
    assert.equal(translate("en", "tabs.right.agents"), "Agents");
    assert.equal(translate("es", "tabs.right.agents"), "Agentes");
    assert.equal(translate("es", "language.downloading", { done: "12 MB", total: "124 MB" }),
                 "Descargando, 12 MB de 124 MB");
    assert.equal(translate("en", "time.minutes", { count: 1 }), "1 minute");
    assert.equal(translate("en", "time.minutes", { count: 2 }), "2 minutes");
    assert.equal(translate("es", "time.days", { count: 1 }), "1 día");
    assert.equal(translate("es", "time.days", { count: 0 }), "0 días");
    assert.equal(translate("es", "time.hours", { count: 1500 }), `${formatNumber(1500, "es")} horas`);
  }),
  check("numbers and percentages are formatted in the language", () => {
    assert.equal(formatNumber(1234.5, "en"), new Intl.NumberFormat("en-US").format(1234.5));
    assert.equal(formatNumber(1234.5, "es"), new Intl.NumberFormat("es-419").format(1234.5));
    assert.equal(formatPercent(0.42, "es"), new Intl.NumberFormat("es-419", { style: "percent" }).format(0.42));
  }),
  check("the language follows what the server reports, and a stale read does not undo a pushed one", () => {
    assert.equal(language.get(), "en");
    const read = guardedRead();               // a read on its way...
    reported("es");                           // ...overtaken by a pushed change
    read("en");
    assert.equal(language.get(), "es");
    assert.equal(t("menu.settings"), "Configuración");
    const fresh = guardedRead();
    fresh("en");                              // a read with nothing pushed since: taken
    assert.equal(language.get(), "en");
    reported("fr");                           // not a language JARVIS has: ignored
    assert.equal(language.get(), "en");
  }),
  check("a missing key or value is reported, never hidden", () => {
    const dynamic = "tabs.right.nowhere" as MessageKey;
    assert.equal(translate("es", dynamic as "menu.button"), "tabs.right.nowhere");
    assert.equal((translate as (l: string, k: string, p?: object) => string)("en", "language.download", {}),
                 "Download ({size})");
    assert.ok(missingTranslations().includes("es: no message tabs.right.nowhere"), missingTranslations().join());
    assert.ok(missingTranslations().includes("en language.download: no value for {size}"));
  }),
];

for (const { name, fn } of cases) {
  fn();
  done.push(name);
}
process.stdout.write(JSON.stringify({ passed: done }));
