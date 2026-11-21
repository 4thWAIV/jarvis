"""A role's read-only file tools, on its own tool server (role_mcp.py).

Tim, 2026-11-20: roles get only the MCP tools they need, written by us,
never exec or a shell, and the same on both brains. A role that reads files
(roles.FILE_READ_TOOLS: the author, the editor, the context owner and the
structure and decision owner) reads them here, on Claude in place of the built-in
Read, Grep and Glob and on Codex in place of its shell:

    list_package_files    one page of a folder
    read_package_file     one window of a file, its lines numbered
    search_package_files  the lines holding some words, in the files under a folder

Only inside the folders the role's server was started with (`--read`: its
run folder and the folders its run gives it, the package's evidence and,
for the author, the Work item's research), through folder_readers: every
read starts at one of those folders, walks down without following a link
and is bounded (a page of PAGE_ENTRIES, a window of TEXT_MAX_LINES lines
and TEXT_MAX_BYTES bytes). A path is absolute, or relative to the first
folder (the run folder). Hidden, credential and key files are never shown
(repo_read.sensitive_reason, the folder catalog's rule), so the server's
own configuration beside the run (`.jarvis-role/`) is not either.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from jarvis import folder_readers, repo_read, roles
from jarvis.folder_contract import ERRORS, FolderError, PAGE_ENTRIES, SCAN_MAX_ENTRIES

NAMES: tuple[str, ...] = roles.FILE_READ_TOOLS
LIST, READ, SEARCH = NAMES

_PATH = {"type": "string",
         "description": "An absolute path inside the folders you were given, or one relative "
                        "to your run folder."}

SPECS: list[dict] = [
    {"name": LIST,
     "description": ("List one folder you were given, or a folder inside it: its folders, then "
                     "its files, with their sizes. Read only. Give `after` from the answer "
                     "before for the next page."),
     "inputSchema": {"type": "object", "properties": {
         "path": _PATH,
         "after": {"type": "string", "description": "The `next` of the page before."}},
         "required": [], "additionalProperties": False}},
    {"name": READ,
     "description": ("Read a text file inside the folders you were given, a window at a time, "
                     "each line numbered. Read only. Give `offset` from the answer before for "
                     "the next window."),
     "inputSchema": {"type": "object", "properties": {
         "path": _PATH,
         "offset": {"type": "integer", "minimum": 0,
                    "description": "Where to start, the `next_offset` of the window before."},
         "line": {"type": "integer", "minimum": 1,
                  "description": "The number of the line `offset` starts, from the answer "
                                 "before."}},
         "required": ["path"], "additionalProperties": False}},
    {"name": SEARCH,
     "description": ("Find the lines holding these words (not case sensitive) in the text files "
                     "inside a folder you were given, every folder below it included. Read "
                     "only."),
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string", "description": "The words to find, as they are written."},
         "path": _PATH},
         "required": ["text"], "additionalProperties": False}},
]


class Refused(ValueError):
    """A path outside the role's folders, or one it may not read."""


def _visible(relative: Path, _is_dir: bool) -> bool:
    return repo_read.sensitive_reason(relative) is None


class Files:
    """The file tools over these folders, the first the run folder."""

    def __init__(self, folders: list[Path]):
        self.folders = [Path(os.path.abspath(f)) for f in folders]

    def _place(self, path: Optional[str]) -> tuple[Path, Path]:
        """(the folder it is in, its path relative to it)."""
        if not self.folders:
            raise Refused("You were given no folders to read.")
        raw = Path(path) if path else self.folders[0]
        absolute = Path(os.path.normpath(raw if raw.is_absolute() else self.folders[0] / raw))
        for root in self.folders:
            try:
                relative = absolute.relative_to(root)
            except ValueError:
                continue
            if relative.parts and not _visible(relative, False):
                raise Refused(f"{absolute} may not be read.")
            return root, relative
        raise Refused(f"{absolute} is outside the folders you were given: "
                      + ", ".join(str(f) for f in self.folders))

    def list(self, path: Optional[str] = None, after: Optional[str] = None) -> dict:
        root, relative = self._place(path)
        key = json.loads(after) if after else None
        found = folder_readers.list_dir(root, relative, _visible, after=key)
        return {"folder": str(root / relative),
                "entries": [{"name": e.name, "kind": e.kind, "size": e.size}
                            for e in found.entries],
                "next": json.dumps(found.last_key) if found.more else None,
                "partial": found.partial}

    def read(self, path: str, offset: int = 0, line: int = 1) -> dict:
        root, relative = self._place(path)
        window = folder_readers.read_text(root, relative, offset, first_line=line)
        if window.kind == "binary":
            return {"file": str(root / relative), "binary": True, "size": window.size}
        return {"file": str(root / relative), "size": window.size,
                "lines": [f"{line + i}\t{text}" for i, text in enumerate(window.lines)],
                "next_offset": window.next_offset,
                "next_line": line + window.newlines if window.next_offset is not None else None}

    def search(self, text: str, path: Optional[str] = None) -> dict:
        """Every line holding `text`, file by file, until PAGE_ENTRIES lines
        are found or SCAN_MAX_ENTRIES entries looked at (then `partial`)."""
        root, relative = self._place(path)
        wanted = text.casefold()
        hits: list[str] = []
        looked = 0
        partial = False
        folders = [relative]
        while folders and not partial:
            folder = folders.pop(0)
            after = None
            while True:
                page = folder_readers.list_dir(root, folder, _visible, after=after)
                for entry in page.entries:
                    looked += 1
                    if looked > SCAN_MAX_ENTRIES:
                        partial = True
                        break
                    if entry.kind == "dir":
                        folders.append(folder / entry.name)
                    elif entry.kind == "file":
                        partial = self._search_file(root, folder / entry.name, wanted, hits)
                        if partial:
                            break
                partial = partial or page.partial
                if partial or not page.more:
                    break
                after = page.last_key
        return {"folder": str(root / relative), "text": text, "lines": hits, "partial": partial}

    @staticmethod
    def _search_file(root: Path, relative: Path, wanted: str, hits: list[str]) -> bool:
        """The file's lines holding the words added to `hits`; True once
        PAGE_ENTRIES are found."""
        offset, line = 0, 1
        while offset is not None:
            try:
                window = folder_readers.read_text(root, relative, offset, first_line=line)
            except FolderError:
                return False
            if window.kind == "binary":
                return False
            for i, words in enumerate(window.lines):
                if wanted in words.casefold():
                    hits.append(f"{root / relative}:{line + i}: {words}")
                    if len(hits) >= PAGE_ENTRIES:
                        return True
            offset, line = window.next_offset, line + window.newlines
        return False


def call(files: Files, tool: str, arguments: dict) -> tuple[bool, str]:
    """One file tool's answer, as JSON text, or why it was refused."""
    try:
        if tool == LIST:
            body = files.list(arguments.get("path"), arguments.get("after"))
        elif tool == READ:
            body = files.read(str(arguments.get("path") or ""), int(arguments.get("offset") or 0),
                              int(arguments.get("line") or 1))
        elif tool == SEARCH:
            if not str(arguments.get("text") or "").strip():
                return False, "Give the words to find."
            body = files.search(str(arguments["text"]), arguments.get("path"))
        else:
            return False, f"No file tool called {tool!r}."
    except FolderError as e:
        return False, ERRORS[e.code][1]
    except (Refused, ValueError, TypeError) as e:
        return False, str(e) or type(e).__name__
    return True, json.dumps(body, ensure_ascii=False)
