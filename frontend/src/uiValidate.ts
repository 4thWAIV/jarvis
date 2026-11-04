/**
 * The page's half of the UI contract's validation: the same rules as
 * jarvis/ui_contract/__init__.py, over the same generated declarations
 * (uiContract.ts). Every request the page receives and every command the
 * page itself dispatches passes through here; an unknown command, kind,
 * part, argument or protocol version is refused with a reason.
 *
 * Pure: no page, no store. tests/test_ui_contract.py runs the shared cases
 * (tests/fixtures/ui_contract_cases.json) through this file under node and
 * through the Python module, and both must agree. Its imports name their
 * `.ts` extension so node can run it as it is.
 */

import { UI_CONTRACT, type Actor, type Operation, type TargetRef } from "./uiContract.ts";

type Spec = { type: string; values?: readonly string[]; entity?: string; maxLength?: number;
              min?: number; max?: number };
type CommandDecl = { id: string; args: Record<string, Spec>; required: readonly string[];
                     actors: readonly string[] };
type KindDecl = { kind: string; entity?: string | null; keyOf?: readonly string[];
                  keys?: readonly string[] | null; parts: readonly string[];
                  presentations: readonly { view: string; parts?: readonly string[] }[] };

export const KEY_SEPARATOR = "/";
const C = UI_CONTRACT;
const COMMANDS = new Map<string, CommandDecl>(
  (C.commands as unknown as CommandDecl[]).map((c) => [c.id, c]));
const KINDS = new Map<string, KindDecl>((C.kinds as unknown as KindDecl[]).map((k) => [k.kind, k]));
const VIEWS = new Map<string, { id: string; region: string }>(C.views.map((v) => [v.id, v]));
const KEY_PATTERNS = new Map<string, RegExp>(
  Object.entries(C.entityKeys).map(([k, p]) => [k, new RegExp(p as string, "u")]));

export function commandDecl(id: string): CommandDecl | undefined { return COMMANDS.get(id); }
export function kindDecl(kind: string): KindDecl | undefined { return KINDS.get(kind); }

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function keyProblem(entity: string, value: unknown): string | null {
  const pattern = KEY_PATTERNS.get(entity)!;
  if (typeof value !== "string") return `must be a ${entity} key`;
  return value.length <= C.limits.keyMax && fullMatch(pattern, value) ? null : `is not a valid ${entity} key`;
}

function fullMatch(pattern: RegExp, value: string): boolean {
  const m = pattern.exec(value);
  return m !== null && m.index === 0 && m[0].length === value.length;
}

function argProblem(spec: Spec, value: unknown): string | null {
  switch (spec.type) {
    case "enum": return spec.values!.includes(value as string) ? null : `must be one of ${spec.values!.join(", ")}`;
    case "key": return keyProblem(spec.entity!, value);
    case "string": {
      const limit = spec.maxLength ?? C.limits.labelMax;
      return typeof value === "string" && value.length > 0 && value.length <= limit ? null
        : `must be a string of 1 to ${limit} characters`;
    }
    case "integer":
      return Number.isInteger(value) && (spec.min ?? -Infinity) <= (value as number)
        && (value as number) <= (spec.max ?? Infinity) ? null : "must be a whole number in range";
    case "boolean": return typeof value === "boolean" ? null : "must be true or false";
    default: return `has unknown type ${spec.type}`;
  }
}

/** Why `args` are not valid for `commandId` called by `actor`, or null. */
export function argsProblem(commandId: unknown, args: unknown, actor: Actor = "brain"): string | null {
  const cmd = typeof commandId === "string" ? COMMANDS.get(commandId) : undefined;
  if (!cmd) return `unknown command ${JSON.stringify(commandId)}`;
  if (!cmd.actors.includes(actor)) return `${cmd.id} is not offered to the ${actor}`;
  if (!isObject(args)) return "arguments must be an object";
  const unknown = Object.keys(args).filter((k) => !(k in cmd.args)).sort();
  if (unknown.length) return `unknown argument ${unknown[0]}`;
  const missing = cmd.required.filter((k) => !(k in args));
  if (missing.length) return `missing argument ${missing[0]}`;
  for (const [name, value] of Object.entries(args)) {
    const problem = argProblem(cmd.args[name], value);
    if (problem) return `argument ${name} ${problem}`;
  }
  return null;
}

/** A composite key's parts, in its kind's `keyOf` order. */
export function splitKey(kind: KindDecl, key: string): string[] {
  const n = kind.keyOf!.length;
  const parts = key.split(KEY_SEPARATOR);
  return parts.length <= n ? parts : [...parts.slice(0, n - 1), parts.slice(n - 1).join(KEY_SEPARATOR)];
}

/** The parts a kind offers in one view. */
export function partsIn(kind: KindDecl, view: string): readonly string[] | null {
  const pres = kind.presentations.find((p) => p.view === view);
  return pres ? (pres.parts ?? kind.parts) : null;
}

/** Why `ref` is not a valid target reference, or null. */
export function targetProblem(ref: unknown): string | null {
  if (!isObject(ref)) return "target must be an object";
  const unknown = Object.keys(ref).filter((k) => !["region", "view", "kind", "key", "part"].includes(k)).sort();
  if (unknown.length) return `unknown target field ${unknown[0]}`;
  const kind = KINDS.get(ref.kind as string);
  if (!kind) return `unknown target kind ${JSON.stringify(ref.kind ?? null)}`;
  const view = VIEWS.get(ref.view as string);
  if (!view) return `unknown view ${JSON.stringify(ref.view ?? null)}`;
  if (ref.region !== view.region) return `view ${view.id} is in region ${view.region}, not ${JSON.stringify(ref.region ?? null)}`;
  const parts = partsIn(kind, view.id);
  if (!parts) return `${kind.kind} is not shown in ${view.id}`;
  const key = ref.key;
  if (kind.entity) {
    const problem = keyProblem(kind.entity, key);
    if (problem) return `key ${problem}`;
  } else if (kind.keyOf && kind.keyOf.length) {
    if (typeof key !== "string") return "key must be a composite key";
    const split = splitKey(kind, key);
    if (split.length !== kind.keyOf.length) {
      return `key must have ${kind.keyOf.length} parts joined by ${KEY_SEPARATOR}`;
    }
    for (let i = 0; i < split.length; i++) {
      const problem = keyProblem(kind.keyOf[i], split[i]);
      if (problem) return `key part ${problem}`;
    }
  } else if (kind.keys != null) {
    if (!kind.keys.includes(key as string)) return `key must be one of ${kind.keys.join(", ")}`;
  } else if (key !== undefined && key !== null) {
    return `${kind.kind} takes no key`;
  }
  const part = ref.part;
  if (part !== undefined && part !== null && !parts.includes(part as string)) {
    return `${kind.kind} in ${view.id} has no part ${JSON.stringify(part)}`;
  }
  return null;
}

const OPERATION_FIELDS: Record<string, string[]> = {
  map: ["kind", "region", "view", "cursor"],
  navigate: ["kind", "commandId", "args"],
  indicate: ["kind", "target", "reveal", "style", "holdMs", "label"],
  clear: ["kind", "cueId"],
  journey: ["kind", "steps"],
};

/** Why a request's `operation` is not valid, or null. */
export function operationProblem(op: unknown): string | null {
  if (!isObject(op)) return "operation must be an object";
  const allowed = OPERATION_FIELDS[op.kind as string];
  if (!allowed) return `unknown operation ${JSON.stringify(op.kind ?? null)}`;
  const unknown = Object.keys(op).filter((k) => !allowed.includes(k)).sort();
  if (unknown.length) return `unknown ${op.kind} field ${unknown[0]}`;
  switch (op.kind) {
    case "map":
      if ("region" in op && !(C.regions as readonly string[]).includes(op.region as string)) return "map region is unknown";
      if ("view" in op && !VIEWS.has(op.view as string)) return "map view is unknown";
      return null;
    case "navigate":
      return argsProblem(op.commandId, op.args ?? {});
    case "indicate": {
      const problem = targetProblem(op.target);
      if (problem) return problem;
      if (!(C.revealModes as readonly string[]).includes((op.reveal ?? "never") as string)) return "reveal must be never or navigate";
      if (!(C.endpointStyles as readonly string[]).includes((op.style ?? "outline") as string)) return "style is unknown";
      const hold = op.holdMs ?? 0;
      if (!Number.isInteger(hold) || (hold as number) < 0 || (hold as number) > C.timing.maxExtraHoldMs) {
        return "holdMs must be a whole number from 0 to the longest extra hold";
      }
      if (op.label !== undefined && op.label !== null
          && (typeof op.label !== "string" || op.label.length > C.limits.labelMax)) return "label is too long";
      return null;
    }
    case "clear": {
      const cue = op.cueId;
      if (cue !== undefined && cue !== null
          && (typeof cue !== "string" || cue.length === 0 || cue.length > C.limits.idMax)) return "cueId is not an id";
      return null;
    }
    default: {
      const steps = op.steps;
      if (!Array.isArray(steps) || steps.length === 0 || steps.length > C.limits.journeyMaxSteps) {
        return `a journey has 1 to ${C.limits.journeyMaxSteps} steps`;
      }
      for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        if (!isObject(step) || !["navigate", "indicate"].includes(step.kind as string)) {
          return `journey step ${i + 1} must navigate or indicate`;
        }
        const problem = operationProblem(step);
        if (problem) return `journey step ${i + 1}: ${problem}`;
      }
      return null;
    }
  }
}

/** The operation, typed, once it has passed `operationProblem`. */
export function asOperation(op: unknown): Operation { return op as Operation; }
/** The reference, typed, once it has passed `targetProblem`. */
export function asTarget(ref: unknown): TargetRef { return ref as TargetRef; }
