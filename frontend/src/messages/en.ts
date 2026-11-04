/**
 * The interface's words in English: the reference catalog, one part per
 * region so each region's words are kept by its owner (execution.md,
 * LOCALIZATION OWNERSHIP). Every key here is in es.ts too, with the same
 * placeholders: the types in locale.ts make a missing key a compile error,
 * and tests/ui_locale_cases.mts checks the placeholders, that no key is
 * extra or in two parts, and that nothing is left in English.
 *
 * A message is whole: never a fragment joined to another. A value goes in
 * by name, `{name}`; a count chooses between the plural forms of an entry
 * (`one`, `other`, and in Spanish optionally `many`) and is `{count}` in
 * each. Keys are grouped by where the words are shown, `<area>.<what>`;
 * each region's keys start with its name (`right.`, `main.`, `left.`), the
 * shell's with its own areas.
 *
 * Names, file paths, people's and agents' words, and provider and product
 * names (JARVIS, Claude, Codex, GitHub, Fish Audio) are never here: they
 * are shown as they are, in either language.
 */

import { leftEn } from "./left.en";
import { mainEn } from "./main.en";
import { rightEn } from "./right.en";
import { shellEn } from "./shell.en";

export const PARTS = { shell: shellEn, right: rightEn, main: mainEn, left: leftEn } as const;

export const en = { ...shellEn, ...rightEn, ...mainEn, ...leftEn } as const;
