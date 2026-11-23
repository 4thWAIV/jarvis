"""The deliver step of a package run (workflow.yaml deliver, approved by Tim
2026-11-19; the sender's check instructions 2026-11-20), in JARVIS's own
process after the deck passed (package_jobs.PRODUCERS):

  assemble       the deck's zip carries, under `.jarvis/briefing/`, the
                 research, the prep, the outline, the passed script, the
                 swarm's conversation and every sign-off (BRIEFING); who asked
                 (`requested_by`) is in JARVIS's signature.
  write_message  the message that carries it to Tim, written by the program
                 (no sender text): slide 1 of the passed script (its title
                 and what is on screen, package_script.opening), then the
                 question the request gave, each of its questions' text in
                 order.
  sign           with JARVIS's own key, naming who asked
                 (package_jobs.sign).
  sender_check   the finished package goes back to the sender with the
                 pre-written instructions (package_jobs.ask_check,
                 SENDER_CHECK): the job waits on its answer. Its approval
                 delivers it (server._deliver_package_run: on the Work item's
                 Messages tab, or the general Inbox with none, as a message
                 from the agent that asked); its words of what is wrong end
                 the run and start a new one with them.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from jarvis import (agent_keys, package_jobs, package_script, package_signature,
                    package_swarm as ps, step_cache)

DELIVER = "deliver"
FOLDER = "package"                       # the signed package, in the run's folder
BRIEFING = ".jarvis/briefing"
SIGNOFFS = "signoffs.json"


def _deck_noted(doc: dict) -> dict:
    noted = package_jobs.get(doc["id"]).get("deck") or {}
    if noted.get("result") != "passed":
        raise package_jobs.Blocked(DELIVER, "The deck did not pass its review.",
                                   "JARVIS reached the deliver step.", "A new run.")
    return noted


def briefing_files(run: ps.Run, review: Path) -> dict[str, bytes]:
    """What the package carries under `.jarvis/briefing/`: the research,
    the prep, the outline, the passed script, the whole conversation and
    every sign-off (the script's and the deck's, each concern's state, and
    what the sender was asked). A Blocked names any missing."""
    named = {ps.RESEARCH_MD: run.research, ps.PREP_MD: run.prep, ps.OUTLINE: run.outline,
             ps.SCRIPT: run.script, ps.CONVERSATION: run.folder / ps.CONVERSATION}
    missing = [name for name, path in named.items() if not path.is_file()]
    try:
        result = json.loads(review.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        missing.append(review.name)
        result = {}
    if missing:
        raise package_jobs.Blocked(DELIVER, "The run's " + ", ".join(missing) + " is missing.",
                                   "JARVIS assembled the package from the run's folder.",
                                   "A new run.")
    signoffs = {k: result.get(k) for k in ("concerns", "signoffs", "asked")}
    files = {f"{BRIEFING}/{name}": path.read_bytes() for name, path in named.items()}
    files[f"{BRIEFING}/{SIGNOFFS}"] = (json.dumps(signoffs, indent=2, ensure_ascii=False)
                                      + "\n").encode("utf-8")
    return files


def message_of(script: dict, request: dict) -> str:
    """The message that carries the package to Tim (workflow.yaml deliver
    write_message): slide 1 of the passed script, then the question the
    request gave, each of its questions' text in order; nothing else."""
    lines = package_script.opening(script)
    asked = [q["text"] for q in request.get("questions") or []]
    if asked:
        lines += [""] + asked
    return "\n".join(lines).strip()


def signer_fingerprint() -> str:
    """The fingerprint of JARVIS's own key, which signs every package it
    makes (package_jobs.sign; made now when it has none, as signing makes
    it)."""
    _, public = agent_keys.jarvis_key()
    return agent_keys.fingerprint(public)


def deliver(doc: dict) -> Optional[str]:
    """The deliver step of one run (package_jobs.PRODUCERS["deliver"]): the
    package assembled, its message written and signed with JARVIS's own key
    (reused from the step cache when its inputs, the built deck, the
    briefing, the message, who asked and the fingerprint of the key that
    signs it, are kept), then sent to its sender
    to check; WAITING on that check."""
    run = ps._run(doc, DELIVER)
    noted = _deck_noted(doc)
    built = Path(noted["archive"])
    folder = run.folder / FOLDER
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    briefing = briefing_files(run, Path(noted["review"]))
    message = message_of(package_script.read(run.script) or {}, doc.get("request") or {})
    unit = step_cache.Unit(DELIVER, "", {
        "deck": step_cache.file_sha(built), "name": built.name,
        "briefing": {k: step_cache.digest(v.decode("utf-8", "replace"))
                     for k, v in briefing.items()},
        "message": message, "requested_by": doc.get("requested_by"),
        # The key it is signed with: a JARVIS key made again signs again.
        "signer": signer_fingerprint(),
        "code": step_cache.code("jarvis.package_deliver", "jarvis.package_signature",
                                "jarvis.agent_keys")})

    def assemble() -> None:
        unsigned = folder / f"unsigned-{built.name}"
        shutil.copyfile(built, unsigned)
        package_signature.add_files(unsigned, briefing)
        package_jobs.sign(doc, unsigned, folder / built.name)
        unsigned.unlink()
    step_cache.run(unit, doc, assemble, folder=folder, files=[built.name])
    signed = folder / built.name
    package_jobs.noted(doc["id"], DELIVER, {"archive": str(signed), "message": message})
    package_jobs.ask_check(doc["id"], {"archive": str(signed), "folder": noted["folder"],
                                       "message": message})
    return package_jobs.WAITING
