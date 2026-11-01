"""Work packages (`jarvis/work_packages.py` and the brain's tools in
server.py): a zip unpacked into its own folder with any escaping entry
refused, opening and running gated on his own turn, a package's results
found by its `returns`, and files attached to a draft through the page's own
snapshot step. Each test has its own JARVIS_DATA_DIR (conftest); the team
server is the contract's fake, and nothing here runs xattr or osascript."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from jarvis import data_paths, messaging_files, work_packages
from jarvis.messaging_files import FilesContext, FileSnapshots
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)
from tests.test_messaging_files import MATT, SERVER, TIM, World

MANIFEST = {"name": "spanish-speech-test", "version": "1.0.0",
            "purpose": "Measure the Spanish speech model.", "skill": "SKILL.md",
            "returns": ["results/*.md", "results/**/*.wav", "results/bench.json"]}


def make_zip(files: dict, manifest=MANIFEST, modes: dict | None = None) -> bytes:
    """A zip in memory: package.json from `manifest` (unless None), then
    `files` by name, each with its Unix mode from `modes`."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        if manifest is not None:
            z.writestr("package.json", json.dumps(manifest))
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = ((modes or {}).get(name, 0o100644)) << 16
            z.writestr(info, data)
    return out.getvalue()


def write_zip(tmp_path: Path, data: bytes, name: str = "package.zip") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


@pytest.fixture
def marks(monkeypatch):
    """The quarantine mark's removal, recorded instead of run. A step's shell
    reads no BASH_ENV here, so its timing is the step's own."""
    cleared: list[Path] = []
    monkeypatch.setattr(work_packages, "unquarantine", cleared.append)
    monkeypatch.delenv("BASH_ENV", raising=False)
    return cleared


# -- unpacking --------------------------------------------------------------------------

def test_a_package_unpacks_into_its_own_folder_and_runs_as_it_was_packed(tmp_path, marks):
    archive = write_zip(tmp_path, make_zip(
        {"SKILL.md": "# Do the thing", "scripts/record.sh": "#!/bin/bash\necho hi\n"},
        modes={"scripts/record.sh": 0o100755}))
    package, fresh = work_packages.unpack(archive, "ab12cd34ef")
    assert fresh and package.id == "spanish-speech-test-1.0.0-ab12cd34"
    assert package.folder.parent == data_paths.packages_dir()
    assert package.skill_text() == "# Do the thing"
    assert package.manifest.returns == tuple(MANIFEST["returns"])
    assert os.stat(package.folder / "scripts/record.sh").st_mode & stat.S_IXUSR
    assert marks == [package.folder], "its scripts can run: the mark is off"
    again, fresh = work_packages.unpack(archive, "ab12cd34ef")
    assert not fresh and again.folder == package.folder and marks == [package.folder]
    assert [p.id for p in work_packages.every()] == [package.id]


@pytest.mark.parametrize("name", ["../escaped.txt", "results/../../escaped.txt",
                                  "/tmp/escaped.txt", "..\\escaped.txt"])
def test_an_entry_that_escapes_the_folder_refuses_the_whole_zip(tmp_path, marks, name):
    archive = write_zip(tmp_path, make_zip({"SKILL.md": "x", name: "owned"}))
    with pytest.raises(work_packages.PackageError, match="outside the package"):
        work_packages.unpack(archive, "ab12cd34")
    assert list(data_paths.packages_dir().iterdir()) == [], "nothing was written"
    assert not (data_paths.packages_dir().parent / "escaped.txt").exists() and marks == []


def test_a_link_in_the_zip_is_refused(tmp_path, marks):
    archive = write_zip(tmp_path, make_zip({"SKILL.md": "x", "link": "/etc/passwd"},
                                           modes={"link": 0o120777}))
    with pytest.raises(work_packages.PackageError, match="is a link"):
        work_packages.unpack(archive, "ab12cd34")
    assert list(data_paths.packages_dir().iterdir()) == []


@pytest.mark.parametrize("data,said", [
    (b"not a zip", "not a zip"),
    (make_zip({"SKILL.md": "x"}, manifest=None), "no package.json"),
    (make_zip({}, manifest=MANIFEST), "skill file 'SKILL.md' is not in the zip"),
    (make_zip({"SKILL.md": "x"}, manifest={**MANIFEST, "returns": "results"}),
     "returns is not a list"),
    (make_zip({"SKILL.md": "x"}, manifest={**MANIFEST, "purpose": ""}), "has no purpose"),
])
def test_what_is_not_a_package_is_said_and_nothing_is_unpacked(tmp_path, marks, data, said):
    with pytest.raises(work_packages.PackageError, match=said):
        work_packages.unpack(write_zip(tmp_path, data), "ab12cd34")
    assert list(data_paths.packages_dir().iterdir()) == []


# -- results and attachable files ---------------------------------------------------------

def test_package_results_are_the_files_its_returns_match(tmp_path, marks):
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    results = package.folder / "results"
    (results / "clips").mkdir(parents=True)
    for name in ("summary.md", "bench.json", "notes.txt", "clips/01.wav", "clips/02.wav"):
        (results / name).write_text(name)
    os.symlink("/etc/hosts", results / "link.md")
    found = [p.relative_to(package.folder.resolve()).as_posix() for p in package.results()]
    assert found == ["results/summary.md", "results/clips/01.wav", "results/clips/02.wav",
                     "results/bench.json"], "in returns order, links and others left out"


def test_a_file_may_be_attached_from_a_package_or_anywhere_he_names(tmp_path, marks):
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    assert work_packages.attachable(str(package.folder / "SKILL.md")) == \
        (package.folder / "SKILL.md").resolve()
    named = tmp_path / "made-here.zip"
    named.write_bytes(b"zip")
    assert work_packages.attachable(str(named)) == named.resolve()
    os.symlink(named, tmp_path / "via-link.zip")
    data_paths.ensure_tool_token()
    token = data_paths.tool_token_path()
    for raw, said in [("made-here.zip", "not a full path"), (str(tmp_path), "not a file"),
                      (str(tmp_path / "via-link.zip"), "not a file"),
                      (str(tmp_path / "gone.zip"), "no file"),
                      (str(token), "JARVIS's own data")]:
        with pytest.raises(work_packages.PackageError, match=said):
            work_packages.attachable(raw)


# -- running a step -------------------------------------------------------------------------

def test_a_step_runs_in_the_package_folder_with_its_exit_code_and_output(tmp_path, marks):
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    ran = asyncio.run(work_packages.run(
        package, 'pwd; echo "$JARVIS_PYTHON"; echo oops >&2; exit 3'))
    lines = ran.output.splitlines()[-3:]      # after whatever his shell prints first
    assert ran.exit_code == 3 and Path(lines[0]).resolve() == package.folder.resolve()
    assert lines[1] == sys.executable and lines[2] == "oops"
    long = asyncio.run(work_packages.run(package, "yes x | head -c 20000"))
    assert long.exit_code == 0 and len(long.output) == work_packages.OUTPUT_TAIL_CHARS


def test_a_step_that_runs_too_long_is_stopped(tmp_path, marks):
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    ran = asyncio.run(work_packages.run(package, "echo started; sleep 30", timeout=1))
    assert ran.exit_code is None and "started" in ran.output


def test_a_terminal_step_is_the_same_command_in_the_package_folder(tmp_path, marks):
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    typed = work_packages.terminal_command(package, "bash scripts/record.sh")
    assert typed.startswith(f"cd {package.folder} && export PACKAGE_DIR=")
    assert typed.endswith("&& bash scripts/record.sh")


# -- the brain's tools ----------------------------------------------------------------------

def _sent_package(tmp_path, data: bytes) -> tuple[World, UUID, UUID]:
    """Tim sends Matt a message with `data` as package.zip; Matt has it."""
    w = World()
    draft = w.tim.draft(MATT, [])
    ref = asyncio.run(w.tim.files.snapshot(SERVER, TIM, draft.message_id, "package.zip",
                                           "application/zip", _chunks(data)))
    saved = w.tim.store.save_draft(SERVER, TIM, draft.model_copy(update={"attachments": [ref]}),
                                   1)
    asyncio.run(w.tim.send(saved))
    asyncio.run(w.matt.sync())
    return w, saved.message_id, ref.attachment_id


async def _chunks(data: bytes):
    yield data


def test_open_package_downloads_unpacks_and_hands_over_the_skill(wired, tmp_path, marks):
    w, message, attachment = _sent_package(tmp_path, make_zip({"SKILL.md": "Step one."}))
    wired.server.messaging = SimpleNamespace(
        files_context=lambda: FilesContext(w.matt.files, SERVER, MATT))
    got = _call(wired, "open_package", message=str(message), attachment=str(attachment))
    assert got["ok"], got
    text = got["text"]
    folder = f"spanish-speech-test-1.0.0-{attachment.hex[:8]}"
    assert text.startswith(f"Package {folder} is unpacked in ")
    assert '"purpose": "Measure the Spanish speech model."' in text and "Step one." in text
    assert f"reply_to {message}" in text
    assert w.matt.files.files(SERVER, MATT, message)[0].state == "verified"
    assert wired.server.brain_instance.marks == ["a work package a teammate sent"]
    listed = _call(wired, "list_packages")["text"]
    assert listed.startswith("1 package unpacked here") and folder in listed
    assert "was already unpacked" in _call(wired, "open_package", message=str(message),
                                           attachment=str(attachment))["text"]


def test_open_package_says_what_is_wrong_and_opens_nothing(wired, tmp_path, marks):
    w, message, attachment = _sent_package(tmp_path, b"plain bytes")
    assert _call(wired, "open_package", message=str(message),
                 attachment=str(attachment))["text"] == wired.server.MESSAGING_OFF
    wired.server.messaging = SimpleNamespace(
        files_context=lambda: FilesContext(w.matt.files, SERVER, MATT))
    got = _call(wired, "open_package", message=str(message), attachment=str(attachment))
    assert got["text"] == "That file is not a zip. Nothing was opened."
    assert "Which file" in _call(wired, "open_package", message="x", attachment="y")["text"]
    assert list(data_paths.packages_dir().iterdir()) == []


@pytest.mark.parametrize("tool,args", [
    ("open_package", {"message": str(UUID(int=1)), "attachment": str(UUID(int=2))}),
    ("run_package_command", {"package": "spanish-speech-test", "command": "touch ran"}),
])
def test_opening_and_running_act_only_on_his_own_turn(wired, tmp_path, marks, tool, args):
    from jarvis import claude_provider, jarvis_mcp
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    refused = _call(wired, tool, origin="event", **args)
    assert not refused["ok"] and "not_allowed_from_event" in refused["text"]
    assert not (package.folder / "ran").exists()
    assert tool in wired.server.ACTING_TOOLS and tool in wired.server.TAINTING_TOOLS
    assert tool in {t["name"] for t in jarvis_mcp.TOOL_SPECS}
    assert f"mcp__jarvis__{tool}" in claude_provider.ALLOWED_TOOLS


def test_run_package_command_runs_the_step_on_his_word(wired, tmp_path, marks, monkeypatch):
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    got = _call(wired, "run_package_command", package=package.id,
                command="echo done > ran; echo finished")
    assert got["text"].startswith("The step exited with code 0.") and "finished" in got["text"]
    assert (package.folder / "ran").read_text() == "done\n"
    typed = []

    async def open_terminal(command=""):
        typed.append(command)
        return {"success": True}
    monkeypatch.setattr(wired.server.actions, "open_terminal", open_terminal)
    got = _call(wired, "run_package_command", package="spanish-speech-test",
                command="bash record.sh", terminal=True)
    assert "Terminal window" in got["text"]
    assert typed == [work_packages.terminal_command(package, "bash record.sh")]
    assert "no package 'nope'" in _call(wired, "run_package_command", package="nope",
                                        command="ls")["text"]


def test_package_results_lists_paths_for_draft_message(wired, tmp_path, marks):
    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    assert "matches its returns" in _call(wired, "package_results", package=package.id)["text"]
    (package.folder / "results").mkdir()
    (package.folder / "results" / "summary.md").write_text("# Results")
    got = _call(wired, "package_results", package=package.id)
    assert got["text"].splitlines()[1].startswith(
        str((package.folder / "results" / "summary.md").resolve()))
    assert "package_results" in wired.server.TAINT_EXEMPT_TOOLS
    assert "package_results" not in wired.server.ACTING_TOOLS


# -- sending results back: draft_message's files ------------------------------------------

@pytest.mark.asyncio
async def test_draft_message_files_go_through_the_page_s_snapshot_step(tmp_path, monkeypatch,
                                                                       marks):
    """The real runtime: a file the brain names is added by the same
    `add_to_draft` the page's upload route calls, then reviewed as usual."""
    from tests.test_messaging_signed_in import Connection, offers_messaging, runtime
    rt, _ = runtime(Connection(signed_in=True))
    offers_messaging(rt)
    assert await rt.delivery.availability() == "available"
    added = []
    real = FileSnapshots.add_to_draft

    async def spy(self, *args):
        added.append(args[3])
        return await real(self, *args)
    monkeypatch.setattr(FileSnapshots, "add_to_draft", spy)

    package, _ = work_packages.unpack(write_zip(tmp_path, make_zip({"SKILL.md": "x"})), "aa")
    (package.folder / "results").mkdir()
    summary = package.folder / "results" / "summary.md"
    summary.write_text("# Spanish\nWER 12%")
    out = await rt.tools.draft_message({"to": "Me", "text": "Results.",
                                        "files": [str(p) for p in package.results()]})
    assert "is saved with 1 file at revision 2" in out, out
    assert added == ["summary.md"], "the page's own step"
    who = rt.identity()
    draft = rt.store.draft(*who.ns, UUID(out.split()[1]))
    (ref,) = draft.attachments
    assert (ref.filename, ref.media_type, ref.sha256) == (
        "summary.md", "text/markdown", hashlib.sha256(summary.read_bytes()).hexdigest())
    reviewed = rt.reviews.review(*who.ns, draft.message_id, draft.draft_revision)
    assert reviewed.send.state == "awaiting_approval", "review takes it unchanged"

    refused = await rt.tools.draft_message({"to": "Me", "text": "x", "files": ["rel.md"]})
    assert refused == "'rel.md' is not a full path. Nothing was drafted."
    assert len(rt.store.snapshot(*who.ns, availability="available", stale=False).unsent) == 1
    await rt.stop()


def test_the_page_s_upload_route_uses_the_same_step(monkeypatch):
    from tests.test_messaging_files import test_the_page_routes
    called = []
    real = FileSnapshots.add_to_draft

    async def spy(self, *args):
        called.append(args[3])
        return await real(self, *args)
    monkeypatch.setattr(FileSnapshots, "add_to_draft", spy)
    test_the_page_routes()
    assert "über.png" in [messaging_files.wire.normalize_filename(n) for n in called]
