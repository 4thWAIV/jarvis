"""AccessRights at paths: who may read an item's data, read its content,
and write it, and which of its fields a reader cannot see (Tim's approved
UDS design, sections 3 to 5: data and content apart; the gate inside every
call; the AI reads with the rights of the person it works for).

Rights are set on a path (an item's or a folder's, which needs no item)
and inherited by everything below it; a path below can add rights, never
take one away, and a field hidden above stays hidden below. A grant names
a person (or `ANYONE`) and says `data`, `content` and `write`, with the
fields `hidden` from them. The content right covers the content, its
sections, its vectors and its full-text hits, and counts only with the
data right beside it (an item a reader cannot see has no content for
them). JARVIS has one owner, `OWNER`: Tim, whose rights are everywhere and
who alone sets rights. The brain and the runs read as Tim (an AI caller is
its person's rights, never wider).

The grants a path holds are one value in family `RIGHTS`, keyed by the
path ("/" for the root), written in a log transaction like any write and
held resident (they are few), so a check is a walk up the path's
ancestors and costs no read.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Optional

from jarvis.knowledge import encoding, paths
from jarvis.knowledge.documents import DocumentError
from jarvis.knowledge.store import RIGHTS, Store
from jarvis.knowledge.wal import Op

OWNER = "tim"
ANYONE = "*"
GRANTS_MAX = 200
HIDDEN_MAX = 200
ROOT = "/"


class Denied(DocumentError):
    """A write the caller has no right to make; answered 403. A denied read
    is never this: it looks the same as a missing item."""


@dataclass(frozen=True)
class Access:
    data: bool
    content: bool
    write: bool
    hidden: frozenset = frozenset()

    def sees_field(self, field: str) -> bool:
        """Whether a field path (`salary`, `pay.base`, `pieces[].owner`) is
        outside every hidden field."""
        return not any(is_within(field, h) for h in self.hidden)


FULL = Access(True, True, True)
NOTHING = Access(False, False, False)


def is_within(field: str, hidden: str) -> bool:
    if field == hidden:
        return True
    return field.startswith(hidden) and field[len(hidden)] in ".["


def check_person(person) -> str:
    if not isinstance(person, str) or not person.strip() or len(person) > 200:
        raise DocumentError("a caller's person is text of at most 200 characters")
    return person


def check_grants(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > GRANTS_MAX:
        raise DocumentError(f"grants are a list of at most {GRANTS_MAX}")
    out = []
    for g in raw:
        if not isinstance(g, dict):
            raise DocumentError("a grant is an object: person, data, content, write, hidden")
        unknown = set(g) - {"person", "data", "content", "write", "hidden"}
        if unknown:
            raise DocumentError(f"a grant has no {sorted(unknown)[0]}")
        grant = {"person": check_person(g.get("person"))}
        for k in ("data", "content", "write"):
            v = g.get(k, False)
            if not isinstance(v, bool):
                raise DocumentError(f"a grant's {k} is true or false")
            grant[k] = v
        hidden = g.get("hidden", [])
        if not isinstance(hidden, list) or len(hidden) > HIDDEN_MAX or \
                not all(isinstance(h, str) and h and not h.startswith("_") for h in hidden):
            raise DocumentError(f"hidden is a list of at most {HIDDEN_MAX} field names")
        grant["hidden"] = sorted(set(hidden))
        out.append(grant)
    return out


def rights_key(path: str) -> bytes:
    return path.encode()


class Rights:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.Lock()
        self.grants: dict[str, list[dict]] = {}
        for v in store.scan(RIGHTS):
            if v.value is not None:
                self.grants[v.key.decode()] = json.loads(v.value)["grants"]
        store.listeners.append(self._on_apply)

    def _on_apply(self, lsn: int, ops: list[Op]) -> None:
        with self._lock:
            for op in ops:
                if op.family != RIGHTS:
                    continue
                path = op.key.decode()
                if op.value is None:
                    self.grants.pop(path, None)
                else:
                    self.grants[path] = json.loads(op.value)["grants"]

    def access(self, person: str, path: str) -> Access:
        """The person's rights at `path`: the owner's are everywhere; anyone
        else's are the grants on the path and every folder above it."""
        if person == OWNER:
            return FULL
        if not self.grants:
            return NOTHING
        data = content = write = False
        hidden: set[str] = set()
        for at in [ROOT, *paths.ancestors(path), path]:
            for g in self.grants.get(at, ()):
                if g["person"] not in (person, ANYONE):
                    continue
                data |= g["data"]
                content |= g["content"]
                write |= g["write"]
                hidden.update(g["hidden"])
        return Access(data, content and data, write, frozenset(hidden))

    def set_ops(self, path: str, grants: list[dict], by: str, at: float) -> list[Op]:
        """The write that sets the grants on `path` (replacing its own; an
        empty list clears them). Only the owner sets rights."""
        if by != OWNER:
            raise Denied("only the owner sets rights")
        if path != ROOT:
            paths.check(path)
        grants = check_grants(grants)
        value = None if not grants else encoding.canonical({"grants": grants, "by": by, "at": at})
        return [Op(RIGHTS, rights_key(path), value)]

    def on_path(self, path: str) -> dict:
        """The grants set on `path` itself and those it inherits, for the
        owner's eyes."""
        own = self.grants.get(path, [])
        inherited = [{"at": at, **g} for at in [ROOT, *paths.ancestors(path)] if at != path
                     for g in self.grants.get(at, ())]
        return {"own": own, "inherited": inherited}


def without_hidden(data: dict, access: Access) -> dict:
    """The data with every hidden field taken out."""
    if not access.hidden:
        return data
    out = json.loads(json.dumps(data))
    for h in access.hidden:
        _drop(out, h.replace("[]", "").split("."))
    return out


def _drop(value, parts: list[str]) -> None:
    if not parts:
        return
    if isinstance(value, list):
        for v in value:
            _drop(v, parts)
        return
    if not isinstance(value, dict) or parts[0] not in value:
        return
    if len(parts) == 1:
        del value[parts[0]]
    else:
        _drop(value[parts[0]], parts[1:])


def caller_of(raw, what: str = "caller") -> dict:
    """Whose rights a call reads with: {person, agent?, model?}."""
    if not isinstance(raw, dict):
        raise DocumentError(f"a call says whose rights apply: {what}.person")
    out = {"person": check_person(raw.get("person"))}
    for k in ("agent", "model"):
        if raw.get(k) is not None:
            if not isinstance(raw[k], str) or not raw[k]:
                raise DocumentError(f"{what}.{k} is text")
            out[k] = raw[k]
    return out


def visible_field(field: str, access: Optional[Access]) -> bool:
    return access is None or access.sees_field(field)
