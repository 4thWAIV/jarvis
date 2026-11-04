"""Write the page's copy of the UI navigation contract, or check it.

    .venv/bin/python scripts/generate_ui_contract.py           # write
    .venv/bin/python scripts/generate_ui_contract.py --check   # exit 1 if stale

The source is `jarvis/ui_contract/` (protocol.json and families/*.json),
merged and checked by `jarvis.ui_contract.load`. From it:

    frontend/src/uiContract.ts   the merged contract as a constant, and every
                                 type the page's registry, dispatcher,
                                 pipeline and transport are written against

The test suite runs `--check`'s comparison, so a stale copy fails CI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPES = ROOT / "frontend" / "src" / "uiContract.ts"

HEADER = """\
// GENERATED from jarvis/ui_contract/ by scripts/generate_ui_contract.py.
// Do not edit: change the declarations there and regenerate. The test suite
// checks this copy is exact.
"""


def _ts_arg(spec: dict) -> str:
    kind = spec["type"]
    if kind == "enum":
        return " | ".join(json.dumps(v) for v in spec["values"])
    if kind in ("string", "key"):
        return "string"
    if kind == "integer":
        return "number"
    return "boolean"


def _command_args(contract) -> str:
    lines = ["export interface CommandArgs {"]
    for cid, cmd in sorted(contract.commands.items()):
        fields = []
        for name, spec in cmd["args"].items():
            optional = "" if name in cmd["required"] else "?"
            fields.append(f"{name}{optional}: {_ts_arg(spec)}")
        lines.append(f"  {json.dumps(cid)}: {{ {'; '.join(fields)}{'; ' if fields else ''}}};")
    lines.append("}")
    return "\n".join(lines)


TYPES_TAIL = """
type C = typeof UI_CONTRACT;
export type Region = C["regions"][number];
export type Actor = C["actors"][number];
export type Status = C["statuses"][number];
export type Lifecycle = C["lifecycle"][number];
export type Arrival = C["arrivals"][number];
export type EndpointStyle = C["endpointStyles"][number];
export type RevealMode = C["revealModes"][number];
export type Visibility = C["visibility"][number];
export type ViewKind = C["viewKinds"][number];
export type AvailabilityReason = keyof C["availabilityReasons"];
export type SideEffect = keyof C["sideEffects"];
export type EntityKind = keyof C["entityKeys"];
export type ViewId = C["views"][number]["id"];
export type CommandId = C["commands"][number]["id"];
export type TargetKind = C["kinds"][number]["kind"];
export type PartOf<K extends TargetKind> = Extract<C["kinds"][number], { kind: K }>["parts"][number];
export type JunctionId = C["pipeline"]["junctions"][number]["id"];
export type RailId = C["pipeline"]["rails"][number]["id"];
export type EdgeId = C["pipeline"]["edges"][number]["id"];

/** One presentation of one target: which entity, in which view, which part. */
export interface TargetRef {
  region: Region;
  view: ViewId;
  kind: TargetKind;
  key?: string;
  part?: string;
}

export interface Availability { available: boolean; reason?: AvailabilityReason }

export type Operation =
  | { kind: "map"; region?: Region; view?: ViewId; cursor?: string }
  | { kind: "navigate"; commandId: CommandId; args: Record<string, unknown> }
  | { kind: "indicate"; target: TargetRef; reveal?: RevealMode; style?: EndpointStyle;
      holdMs?: number; label?: string }
  | { kind: "clear"; cueId?: string }
  | { kind: "journey"; steps: (Extract<Operation, { kind: "navigate" }>
                              | Extract<Operation, { kind: "indicate" }>)[] };

/** `ui_request`, server to the one bound page. */
export interface UIRequest {
  type: "ui_request";
  protocol: number;
  requestId: string;
  clientId: string;
  epoch: number;
  /** The turn the request belongs to (the brain's caller binding). */
  turn: string | null;
  expectedNavigationRevision: number | null;
  /** Epoch milliseconds; a request seen after it is refused as expired. */
  expiresAt: number;
  operation: Operation;
}

/** `ui_result`, the page's one answer to one request. */
export interface UIResult {
  type: "ui_result";
  requestId: string;
  clientId: string;
  epoch: number;
  status: Status;
  navigationRevision: number;
  layoutRevision: number;
  effects: SideEffect[];
  cueId?: string;
  arrival?: Arrival;
  resolvedTarget?: TargetRef;
  reason?: string;
  /** The map operation's bounded answer. */
  map?: unknown;
  /** A journey's per-step outcomes, in order. */
  steps?: Omit<UIResult, "type" | "requestId" | "clientId" | "epoch" | "steps">[];
}

/** `ui_event`, a lifecycle step of a request under way. */
export interface UIEvent {
  type: "ui_event";
  requestId: string;
  clientId: string;
  epoch: number;
  event: Lifecycle;
  at: number;
  cueId?: string;
}

/** `ui_hello`, the page announcing itself on every (re)connect. */
export interface UIHello {
  type: "ui_hello";
  protocol: number;
  clientId: string;
  capabilities: string[];
  navigationRevision: number;
  layoutRevision: number;
  visible: boolean;
  reducedMotion: boolean;
}

/** `ui_welcome`, the server's answer: this connection's epoch. */
export interface UIWelcome { type: "ui_welcome"; clientId: string; epoch: number }
"""


def render() -> str:
    sys.path.insert(0, str(ROOT))
    from jarvis import ui_contract
    contract = ui_contract.load()
    data = json.dumps(contract.merged(), indent=2, ensure_ascii=False)
    return (HEADER + "\n/** The whole contract: protocol and every family, merged and checked. */\n"
            f"export const UI_CONTRACT = {data} as const;\n"
            + TYPES_TAIL + "\n" + _command_args(contract) + "\n")


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if not TYPES.exists() or TYPES.read_text(encoding="utf-8") != text:
            print(f"stale: {TYPES.relative_to(ROOT)}")
            return 1
        return 0
    TYPES.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
