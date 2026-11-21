"""The deliver step of a package run (workflow.yaml deliver, approved by Tim
2026-11-19; the sender's check instructions 2026-11-20), in JARVIS's own
process after the deck passed (package_jobs.PRODUCERS):

  assemble       the deck's zip carries, under `.jarvis/briefing/`, the
                 research, the prep, the passed script, the swarm's
                 conversation and its votes (BRIEFING); who asked
                 (`requested_by`) is in JARVIS's signature.
  write_message  the message that carries it to Tim, written by the program
                 from the passed deck's opening slide (its title and its
                 words, in reading order) and its questions; no sender text.
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

import yaml

from jarvis import package_jobs, package_signature, package_swarm as ps

DELIVER = "deliver"
FOLDER = "package"                       # the signed package, in the run's folder
BRIEFING = ".jarvis/briefing"
VOTES = "votes.json"


def _deck_noted(doc: dict) -> dict:
    noted = package_jobs.get(doc["id"]).get("deck") or {}
    if noted.get("result") != "passed":
        raise package_jobs.Blocked(DELIVER, "The deck did not pass its review.",
                                   "JARVIS reached the deliver step.", "A new run.")
    return noted


def briefing_files(run: ps.Run, review: Path) -> dict[str, bytes]:
    """What the package carries under `.jarvis/briefing/`: the research,
    the prep, the passed script, the whole conversation and every vote
    (the script's and the deck's, with each question's state and the
    judgements whether a change reopened one). A Blocked names any missing."""
    named = {ps.RESEARCH_MD: run.research, ps.PREP_MD: run.prep, ps.SCRIPT_MD: run.script,
             ps.CONVERSATION: run.folder / ps.CONVERSATION}
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
    votes = {k: result.get(k) for k in ("questions", "votes", "reopenings")}
    files = {f"{BRIEFING}/{name}": path.read_bytes() for name, path in named.items()}
    files[f"{BRIEFING}/{VOTES}"] = (json.dumps(votes, indent=2, ensure_ascii=False) + "\n"
                                   ).encode("utf-8")
    return files


def _words(obj: dict) -> list[str]:
    return ["".join(r["text"] for r in p.get("runs") or []) for p in obj.get("paragraphs") or []]


def message_of(source: Path) -> str:
    """The message that carries the package to Tim (workflow.yaml deliver
    write_message): the passed deck's opening slide, its title and its
    words in reading order, then each question it asks him; nothing else."""
    deck = yaml.safe_load(source.read_text(encoding="utf-8"))
    opening = next(s for s in deck["slides"] if s.get("type") == "content")
    objects = {o["id"]: o for o in opening.get("objects") or []}
    lines = [opening["title"], ""]
    for ident in opening.get("readingOrder") or []:
        if objects.get(ident, {}).get("type") == "text":
            lines += _words(objects[ident])
    asked = [d["question"] for d in deck.get("decisions") or []]
    if asked:
        lines += [""] + asked
    return "\n".join(lines).strip()


def deliver(doc: dict) -> Optional[str]:
    """The deliver step of one run (package_jobs.PRODUCERS["deliver"]): the
    package assembled, its message written, signed with JARVIS's own key
    and sent to its sender to check; WAITING on that check."""
    run = ps._run(doc, DELIVER)
    noted = _deck_noted(doc)
    built = Path(noted["archive"])
    folder = run.folder / FOLDER
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    unsigned = folder / f"unsigned-{built.name}"
    shutil.copyfile(built, unsigned)
    package_signature.add_files(unsigned, briefing_files(run, Path(noted["review"])))
    signed = folder / built.name
    package_jobs.sign(doc, unsigned, signed)
    unsigned.unlink()
    message = message_of(Path(noted["source"]))
    package_jobs.noted(doc["id"], DELIVER, {"archive": str(signed), "message": message})
    package_jobs.ask_check(doc["id"], {"archive": str(signed), "folder": noted["folder"],
                                       "message": message})
    return package_jobs.WAITING
