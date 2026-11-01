"""The UI navigation contract: one versioned schema, read by both sides.

`protocol.json` is the wire (frames, statuses, lifecycle, timing, limits,
entity keys, side effects and the pipeline's fixed topology); each file in
`families/` declares one feature's views, navigation commands and target
kinds. They are merged here into one `Contract`, checked as a whole, and
the page's copy (`frontend/src/uiContract.ts`) is generated from the same
merge by `scripts/generate_ui_contract.py` (`--check` says if it is stale).

Python reads it to validate what the brain asks for and to build the
`ui_*` tools' schemas (`tool_specs`); the page validates the same way
(`frontend/src/uiValidate.ts`) against its generated copy. Nothing here is
a second inventory: a command or target the page can execute exists only
because it is declared in a family file, and the page refuses to register a
handler for anything undeclared.

Unknown protocol versions, frame fields, command IDs, target kinds, parts
and arguments fail with a reason; nothing is coerced or guessed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PROTOCOL_PATH = HERE / "protocol.json"

# A composite key's parts are joined with this; no entity key may contain it
# except the last part of a composite (a folder entry's relative path).
KEY_SEPARATOR = "/"
# Arguments substituted into a presentation's reveal command.
KEY_PLACEHOLDER = "$key"


class ContractError(ValueError):
    """The declarations themselves are inconsistent."""


@dataclass(frozen=True)
class Contract:
    protocol: dict
    views: dict[str, dict]
    commands: dict[str, dict]
    kinds: dict[str, dict]
    families: dict[str, dict] = field(default_factory=dict)

    @property
    def version(self) -> int:
        return int(self.protocol["version"])

    def merged(self) -> dict:
        """The whole contract as one JSON-able object: what the page's
        generated copy holds."""
        wire = {k: v for k, v in self.protocol.items() if k != "families"}
        return {**wire,
                "views": list(self.views.values()),
                "commands": list(self.commands.values()),
                "kinds": list(self.kinds.values()),
                "families": [{"family": f["family"], "owner": f["owner"]}
                             for f in self.families.values()]}


# -- loading and checking the declarations -------------------------------------


def _read(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def build(protocol: dict, families: list[dict]) -> Contract:
    """Merge and check. Raises ContractError naming every problem found."""
    problems: list[str] = []
    views: dict[str, dict] = {}
    commands: dict[str, dict] = {}
    kinds: dict[str, dict] = {}
    named: dict[str, dict] = {}
    for fam in families:
        name = fam.get("family")
        if not name or name in named:
            problems.append(f"family {name!r} is missing or declared twice")
            continue
        named[name] = fam
        for view in fam.get("views", []):
            if view["id"] in views:
                problems.append(f"view {view['id']} is declared twice")
            views[view["id"]] = view
        for cmd in fam.get("commands", []):
            if cmd["id"] in commands:
                problems.append(f"command {cmd['id']} is declared twice")
            commands[cmd["id"]] = {**cmd, "family": name}
        for kind in fam.get("kinds", []):
            if kind["kind"] in kinds:
                problems.append(f"target kind {kind['kind']} is declared twice")
            kinds[kind["kind"]] = {**kind, "family": name}
    contract = Contract(protocol, views, commands, kinds, named)
    problems += _check(contract)
    if problems:
        raise ContractError("; ".join(problems))
    return contract


def _check(c: Contract) -> list[str]:
    p = c.protocol
    out: list[str] = []
    regions, actors = set(p["regions"]), set(p["actors"])
    entities, effects = set(p["entityKeys"]), set(p["sideEffects"])
    for pattern in p["entityKeys"].values():
        try:
            re.compile(pattern)
        except re.error as e:
            out.append(f"entity key pattern {pattern!r} does not compile: {e}")
    for view in c.views.values():
        if view["region"] not in regions:
            out.append(f"view {view['id']} has unknown region {view['region']}")
        if not view["id"].startswith(view["region"] + "."):
            out.append(f"view {view['id']} is not named under its region {view['region']}")
        if view["kind"] not in p["viewKinds"]:
            out.append(f"view {view['id']} has unknown kind {view['kind']}")
        if view.get("entity") and view["entity"] not in entities:
            out.append(f"view {view['id']} names unknown entity {view['entity']}")
    for cmd in c.commands.values():
        where = f"command {cmd['id']}"
        if cmd["view"] not in c.views:
            out.append(f"{where} belongs to unknown view {cmd['view']}")
        if not set(cmd["actors"]) <= actors or not cmd["actors"]:
            out.append(f"{where} names unknown or no actors")
        for name, spec in cmd["args"].items():
            out += [f"{where} argument {name}: {e}" for e in _check_arg_spec(spec, entities)]
        for name in cmd["required"]:
            if name not in cmd["args"]:
                out.append(f"{where} requires undeclared argument {name}")
        for eff in cmd["effects"]:
            if eff["effect"] not in effects:
                out.append(f"{where} declares unknown effect {eff['effect']}")
            for arg in eff.get("when", {}):
                if arg not in cmd["args"]:
                    out.append(f"{where} effect condition names unknown argument {arg}")
    for kind in c.kinds.values():
        where = f"target kind {kind['kind']}"
        if kind.get("entity") and kind["entity"] not in entities:
            out.append(f"{where} names unknown entity {kind['entity']}")
        for ent in kind.get("keyOf", []):
            if ent not in entities:
                out.append(f"{where} key names unknown entity {ent}")
        keyings = [bool(kind.get("entity")), bool(kind.get("keyOf")), kind.get("keys") is not None]
        if sum(keyings) > 1:
            out.append(f"{where} is keyed more than one way")
        if len(set(kind["parts"])) != len(kind["parts"]):
            out.append(f"{where} lists a part twice")
        seen_views: set[str] = set()
        for pres in kind["presentations"]:
            view = pres["view"]
            if view not in c.views:
                out.append(f"{where} is presented in unknown view {view}")
            if view in seen_views:
                out.append(f"{where} is presented twice in {view}")
            seen_views.add(view)
            if not set(pres.get("parts", kind["parts"])) <= set(kind["parts"]):
                out.append(f"{where} in {view} presents parts it does not declare")
            reveal = pres.get("reveal")
            if reveal:
                cmd = c.commands.get(reveal["command"])
                if cmd is None:
                    out.append(f"{where} in {view} is revealed by unknown command {reveal['command']}")
                    continue
                if "brain" not in cmd["actors"]:
                    out.append(f"{where} in {view} is revealed by a command the brain may not run")
                sample = {k: ("x" if v == KEY_PLACEHOLDER else v) for k, v in reveal["args"].items()}
                problem = _args_shape_problem(cmd, sample, placeholders=set(
                    k for k, v in reveal["args"].items() if v == KEY_PLACEHOLDER))
                if problem:
                    out.append(f"{where} in {view} reveal: {problem}")
    out += _check_pipeline(p["pipeline"], regions)
    return out


def _check_arg_spec(spec: dict, entities: set[str]) -> list[str]:
    kind = spec.get("type")
    if kind == "enum":
        return [] if spec.get("values") else ["an enum needs values"]
    if kind == "key":
        return [] if spec.get("entity") in entities else [f"unknown entity {spec.get('entity')}"]
    if kind in ("string", "integer", "boolean"):
        return []
    return [f"unknown type {kind}"]


def _check_pipeline(pipe: dict, regions: set[str]) -> list[str]:
    out: list[str] = []
    junctions = {}
    for j in pipe["junctions"]:
        if j["id"] in junctions:
            out.append(f"junction {j['id']} is declared twice")
        if j["region"] not in regions:
            out.append(f"junction {j['id']} has unknown region")
        junctions[j["id"]] = j
    edges: dict[str, dict] = {}
    following: dict[str, list[str]] = {}
    for e in pipe["edges"]:
        if e["id"] in edges:
            out.append(f"edge {e['id']} is declared twice")
        edges[e["id"]] = e
        for end in (e["from"], e["to"]):
            if end not in junctions:
                out.append(f"edge {e['id']} joins unknown junction {end}")
        following.setdefault(e["from"], []).append(e["to"])
    if pipe["source"] not in junctions:
        out.append("the pipeline's source is not a junction")
    reached, todo = {pipe["source"]}, [pipe["source"]]
    while todo:
        for nxt in following.get(todo.pop(), []):
            if nxt not in reached:
                reached.add(nxt)
                todo.append(nxt)
    for jid in junctions:
        if jid not in reached:
            out.append(f"junction {jid} is not connected to the orb")
    for rail in pipe["rails"]:
        if rail["top"] not in junctions:
            out.append(f"rail {rail['id']} starts at unknown junction {rail['top']}")
        if rail["region"] not in regions:
            out.append(f"rail {rail['id']} has unknown region")
    if {r["region"] for r in pipe["rails"]} != regions:
        out.append("every region needs exactly one rail")
    return out


@lru_cache(maxsize=1)
def load() -> Contract:
    """The packaged contract, merged and checked once per process."""
    protocol = _read(PROTOCOL_PATH)
    families = [_read(HERE / rel) for rel in protocol["families"]]
    return build(protocol, families)


# -- validating what a caller sends ----------------------------------------------


def _key_problem(c: Contract, entity: str, value: Any) -> str | None:
    if not isinstance(value, str):
        return f"must be a {entity} key"
    pattern = c.protocol["entityKeys"][entity]
    if len(value) > c.protocol["limits"]["keyMax"] or not re.fullmatch(pattern, value):
        return f"is not a valid {entity} key"
    return None


def _arg_problem(c: Contract, spec: dict, value: Any) -> str | None:
    kind = spec["type"]
    if kind == "enum":
        return None if value in spec["values"] else f"must be one of {', '.join(spec['values'])}"
    if kind == "key":
        return _key_problem(c, spec["entity"], value)
    if kind == "string":
        limit = spec.get("maxLength", c.protocol["limits"]["labelMax"])
        return None if isinstance(value, str) and 0 < len(value) <= limit else \
            f"must be a string of 1 to {limit} characters"
    if kind == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
        ok = ok and spec.get("min", value) <= value <= spec.get("max", value)
        return None if ok else "must be a whole number in range"
    if kind == "boolean":
        return None if isinstance(value, bool) else "must be true or false"
    return f"has unknown type {kind}"   # pragma: no cover - _check refuses it


def _args_shape_problem(cmd: dict, args: Any, placeholders: set[str] = frozenset()) -> str | None:
    if not isinstance(args, dict):
        return "arguments must be an object"
    unknown = sorted(set(args) - set(cmd["args"]))
    if unknown:
        return f"unknown argument {unknown[0]}"
    missing = [n for n in cmd["required"] if n not in args]
    if missing:
        return f"missing argument {missing[0]}"
    return None


def args_problem(command_id: Any, args: Any, actor: str = "brain") -> str | None:
    """Why `args` are not valid for `command_id` called by `actor`, or None."""
    c = load()
    cmd = c.commands.get(command_id) if isinstance(command_id, str) else None
    if cmd is None:
        return f"unknown command {command_id!r}"
    if actor not in cmd["actors"]:
        return f"{command_id} is not offered to the {actor}"
    shape = _args_shape_problem(cmd, args)
    if shape:
        return shape
    for name, value in args.items():
        problem = _arg_problem(c, cmd["args"][name], value)
        if problem:
            return f"argument {name} {problem}"
    return None


def split_key(kind: dict, key: str) -> list[str]:
    """A composite key's parts, in its `keyOf` order."""
    return key.split(KEY_SEPARATOR, len(kind["keyOf"]) - 1)


def target_problem(ref: Any) -> str | None:
    """Why `ref` is not a valid target reference, or None."""
    c = load()
    if not isinstance(ref, dict):
        return "target must be an object"
    unknown = sorted(set(ref) - {"region", "view", "kind", "key", "part"})
    if unknown:
        return f"unknown target field {unknown[0]}"
    kind = c.kinds.get(ref.get("kind"))
    if kind is None:
        return f"unknown target kind {ref.get('kind')!r}"
    view = c.views.get(ref.get("view"))
    if view is None:
        return f"unknown view {ref.get('view')!r}"
    if ref.get("region") != view["region"]:
        return f"view {view['id']} is in region {view['region']}, not {ref.get('region')!r}"
    pres = next((p for p in kind["presentations"] if p["view"] == view["id"]), None)
    if pres is None:
        return f"{kind['kind']} is not shown in {view['id']}"
    key = ref.get("key")
    if kind.get("entity"):
        problem = _key_problem(c, kind["entity"], key)
        if problem:
            return f"key {problem}"
    elif kind.get("keyOf"):
        if not isinstance(key, str):
            return "key must be a composite key"
        parts = split_key(kind, key)
        if len(parts) != len(kind["keyOf"]):
            return f"key must have {len(kind['keyOf'])} parts joined by {KEY_SEPARATOR}"
        for ent, value in zip(kind["keyOf"], parts):
            problem = _key_problem(c, ent, value)
            if problem:
                return f"key part {problem}"
    elif kind.get("keys") is not None:
        if key not in kind["keys"]:
            return f"key must be one of {', '.join(kind['keys'])}"
    elif key is not None:
        return f"{kind['kind']} takes no key"
    part = ref.get("part")
    if part is not None and part not in pres.get("parts", kind["parts"]):
        return f"{kind['kind']} in {view['id']} has no part {part!r}"
    return None


def operation_problem(op: Any) -> str | None:
    """Why a request's `operation` is not valid, or None."""
    c = load()
    p = c.protocol
    if not isinstance(op, dict):
        return "operation must be an object"
    kind = op.get("kind")
    allowed = {
        "map": {"kind", "region", "view", "cursor"},
        "navigate": {"kind", "commandId", "args"},
        "indicate": {"kind", "target", "reveal", "style", "holdMs", "label"},
        "clear": {"kind", "cueId"},
        "journey": {"kind", "steps"},
    }.get(kind)
    if allowed is None:
        return f"unknown operation {kind!r}"
    unknown = sorted(set(op) - allowed)
    if unknown:
        return f"unknown {kind} field {unknown[0]}"
    if kind == "map":
        if "region" in op and op["region"] not in p["regions"]:
            return "map region is unknown"
        if "view" in op and op["view"] not in c.views:
            return "map view is unknown"
        return None
    if kind == "navigate":
        return args_problem(op.get("commandId"), op.get("args", {}))
    if kind == "indicate":
        problem = target_problem(op.get("target"))
        if problem:
            return problem
        if op.get("reveal", "never") not in p["revealModes"]:
            return "reveal must be never or navigate"
        if op.get("style", "outline") not in p["endpointStyles"]:
            return "style is unknown"
        hold = op.get("holdMs", 0)
        if not isinstance(hold, int) or isinstance(hold, bool) or not 0 <= hold <= p["timing"]["maxExtraHoldMs"]:
            return "holdMs must be a whole number from 0 to the longest extra hold"
        label = op.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > p["limits"]["labelMax"]):
            return "label is too long"
        return None
    if kind == "clear":
        cue = op.get("cueId")
        if cue is not None and (not isinstance(cue, str) or not 0 < len(cue) <= p["limits"]["idMax"]):
            return "cueId is not an id"
        return None
    steps = op.get("steps")
    if not isinstance(steps, list) or not 0 < len(steps) <= p["limits"]["journeyMaxSteps"]:
        return f"a journey has 1 to {p['limits']['journeyMaxSteps']} steps"
    for i, step in enumerate(steps):
        if not isinstance(step, dict) or step.get("kind") not in ("navigate", "indicate"):
            return f"journey step {i + 1} must navigate or indicate"
        problem = operation_problem(step)
        if problem:
            return f"journey step {i + 1}: {problem}"
    return None


# -- the brain's tools, from the same declarations -------------------------------


def _target_schema(c: Contract) -> dict:
    return {
        "type": "object",
        "properties": {
            "region": {"type": "string", "enum": c.protocol["regions"]},
            "view": {"type": "string", "enum": sorted(c.views)},
            "kind": {"type": "string", "enum": sorted(c.kinds)},
            "key": {"type": "string", "maxLength": c.protocol["limits"]["keyMax"],
                    "description": "The entity's stable key (node ID, session ID, workflow ID, "
                                   "...); composite keys join their parts with '/'. Never a "
                                   "row number or a label."},
            "part": {"type": "string", "maxLength": c.protocol["limits"]["idMax"]},
        },
        "required": ["region", "view", "kind"],
        "additionalProperties": False,
    }


def tool_specs() -> list[dict]:
    """The MCP specs of ui_map, ui_navigate, ui_indicate and ui_clear."""
    c = load()
    p = c.protocol
    return [
        {
            "name": "ui_map",
            "description": (
                "What is on Tim's JARVIS page now: its regions and views, which is shown, the "
                "navigation commands with whether each is available and why not, and the "
                "targets you can point at (kind, key, parts). Bounded; filter by region or "
                "view. Use it before ui_navigate or ui_indicate; never guess a key."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "region": {"type": "string", "enum": p["regions"]},
                    "view": {"type": "string", "enum": sorted(c.views)},
                    "client": {"type": "string", "maxLength": p["limits"]["idMax"],
                               "description": "A page's client ID, only when no page is bound to this turn."},
                },
            },
        },
        {
            "name": "ui_navigate",
            "description": (
                "Run one registered navigation command on Tim's page (the page of this turn): "
                "select a tab, open or close a detail. The same handler his own click runs. "
                "Returns what the page actually did and its declared side effects."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "enum": sorted(c.commands)},
                    "args": {"type": "object"},
                    "client": {"type": "string", "maxLength": p["limits"]["idMax"]},
                },
                "required": ["command"],
            },
        },
        {
            "name": "ui_indicate",
            "description": (
                "Point at one exact thing on Tim's page: a white signal leaves the orb, follows "
                "the pipeline into the target and holds there, then clears by itself. reveal "
                "'navigate' first opens what is needed through registered navigation. Returns "
                "only once the signal has arrived (or why it could not). It never opens, sends "
                "or marks anything read by itself."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": _target_schema(c),
                    "reveal": {"type": "string", "enum": p["revealModes"]},
                    "style": {"type": "string", "enum": p["endpointStyles"]},
                    "holdMs": {"type": "integer", "minimum": 0, "maximum": p["timing"]["maxExtraHoldMs"],
                               "description": "Extra hold for a longer explanation; travel time never changes."},
                    "label": {"type": "string", "maxLength": p["limits"]["labelMax"]},
                    "client": {"type": "string", "maxLength": p["limits"]["idMax"]},
                },
                "required": ["target"],
            },
        },
        {
            "name": "ui_clear",
            "description": "Clear one signal you started (its cueId), or all of yours. Never stops a message's transmission.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "cueId": {"type": "string", "maxLength": p["limits"]["idMax"]},
                    "client": {"type": "string", "maxLength": p["limits"]["idMax"]},
                },
            },
        },
    ]
