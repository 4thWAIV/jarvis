"""Topics and the links the owner asserts, in Markdown he can read and edit
(design section 6).

A topic is `brain_home()/topics/<slug>.md`: front matter, then his words.
The front matter holds `name`, `status` (`implemented`, `active`,
`planned`, `retired`, `unknown`), `pinned`, `aliases` in two classes
(`exact` and `ambiguous`), and the links: `relates_to`, `depends_on`,
`stage_of`, `precedes` and `same_as` (other topics by slug), `about_sources`
(documents he accepted as about this topic, by their source reference) and
`not_linked` (proposals he turned down, so the linker never makes them
again).

These links are the only knowledge that cannot be derived, so they live
here and never only in the engine (I1); the engine follows the files.

This module is the one writer. Every change reads the file, changes the
front matter, and replaces the file atomically (write temp, fsync,
rename) under one lock, refusing when the file moved on since it was read,
so an edit he made by hand is never overwritten. The page's routes and the
brain's acting tools call the same functions.

Front matter is a small fixed shape, read and written here without a YAML
dependency: `key: value`, `key: [a, b]`, and one level of nesting for
`aliases`.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from jarvis import data_paths, jarvis_memory, spoken_words
from jarvis.knowledge.oscompat import O_BINARY, O_NOFOLLOW

STATUSES = ("implemented", "active", "planned", "retired", "unknown")
LINK_KINDS = ("relates_to", "depends_on", "stage_of", "precedes", "same_as")
LISTS = LINK_KINDS + ("about_sources", "not_linked")
ORDER = ("name", "status", "pinned", "aliases") + LISTS
NAME_MAX = 120
ALIAS_MAX = 40
BODY_MAX = 64 << 10

_LOCK = threading.Lock()
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")


class TopicError(ValueError):
    """A change the topic files refuse, in words to show."""


def topics_dir() -> Path:
    return data_paths.brain_home() / "topics"


def check_slug(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
        raise TopicError(f"not a topic: {slug!r}")
    return slug


def topic_path(slug: str) -> Path:
    return topics_dir() / f"{check_slug(slug)}.md"


# --- the front matter -----------------------------------------------------------

def _scalar(text: str):
    text = text.strip()
    if text in ("true", "false"):
        return text == "true"
    if text.startswith('"'):
        try:
            return json.loads(text)
        except ValueError:
            return text.strip('"')
    return text


def _split_list(inner: str) -> list:
    items, buf, quoted, escape = [], "", False, False
    for ch in inner:
        if escape:
            buf += ch
            escape = False
        elif ch == "\\" and quoted:
            buf += ch
            escape = True
        elif ch == '"':
            quoted = not quoted
            buf += ch
        elif ch == "," and not quoted:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf)
    return [_scalar(i) for i in items if i.strip()]


def _value(text: str):
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        return _split_list(text[1:-1])
    return _scalar(text)


def parse(text: str) -> tuple[dict, str]:
    """(front matter, body). A file without front matter is all body."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return {}, text
    front: dict = {}
    parent: Optional[str] = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw.startswith((" ", "\t"))
        key, sep, rest = raw.strip().partition(":")
        if not sep:
            continue
        key = key.strip()
        if indented and parent is not None:
            front[parent][key] = _value(rest)
        elif rest.strip():
            front[key] = _value(rest)
            parent = None
        else:
            front[key] = {}
            parent = key
    return front, "\n".join(lines[end + 1:]).lstrip("\n")


def _emit(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_emit(v) for v in value) + "]"
    text = str(value)
    if not text or text != text.strip() or any(c in text for c in ',[]:#"{}') or text in ("true", "false"):
        return json.dumps(text, ensure_ascii=False)
    return text


def render(front: dict, body: str) -> str:
    out = ["---"]
    keys = [k for k in ORDER if k in front] + sorted(k for k in front if k not in ORDER)
    for key in keys:
        value = front[key]
        if isinstance(value, dict):
            out.append(f"{key}:")
            for sub in sorted(value):
                out.append(f"  {sub}: {_emit(value[sub])}")
        else:
            out.append(f"{key}: {_emit(value)}")
    out.append("---")
    return "\n".join(out) + "\n" + (body if body.endswith("\n") or not body else body + "\n")


# --- reading ---------------------------------------------------------------------

@dataclass
class Topic:
    slug: str
    front: dict
    body: str
    text: str            # the whole file as read, for expect-text

    @property
    def name(self) -> str:
        return str(self.front.get("name") or self.slug)

    @property
    def status(self) -> str:
        s = self.front.get("status")
        return s if s in STATUSES else "unknown"

    @property
    def pinned(self) -> bool:
        return self.front.get("pinned") is True

    def aliases(self, cls: str) -> list[str]:
        found = (self.front.get("aliases") or {}).get(cls) or []
        return [str(a) for a in found] if isinstance(found, list) else []

    def links(self, kind: str) -> list[str]:
        found = self.front.get(kind) or []
        return [str(x) for x in found] if isinstance(found, list) else []

    def json(self) -> dict:
        return {"slug": self.slug, "name": self.name, "status": self.status, "pinned": self.pinned,
                "aliases": {"exact": self.aliases("exact"), "ambiguous": self.aliases("ambiguous")},
                **{k: self.links(k) for k in LISTS}}


def _read_text(path: Path) -> Optional[str]:
    try:
        fd = os.open(str(path), os.O_RDONLY | O_NOFOLLOW | O_BINARY)
    except OSError:
        return None
    try:
        if not (os.fstat(fd).st_mode & 0o170000 == 0o100000):
            return None
        return os.read(fd, BODY_MAX * 2).decode("utf-8", "replace")
    finally:
        os.close(fd)


def read(slug: str) -> Optional[Topic]:
    text = _read_text(topic_path(slug))
    if text is None:
        return None
    front, body = parse(text)
    return Topic(slug, front, body, text)


def topics() -> list[Topic]:
    base = topics_dir()
    if not base.is_dir() or base.is_symlink():
        return []
    out = []
    for path in sorted(base.glob("*.md")):
        slug = path.stem
        if path.is_symlink() or not _SLUG.fullmatch(slug):
            continue
        found = read(slug)
        if found is not None:
            out.append(found)
    return out


# --- writing -----------------------------------------------------------------------

def _write(path: Path, text: str, expect: Optional[str]) -> None:
    """Replace `path` with `text`, only if it still says `expect` (None: it
    must not exist)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _read_text(path)
    if now != expect:
        raise TopicError("the topic file changed since it was read; read it again")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _change(slug: str, change, expect: Optional[str] = None) -> Topic:
    with _LOCK:
        topic = read(slug)
        if topic is None:
            raise TopicError(f"there is no topic {slug!r}")
        if expect is not None and expect != topic.text:
            raise TopicError("the topic file changed since it was read; read it again")
        front = json.loads(json.dumps(topic.front))
        change(front)
        _write(topic_path(slug), render(front, topic.body), topic.text)
        return read(slug)


def _clean_name(name: str, what: str, limit: int) -> str:
    name = " ".join(str(name or "").split())
    if not name or len(name) > limit:
        raise TopicError(f"{what} is 1 to {limit} characters")
    return name


def create_topic(name: str, *, status: str = "unknown", exact: Optional[list[str]] = None,
                 ambiguous: Optional[list[str]] = None, pinned: bool = False, body: str = "") -> Topic:
    name = _clean_name(name, "a topic's name", NAME_MAX)
    if status not in STATUSES:
        raise TopicError(f"status is one of {', '.join(STATUSES)}")
    slug = jarvis_memory.slugify(name)
    check_slug(slug)
    if len(body) > BODY_MAX:
        raise TopicError("the topic's words are too long")
    front = {"name": name, "status": status, "pinned": bool(pinned),
             "aliases": {"exact": [_clean_name(a, "an alias", ALIAS_MAX) for a in (exact or [name])],
                         "ambiguous": [_clean_name(a, "an alias", ALIAS_MAX) for a in (ambiguous or [])]}}
    with _LOCK:
        if read(slug) is not None:
            raise TopicError(f"there is already a topic {slug!r}")
        _write(topic_path(slug), render(front, body or f"# {name}\n"), None)
        return read(slug)


def change_topic(slug: str, *, status: Optional[str] = None, pinned: Optional[bool] = None,
                 add_aliases: Optional[dict] = None, remove_aliases: Optional[list[str]] = None,
                 expect: Optional[str] = None) -> Topic:
    if status is not None and status not in STATUSES:
        raise TopicError(f"status is one of {', '.join(STATUSES)}")

    def change(front: dict) -> None:
        if status is not None:
            front["status"] = status
        if pinned is not None:
            front["pinned"] = bool(pinned)
        aliases = front.setdefault("aliases", {})
        if not isinstance(aliases, dict):
            aliases = front["aliases"] = {}
        for cls, names in (add_aliases or {}).items():
            if cls not in ("exact", "ambiguous"):
                raise TopicError("an alias is exact or ambiguous")
            have = list(aliases.get(cls) or [])
            for n in names:
                n = _clean_name(n, "an alias", ALIAS_MAX)
                if n not in have:
                    have.append(n)
            aliases[cls] = have
        gone = {spoken_words.fold(n).lower() for n in (remove_aliases or [])}
        for cls in ("exact", "ambiguous"):
            if cls in aliases:
                aliases[cls] = [a for a in aliases[cls] if spoken_words.fold(str(a)).lower() not in gone]
    return _change(slug, change, expect)


def link(slug: str, kind: str, target: str) -> Topic:
    """Assert `slug kind target`: another topic for the link kinds, a
    document's source reference for `about_sources`."""
    if kind not in LINK_KINDS + ("about_sources",):
        raise TopicError(f"a link is one of {', '.join(LINK_KINDS + ('about_sources',))}")
    if kind in LINK_KINDS:
        check_slug(target)
        if target == slug:
            raise TopicError("a topic does not link to itself")
        if read(target) is None:
            raise TopicError(f"there is no topic {target!r}")

    def change(front: dict) -> None:
        have = [str(x) for x in front.get(kind) or []]
        if target not in have:
            have.append(target)
        front[kind] = have
        front["not_linked"] = [x for x in front.get("not_linked") or [] if x != target]
    return _change(slug, change)


def unlink(slug: str, kind: str, target: str) -> Topic:
    if kind not in LISTS:
        raise TopicError(f"a link is one of {', '.join(LISTS)}")

    def change(front: dict) -> None:
        front[kind] = [x for x in front.get(kind) or [] if str(x) != target]
    return _change(slug, change)


def turn_down(slug: str, target: str) -> Topic:
    """A proposal he said no to: written down so it is never made again."""
    def change(front: dict) -> None:
        have = [str(x) for x in front.get("not_linked") or []]
        if target not in have:
            have.append(target)
        front["not_linked"] = have
    return _change(slug, change)


# --- the engine's settings note: his say over its extra indexes ---------------------
#
# brain_home()/knowledge.md holds, in front matter, the extra indexes he
# pinned (kept even when unused) and forbade (never made, dropped if there),
# each by its shape's key, `<kinds>|<properties>` (advisor.index_key). They
# are his choices, so they live here and a rebuild of the engine keeps them
# (design 7.17.3); ingest hands them to the engine on every pass.

INDEX_CHOICES = ("pin", "forbid", "clear")
_INDEX_KEY = re.compile(r"[a-z_]+(,[a-z_]+)*\|[A-Za-z0-9_.\[\]]+(,[A-Za-z0-9_.\[\]]+)*")
SETTINGS_BODY = ("# Knowledge engine settings\n\nThe extra indexes you pinned and forbade. "
                 "JARVIS writes this file when you choose on the Knowledge view or say so; "
                 "you may edit it too.\n")


def settings_path() -> Path:
    return data_paths.brain_home() / "knowledge.md"


def check_index_key(key: str) -> str:
    if not isinstance(key, str) or len(key) > 256 or not _INDEX_KEY.fullmatch(key):
        raise TopicError(f"not an index: {key!r}")
    return key


def index_choices() -> dict:
    text = _read_text(settings_path())
    front, _ = parse(text or "")
    def keys(name):
        found = front.get(name) or []
        return sorted({str(k) for k in found if isinstance(k, str) and _INDEX_KEY.fullmatch(str(k))}) \
            if isinstance(found, list) else []
    return {"pinned": keys("pinned_indexes"), "forbidden": keys("forbidden_indexes")}


def choose_index(key: str, choice: str) -> dict:
    """Pin, forbid or clear one index by its key; returns every choice."""
    check_index_key(key)
    if choice not in INDEX_CHOICES:
        raise TopicError(f"the choice is {', '.join(INDEX_CHOICES)}")
    with _LOCK:
        path = settings_path()
        text = _read_text(path)
        front, body = parse(text or "")
        pinned = [k for k in front.get("pinned_indexes") or [] if k != key]
        forbidden = [k for k in front.get("forbidden_indexes") or [] if k != key]
        if choice == "pin":
            pinned.append(key)
        elif choice == "forbid":
            forbidden.append(key)
        front["pinned_indexes"] = sorted(pinned)
        front["forbidden_indexes"] = sorted(forbidden)
        _write(path, render(front, body or SETTINGS_BODY), text)
    return index_choices()


def declined_seeds() -> list[str]:
    front, _ = parse(_read_text(settings_path()) or "")
    found = front.get("declined_seeds") or []
    return sorted({str(x) for x in found}) if isinstance(found, list) else []


def decline_seed(seed_id: str) -> list[str]:
    """A seed he said no to, written down so it is never offered again."""
    check_slug(seed_id)
    with _LOCK:
        path = settings_path()
        text = _read_text(path)
        front, body = parse(text or "")
        declined = sorted({str(x) for x in front.get("declined_seeds") or []} | {seed_id})
        front["declined_seeds"] = declined
        _write(path, render(front, body or SETTINGS_BODY), text)
    return declined_seeds()
