// The page's coverage rules, read with TypeScript's own compiler (its parser
// and type checker, not text matching), for tests/test_ui_coverage_rules.py.
// They keep new navigation from bypassing the one registry
// (frontend/src/uiRegistry.ts) and keep every target declared:
//
//   control-unclassified  A DOM element that reacts to a person (a click, a
//                         key, a change, a submit, a scroll) either runs a
//                         registered command (a `bindingProps(...)` spread, a
//                         `data-ui-command` mark, or a handler that calls
//                         `byHand` / `runBinding`) or carries a reviewed
//                         non-navigation classification, `data-ui-action`,
//                         from CONTROL_CLASSES below. Conditional controls
//                         and keyboard handlers count the same.
//   unknown-classification  `data-ui-action` names a class not in the list.
//   route-state-write     Route state (a store declared with a `@routeState`
//                         JSDoc tag, a `detailSlot(...)`, or anything
//                         initialised from one) is set, shown, closed or
//                         adjusted only in the module that declares it, or
//                         a module its declaration names as a co-owner with
//                         `@routeStateAlso <file>`; a cast does not get round
//                         it.
//   undeclared-target     `useUiTarget` names a kind the contract does not
//                         present in that view. A view or kind is a literal,
//                         or typed as a union of literals (each is checked),
//                         and a reference built elsewhere is read from its
//                         type, member by member.
//   undeclared-part       `.part("x")` on a registration names a part the
//                         contract does not present for it, or a name that
//                         is neither a literal nor typed as literals.
//   index-key             A target's key is made from a number (an array
//                         position), not the record's own identity; a
//                         record's numeric field (`item.id`) is its identity.
//   label-unlocalized     Words the application shows are written into a
//                         component as English instead of coming from the
//                         catalog (`t("key")`, frontend/src/locale.ts): JSX
//                         text, a string in a JSX expression, a LABELLED
//                         attribute (aria-label, title, placeholder, alt,
//                         label), or a `title:` property in a .tsx file;
//                         and a target's `label` (useUiTarget), which the
//                         brain's map reads, in .ts files too, including
//                         the catalog read in one fixed language.
//                         Names that stay as they are in every language
//                         (KEPT_AS_IS) and text with no words (symbols,
//                         numbers, a hex id) are not words to translate; a
//                         call's arguments (a key, a class name) are not
//                         shown text.
//
// Run: node --experimental-transform-types --no-warnings
//        --import ./tests/ui_ts_loader.mjs tests/ui_coverage_rules.mts <dir>...
// Each <dir> is checked file by file (*.ts, *.tsx; the generated contract
// copy is skipped). Prints {"violations": [{rule, file, line, detail}]}.
import path from "node:path";
import fs from "node:fs";
import ts from "../frontend/node_modules/typescript/lib/typescript.js";
import { UI_CONTRACT } from "../frontend/src/uiContract.ts";

/** The reviewed classes of controls that are not navigation. Adding one is
 *  a review decision: say here what it covers. */
export const CONTROL_CLASSES: Record<string, string> = {
  domain: "Acts on JARVIS's records, settings or services on its own authorised path "
    + "(answer, dismiss, save, send, delete, restore, mark read, look up, test a key).",
  input: "Edits a form's own fields (typing, choosing, clearing); not navigation.",
  refresh: "Reads the same thing again; nothing is shown that was not.",
  microphone: "Mutes or releases the microphone.",
  speech: "Stops JARVIS speaking (hush).",
  recipient: "Chooses who a message goes to.",
  "sign-in": "A browser approval or sign-in flow, signing out included; never the brain's.",
  restart: "Restarts or updates the server; never the brain's.",
  file: "Downloads, reveals or removes a file.",
  setup: "First-time setup's own steps.",
  plumbing: "Only keeps an event from reaching the page; no action of its own.",
  scroll: "Remembers where a person scrolled a box (whether it follows its newest entries); "
    + "kept local, runs no command and never moves the navigation revision.",
};

/** Names shown as they are in every language: products, providers and
 *  each language's own name. Adding one is a review decision. */
export const KEPT_AS_IS = ["JARVIS", "Claude Code", "Claude", "Codex", "GitHub", "Fish Audio", "Fish",
                           "English", "Español", "MB", "GB", "KB", "ms", "CPU", "SHA-256", "OK"];

/** The attributes whose value is words a person reads or hears. */
const LABELLED = new Set(["aria-label", "title", "placeholder", "alt", "label", "aria-description"]);

/** The events that mean a person acted on the element. */
const EVENTS = new Set(["onClick", "onDoubleClick", "onKeyDown", "onKeyUp", "onKeyPress", "onSubmit",
                        "onChange", "onInput", "onPointerDown", "onPointerUp", "onMouseDown", "onMouseUp",
                        "onContextMenu", "onScroll"]);
const WRITES = new Set(["set", "show", "close", "adjust"]);

interface Violation { rule: string; file: string; line: number; detail: string }

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");

function sourcesIn(dir: string): string[] {
  return fs.readdirSync(dir)
    .filter((f) => /\.tsx?$/.test(f) && !f.endsWith(".d.ts") && f !== "uiContract.ts")
    .map((f) => path.join(dir, f));
}

const dirs = process.argv.slice(2).map((d) => path.resolve(d));
const roots = dirs.flatMap(sourcesIn);
const config = ts.readConfigFile(path.join(ROOT, "frontend", "tsconfig.json"), ts.sys.readFile).config;
const { options } = ts.parseJsonConfigFileContent(config, ts.sys, path.join(ROOT, "frontend"));
const program = ts.createProgram(roots, options);
const checker = program.getTypeChecker();
const violations: Violation[] = [];

function report(rule: string, node: ts.Node, detail: string): void {
  const sf = node.getSourceFile();
  violations.push({ rule, file: path.relative(ROOT, sf.fileName), detail,
                    line: sf.getLineAndCharacterOfPosition(node.getStart()).line + 1 });
}

/** The symbol an expression names, through imports. */
function symbolOf(node: ts.Node): ts.Symbol | undefined {
  let s = checker.getSymbolAtLocation(node);
  if (s && s.flags & ts.SymbolFlags.Alias) s = checker.getAliasedSymbol(s);
  return s;
}

/** An expression without parentheses, casts and non-null marks. */
function bare(e: ts.Expression): ts.Expression {
  while (ts.isParenthesizedExpression(e) || ts.isAsExpression(e) || ts.isNonNullExpression(e)
         || ts.isTypeAssertionExpression(e) || ts.isSatisfiesExpression(e)) e = e.expression;
  return e;
}

const calls = (e: ts.Expression, name: string) =>
  ts.isCallExpression(e) && ts.isIdentifier(e.expression) && symbolOf(e.expression)?.name === name;

/** The declaration that makes a value route state, if it is: tagged
 *  `@routeState`, made by `detailSlot`, or initialised from either. */
function routeStateDecl(s: ts.Symbol | undefined, seen = new Set<ts.Symbol>()): ts.VariableDeclaration | null {
  if (!s || seen.has(s)) return null;
  seen.add(s);
  for (const d of s.declarations ?? []) {
    if (!ts.isVariableDeclaration(d)) continue;
    // The tag is on the statement's JSDoc; the declaration's tags include it.
    const tagged = ts.getJSDocTags(d).some((t) => t.tagName.text === "routeState");
    if (tagged) return d;
    if (!d.initializer) continue;
    const init = bare(d.initializer);
    if (calls(init, "detailSlot")) return d;
    const from = ts.isIdentifier(init) ? routeStateDecl(symbolOf(init), seen) : null;
    if (from) return from;
  }
  return null;
}

function attr(el: ts.JsxOpeningLikeElement, name: string): ts.JsxAttribute | undefined {
  return el.attributes.properties.find((p): p is ts.JsxAttribute =>
    ts.isJsxAttribute(p) && p.name.getText() === name);
}

function containsCall(node: ts.Node, name: string): boolean {
  let found = false;
  const visit = (n: ts.Node) => {
    if (found) return;
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && symbolOf(n.expression)?.name === name) found = true;
    else ts.forEachChild(n, visit);
  };
  visit(node);
  return found;
}

/** Whether a spread is a binding's props: `bindingProps(...)` itself, or
 *  a variable that holds them. */
function spreadsBinding(e: ts.Expression): boolean {
  const b = bare(e);
  if (ts.isIdentifier(b)) {
    const d = symbolOf(b)?.declarations?.[0];
    return !!d && ts.isVariableDeclaration(d) && !!d.initializer && containsCall(d.initializer, "bindingProps");
  }
  return containsCall(b, "bindingProps");
}

function checkControl(el: ts.JsxOpeningLikeElement): void {
  const tag = el.tagName.getText();
  if (!/^[a-z]/.test(tag)) return;                      // a component: its own elements are checked
  const handlers = el.attributes.properties.filter((p) => ts.isJsxAttribute(p) && EVENTS.has(p.name.getText()));
  if (!handlers.length) return;
  const bound = el.attributes.properties.some((p) => ts.isJsxSpreadAttribute(p) && spreadsBinding(p.expression))
    || attr(el, "data-ui-command") !== undefined
    || handlers.some((h) => containsCall(h, "byHand") || containsCall(h, "runBinding"));
  const action = attr(el, "data-ui-action");
  if (action) {
    const value = action.initializer && ts.isStringLiteral(action.initializer) ? action.initializer.text : null;
    if (value === null || !(value in CONTROL_CLASSES)) {
      report("unknown-classification", el, `<${tag}> data-ui-action ${value ?? "(not a literal)"}`);
    }
    return;
  }
  if (!bound) {
    const names = handlers.map((h) => (h as ts.JsxAttribute).name.getText()).join(", ");
    report("control-unclassified", el, `<${tag}> ${names} runs no registered command and has no data-ui-action`);
  }
}

function checkWrite(call: ts.CallExpression): void {
  const callee = call.expression;
  if (!ts.isPropertyAccessExpression(callee) || !WRITES.has(callee.name.text)) return;
  const target = bare(callee.expression);
  if (!ts.isIdentifier(target) && !ts.isPropertyAccessExpression(target)) return;
  const decl = routeStateDecl(symbolOf(ts.isIdentifier(target) ? target : target.name));
  if (!decl) return;
  const here = path.basename(call.getSourceFile().fileName);
  const also = ts.getJSDocTags(decl).filter((t) => t.tagName.text === "routeStateAlso")
    .map((t) => ts.getTextOfJSDocComment(t.comment)?.trim());
  if (decl.getSourceFile() !== call.getSourceFile() && !also.includes(here)) {
    report("route-state-write", call, `${target.getText()}.${callee.name.text}(...) outside `
      + path.basename(decl.getSourceFile().fileName));
  }
}

/** The object literals an argument may be (a conditional's branches). */
function literals(e: ts.Expression): ts.ObjectLiteralExpression[] {
  e = bare(e);
  if (ts.isObjectLiteralExpression(e)) return [e];
  if (ts.isConditionalExpression(e)) return [...literals(e.whenTrue), ...literals(e.whenFalse)];
  if (ts.isIdentifier(e)) {
    const s = ts.isShorthandPropertyAssignment(e.parent) && e.parent.name === e
      ? checker.getShorthandAssignmentValueSymbol(e.parent) : symbolOf(e);
    const d = s?.declarations?.[0];
    if (d && ts.isVariableDeclaration(d) && d.initializer) return literals(d.initializer);
  }
  return [];
}

function prop(o: ts.ObjectLiteralExpression, name: string): ts.Expression | undefined {
  for (const p of o.properties) {
    if (ts.isPropertyAssignment(p) && p.name.getText() === name) return p.initializer;
    if (ts.isShorthandPropertyAssignment(p) && p.name.text === name) return p.name;
  }
  return undefined;
}

function hasNumber(e: ts.Node): boolean {
  let found = false;
  const visit = (n: ts.Node) => {
    if (found) return;
    const field = ts.isPropertyAccessExpression(n.parent) && n.parent.name === n;
    if (ts.isIdentifier(n) && !field && checker.getTypeAtLocation(n).flags & ts.TypeFlags.NumberLike) found = true;
    else ts.forEachChild(n, visit);
  };
  visit(e);
  return found;
}

/** The strings a type can only be: its literal, or each member of a union
 *  of literals; null for anything wider. */
function literalTexts(t: ts.Type): string[] | null {
  const members = t.isUnion() ? t.types : [t];
  return members.every((m) => m.isStringLiteral()) ? members.map((m) => (m as ts.StringLiteralType).value) : null;
}

/** The strings an expression can only be: a literal, or typed as literals. */
function textsOf(e: ts.Expression): string[] | null {
  const b = bare(e);
  return ts.isStringLiteral(b) ? [b.text] : literalTexts(checker.getTypeAtLocation(b));
}

/** The (view, kind) pairs a reference can be: from its literal's view and
 *  kind, or, for one built elsewhere, from its type, member by member. No
 *  pairs when a view or kind is not known to be literals. */
function refPairs(refExpr: ts.Expression): { node: ts.Node; pairs: [string, string][]; key?: ts.Expression }[] {
  const lits = literals(refExpr);
  const cross = (vs: string[] | null, ks: string[] | null) =>
    vs && ks ? vs.flatMap((v) => ks.map((k): [string, string] => [v, k])) : null;
  if (lits.length) {
    return lits.map((ref) => {
      const view = prop(ref, "view"), kind = prop(ref, "kind");
      return { node: ref, key: prop(ref, "key"),
               pairs: cross(view ? textsOf(view) : null, kind ? textsOf(kind) : null) ?? [] };
    });
  }
  const t = checker.getTypeAtLocation(bare(refExpr));
  const field = (m: ts.Type, name: string) => {
    const s = m.getProperty(name);
    return s ? literalTexts(checker.getTypeOfSymbolAtLocation(s, refExpr)) : null;
  };
  return (t.isUnion() ? t.types : [t]).map((m) => ({ node: refExpr, pairs: cross(field(m, "view"), field(m, "kind")) ?? [] }));
}

function checkTarget(call: ts.CallExpression): void {
  if (!ts.isIdentifier(call.expression) || symbolOf(call.expression)?.name !== "useUiTarget") return;
  const allowed = new Set<string>();
  for (const spec of call.arguments.length ? literals(call.arguments[0]) : []) {
    const label = prop(spec, "label");
    if (label) checkTargetLabel(label);
    const refExpr = prop(spec, "ref");
    for (const { node, pairs, key } of refExpr ? refPairs(refExpr) : []) {
      if (!pairs.length) {
        report("undeclared-target", node, "a target's view and kind are literals");
        continue;
      }
      for (const [v, k] of pairs) {
        const decl = UI_CONTRACT.kinds.find((x) => x.kind === k);
        const pres = decl?.presentations.find((p) => p.view === v) as { parts?: readonly string[] } | undefined;
        if (!decl || !pres) {
          report("undeclared-target", node, `${k} is not presented in ${v}`);
          continue;
        }
        (pres.parts ?? decl.parts).forEach((p) => allowed.add(p));
        if (key && hasNumber(key)) report("index-key", key, `${k} keyed by ${key.getText()}`);
      }
    }
  }
  // The registration's parts: `<name>.part("x")` wherever its result goes.
  const holder = call.parent;
  if (!ts.isVariableDeclaration(holder) || !ts.isIdentifier(holder.name)) return;
  const owner = symbolOf(holder.name);
  const scope = holder.getSourceFile();
  const visit = (n: ts.Node) => {
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) && n.expression.name.text === "part"
        && ts.isIdentifier(n.expression.expression) && symbolOf(n.expression.expression) === owner) {
      const names = n.arguments[0] ? textsOf(n.arguments[0]) : null;
      if (!names) report("undeclared-part", n, "a part's name is a literal");
      else for (const name of names) {
        if (!allowed.has(name)) report("undeclared-part", n, `part ${name} is not declared for it`);
      }
    }
    ts.forEachChild(n, visit);
  };
  visit(scope);
}

// ── words written in English ─────────────────────────────────────────────

const KEPT = new RegExp(`\\b(?:${KEPT_AS_IS.map((w) => w.replace(/[.*+?^${}()|[\]\\-]/g, "\\$&")).join("|")})\\b`, "g");

/** Whether text has words to translate: two letters in a row, once the
 *  names kept as they are and hex ids are taken out. */
function hasWords(text: string): boolean {
  const rest = text.replace(KEPT, " ").replace(/\b[0-9a-f]{8,}\b/gi, " ");
  return /\p{L}{2,}/u.test(rest);
}

/** The literal texts an expression shows (through conditionals, `??`,
 *  `||`, `&&` and templates), never a call's arguments or a comparison. */
function shownLiterals(e: ts.Expression, out: ts.Node[] = []): ts.Node[] {
  e = bare(e);
  if (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e)) {
    if (hasWords(e.text)) out.push(e);
  } else if (ts.isTemplateExpression(e)) {
    const texts = [e.head.text, ...e.templateSpans.map((sp) => sp.literal.text)].join(" ");
    if (hasWords(texts)) out.push(e);
    e.templateSpans.forEach((sp) => shownLiterals(sp.expression, out));
  } else if (ts.isConditionalExpression(e)) {
    shownLiterals(e.whenTrue, out);
    shownLiterals(e.whenFalse, out);
  } else if (ts.isBinaryExpression(e) && [ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken,
                                           ts.SyntaxKind.AmpersandAmpersandToken].includes(e.operatorToken.kind)) {
    shownLiterals(e.left, out);
    shownLiterals(e.right, out);
  }
  return out;
}

/** A target's label is what the brain's map reads (ui_map): the catalog's
 *  words in the language in force, never English written in, nor the
 *  catalog read in one fixed language (`translate("en", …)`). */
function checkTargetLabel(e: ts.Expression): void {
  for (const lit of shownLiterals(e)) report("label-unlocalized", lit, `target label ${lit.getText().slice(0, 40)}`);
  const visit = (n: ts.Node) => {
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && symbolOf(n.expression)?.name === "translate"
        && n.arguments[0] && ts.isStringLiteral(bare(n.arguments[0]))) {
      report("label-unlocalized", n, `target label in one language ${n.getText().slice(0, 40)}`);
    }
    ts.forEachChild(n, visit);
  };
  visit(e);
}

function checkWords(n: ts.Node): void {
  if (!n.getSourceFile().fileName.endsWith(".tsx")) return;
  if (ts.isJsxText(n)) {
    if (hasWords(n.text)) report("label-unlocalized", n, `text "${n.text.trim().slice(0, 40)}"`);
  } else if (ts.isJsxExpression(n) && n.expression && (ts.isJsxElement(n.parent) || ts.isJsxFragment(n.parent))) {
    for (const lit of shownLiterals(n.expression)) {
      report("label-unlocalized", lit, `text ${lit.getText().slice(0, 40)}`);
    }
  } else if (ts.isJsxAttribute(n) && LABELLED.has(n.name.getText()) && n.initializer) {
    const init = n.initializer;
    const lits = ts.isStringLiteral(init) ? (hasWords(init.text) ? [init] : [])
      : ts.isJsxExpression(init) && init.expression ? shownLiterals(init.expression) : [];
    for (const lit of lits) report("label-unlocalized", lit, `${n.name.getText()} ${lit.getText().slice(0, 40)}`);
  } else if (ts.isPropertyAssignment(n) && n.name.getText() === "title") {
    for (const lit of shownLiterals(n.initializer)) report("label-unlocalized", lit, `title ${lit.getText().slice(0, 40)}`);
  }
}

for (const file of roots) {
  const sf = program.getSourceFile(file)!;
  const visit = (n: ts.Node) => {
    if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) checkControl(n);
    if (ts.isCallExpression(n)) { checkWrite(n); checkTarget(n); }
    checkWords(n);
    ts.forEachChild(n, visit);
  };
  visit(sf);
}

process.stdout.write(JSON.stringify({ violations, classes: CONTROL_CLASSES }));
