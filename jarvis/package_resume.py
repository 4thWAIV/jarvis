"""A package's next run goes on from where the run before it failed (Tim
2026-11-22, construction-drafts.md "Found on the first full run": only what
failed is run again; every earlier success is reused from what was kept).

A run fails at a step: its swarm or its deck review ended with findings
for the sender (package_jobs.failed_swarm), or it stopped in Error
(package_jobs.error, whose `step` says where). The next run of the package
(package_jobs.new_run with `resume`) then starts at that step and, within
the swarm or the deck, at the stage that failed:

- every step before it (the run before reached it only once they had
  finished) is done for the new run too (`done`), its outcome kept on the new job (the swarm's result for
  the deck; the deck's for the deliver step); gather and mark are once a
  package (package_evidence, package_mark) and reuse themselves;
- what the run before kept is copied into the new run's folder
  (`KEPT_FILES`, and the deck's source folder for the deck and deliver
  steps): its research and prep, its search log, its outline and script,
  its conversation, and `swarm.json` with each participant's session;
- `swarm.json`'s flow is set back to the stage that failed: a swarm or a
  deck review that ended after its fix passes starts its fix passes again
  (`pass`, `deck-pass`) with the script or the deck as they stand, the deck
  built again and reviewed again with JARVIS's layout as it is now
  (`review_again`, package_deck); a stage that never finished (the outline,
  the fill, the deck's writing) is that stage again;
- the program says into the conversation that this run goes on from the
  run before at that step, then the findings the run before ended with and
  the sender's answers to them, word for word, as the sender's;
- a Codex participant's thread is resumed where it was; a Claude Code
  session lives in its own folder, so a Claude participant starts a new
  session and is given the whole conversation as it stands;
- the new job records it all as `reused` (from which run, at which step and
  stage, which steps, which files, which sessions were started anew).

A run whose sender said what is wrong at its check, or that Tim rejected,
is revised, never started over (`revise`, Tim 2026-11-22: never throw away
what worked). Its next run keeps the run before's research, prep, outline,
script, conversation, swarm.json with each participant's session, and the
deck's source, and starts at the swarm's revision (package_swarm
`revise-outline`, `revise-script`):

- the program says into the conversation that this run revises the run
  before, then the words, as the sender's (`sender`) or Tim's (`tim`),
  word for word;
- the author revises the outline and then the script with them, naming
  the slides it changes; the program finds which slides changed by
  comparing the script with the one before (`changed_slides`), and only
  those are marked again;
- research is done again only where the words say something is missing:
  the author gets it with its tools in that turn, adding to research.md;
  the research step is done (`done`) and never run again;
- the owners and the editor re-check only the slides that changed, in the
  swarm's fix passes (`flow["slides"]`);
- the deck step revises the deck source the run before kept
  (`deck-revise`): the author changes the deck slides of the changed script
  slides and what the words name, and only those deck slides (the ones that
  changed, and those whose script slide changed) go into the review; the
  rest of the deck source stays as it was;
- the job records what was reused (`reused`, with `revision`) and, as each
  step finishes, what was redone (`revision`: the slides changed, those
  kept, whether research was done again and how many searches it made).

What the run before had is kept in `revision-before/` (BEFORE) to compare
with. A run before with no passed script to revise (it never passed its
swarm, or its files are gone) is started from research instead, with its
research to start from (package_swarm._carry_earlier), and the job says so.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from jarvis import package_jobs as pj

# What a run keeps in its folder that a later run of the package reuses.
KEPT_FILES = ("research.md", "prep.md", "research-log.jsonl", "research-used.json",
              "research-searches.md", "outline.yaml", "script.yaml", "swarm.json",
              "swarm-result.json", "conversation.md")
DECK_FOLDER = "deck"            # package_deck.FOLDER: the deck's source and pictures
STATE = "swarm.json"            # package_swarm.STATE
# Stages of a pass of fix passes (package_swarm.fix_passes): a swarm or deck
# review that failed in them starts its passes again.
PASS_STAGES = ("pass", "asked-after-pass", "recheck", "asked-after-recheck")
# Steps a later run never takes over from the run before: once a package,
# they reuse themselves.
ONCE_A_PACKAGE = ("gather", "mark")
# A revision (a change at the sender's check, or Tim's Reject): who said
# what is wrong, as the conversation names them; what the run before had,
# kept to compare with; the stages a revision starts at.
SENDER, TIM = "sender", "tim"
BEFORE = "revision-before"
OUTLINE, SCRIPT, DECK_SOURCE = "outline.yaml", "script.yaml", "presentation.yaml"
REVISE_STAGE, DECK_REVISE_STAGE = "revise-outline", "deck-revise"


def failed_at(before: dict) -> Optional[str]:
    """The step the run failed at, or None when it did not fail at one
    (delivered, rejected, its check said what is wrong, still going)."""
    if before["state"] == pj.ERROR:
        step = (before.get("error") or {}).get("step") or before.get("step")
    elif before.get("rounds") and pj.kind_of(before["rounds"][-1]) == pj.FINDINGS and \
            before["state"] in (pj.FAILED, pj.WAITING):
        # The step whose findings they were: the deck's review when the
        # deck's outcome is kept, else the swarm's.
        step = before.get("step") or ("deck" if before.get("deck") else
                                      "swarm" if before.get("swarm") else None)
    else:
        return None
    return step if step in pj.STEPS else None


def _stage(flow: Optional[dict], prefix: str, first: str) -> dict:
    """The flow set back to the stage that failed (see the module's words)."""
    flow = dict(flow or {"stage": first, "pass": 1})
    stage = str(flow.get("stage") or first)
    if stage.removeprefix(prefix) in PASS_STAGES:
        return {"stage": f"{prefix}pass", "pass": 1}
    flow.pop("left", None)
    return flow


def _answers_of(before: dict) -> str:
    """The findings the run before ended with and the sender's answers,
    word for word, as the swarm said the sender's answers before
    (package_swarm._take_answers)."""
    from jarvis import sessions
    last = (before.get("rounds") or [None])[-1]
    if not last or pj.kind_of(last) != pj.FINDINGS or not last.get("answers"):
        return ""
    asked = {q["id"]: q["question"] for q in last.get("questions") or []}
    return "\n".join(sessions._tag("answer", v, id=k, question=asked.get(k, ""))
                     for k, v in last["answers"].items())


def _rewritten(value, old: str, new: str):
    """A kept outcome's paths moved from the run before's folder to this one."""
    if isinstance(value, str):
        return new + value[len(old):] if value.startswith(old + "/") else value
    if isinstance(value, dict):
        return {k: _rewritten(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewritten(v, old, new) for v in value]
    return value


def carry(before: dict, doc: dict, step: str) -> dict:
    """What the new run `doc` reuses of `before`, which failed at `step`:
    the files copied into its folder, swarm.json set to go on, and what the
    job records (`reused`, `done`, and the outcomes of the steps reused)."""
    from jarvis import sessions
    source, target = Path(before["folder"]), Path(doc["folder"])
    target.mkdir(parents=True, exist_ok=True)
    at = pj.STEPS.index(step)
    # A run reaches a step only once every step before it has finished.
    steps = [s for s in pj.STEPS[:at] if s not in ONCE_A_PACKAGE]
    files: list[str] = []
    fresh: list[str] = []
    stage = None
    if step not in ONCE_A_PACKAGE and step != "research":
        files = _copy_kept(source, target, step in ("deck", "deliver"))
        kept_path = target / STATE
        if kept_path.is_file():
            kept = json.loads(kept_path.read_text(encoding="utf-8"))
            if step == "swarm":
                kept["flow"] = _stage(kept.get("flow"), "", "outline")
                stage = kept["flow"]["stage"]
            elif step == "deck":
                kept["deck_flow"] = _stage(kept.get("deck_flow"), "deck-", "deck-write")
                if kept["deck_flow"]["stage"] != "deck-write":
                    kept["deck_flow"]["review_again"] = True
                kept.setdefault("deck", {"builds": 0}).update(problems=[], unfit=False)
                stage = kept["deck_flow"]["stage"]
            fresh = _fresh_sessions(kept)
            saved = kept["swarm"]
            note = (f"This is {doc['run']} of the package. It goes on from {before['run']} "
                    f"at the {step} step" + (f" ({stage})" if stage else "") + ": what "
                    f"{before['run']} finished before it is kept and not done again.")
            said = _answers_of(before)
            conversation = saved.setdefault("conversation", [])
            conversation.append({"from": sessions.PROGRAM, "text": note + (
                " The findings it ended with and the sender's answers follow." if said else "")})
            if said:
                conversation.append({"from": "sender", "text": said})
            kept_path.write_text(json.dumps(kept, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    outcomes = {}
    if "swarm" in steps and before.get("swarm"):
        outcomes["swarm"] = _rewritten(before["swarm"], str(source), str(target))
    if "deck" in steps and before.get("deck"):
        outcomes["deck"] = before["deck"]       # the built deck, referenced where it is
    return {"reused": {"from": before["id"], "run": before["run"], "step": step, "stage": stage,
                       "steps": steps, "files": files, "fresh_sessions": fresh},
            "done": steps, **outcomes}


def _copy_kept(source: Path, target: Path, deck: bool) -> list[str]:
    """What the run before kept (KEPT_FILES, and with `deck` its deck's
    source folder), copied into the new run's folder; the names copied."""
    files = []
    for name in KEPT_FILES:
        if (source / name).is_file():
            shutil.copyfile(source / name, target / name)
            files.append(name)
    if deck and (source / DECK_FOLDER).is_dir():
        shutil.copytree(source / DECK_FOLDER, target / DECK_FOLDER, dirs_exist_ok=True)
        files.append(DECK_FOLDER + "/")
    return files


def _fresh_sessions(kept: dict) -> list[str]:
    """swarm.json made ready for a new run: no round of the new job is
    answered yet, and a Claude participant starts a new session (Claude
    Code finds a session only in its own folder), given the whole
    conversation; the names started anew."""
    asking = kept.get("asking") or {}
    kept["asking"] = {"answers_said": 0, "groups": asking.get("groups") or []}
    saved = kept.setdefault("swarm", {})
    seen, introduced = saved.setdefault("seen", {}), set(saved.get("introduced") or [])
    fresh = []
    for name, had in list((kept.get("participants") or {}).items()):
        if had.get("engine") == "claude":
            del kept["participants"][name]
            seen[name] = 0
            introduced.discard(name)
            fresh.append(name)
    saved["introduced"] = sorted(introduced)
    return fresh


def revisable(before: dict) -> bool:
    """Whether the run before has work to revise: a script that passed its
    swarm, with its script and swarm.json still in its folder."""
    folder = Path(before.get("folder") or "")
    return ((before.get("swarm") or {}).get("result") == "passed"
            and (folder / SCRIPT).is_file() and (folder / STATE).is_file())


def words_said(words: str, by: str) -> str:
    """The words of what is wrong as they are said into the conversation:
    the sender's as its answer to its check, word for word; Tim's as they
    are."""
    if by == SENDER:
        from jarvis import sessions
        return sessions._tag("answer", words, id=pj.WHAT_IS_WRONG, question=pj.SENDER_CHECK)
    return words


def revise(before: dict, doc: dict, words: str, by: str) -> dict:
    """What the new run `doc` keeps of `before`, whose sender said what is
    wrong at its check (`by` SENDER) or that Tim rejected (`by` TIM), with
    `words`: everything it made, copied into the new run's folder; what it
    had, kept apart in BEFORE; swarm.json set to the swarm's revision and
    the deck's; the words said into the conversation; and what the job
    records (`reused`, `done`)."""
    from jarvis import sessions
    source, target = Path(before["folder"]), Path(doc["folder"])
    target.mkdir(parents=True, exist_ok=True)
    files = _copy_kept(source, target, True)
    keep = target / BEFORE
    keep.mkdir(exist_ok=True)
    for name, there in ((OUTLINE, target / OUTLINE), (SCRIPT, target / SCRIPT),
                        (DECK_SOURCE, target / DECK_FOLDER / DECK_SOURCE)):
        if there.is_file():
            shutil.copyfile(there, keep / name)
    kept_path = target / STATE
    kept = json.loads(kept_path.read_text(encoding="utf-8"))
    kept["flow"] = {"stage": REVISE_STAGE, "pass": 1}
    kept["deck_flow"] = {"stage": DECK_REVISE_STAGE, "pass": 1}
    kept.setdefault("deck", {"builds": 0}).update(problems=[], unfit=False)
    kept["revision"] = {"by": by, "words": words, "from": before["id"],
                        "run": before["run"]}
    fresh = _fresh_sessions(kept)
    who = "its sender said what is wrong at its check" if by == SENDER else \
        "Tim rejected it"
    note = (f"This is {doc['run']} of the package. It revises {before['run']}, which {who}: "
            f"everything {before['run']} made is kept (its research, the prep, the outline, "
            f"the script and the deck), and only what the words below change is done again. "
            f"{'The sender' if by == SENDER else 'Tim'} said:")
    conversation = kept["swarm"].setdefault("conversation", [])
    conversation.append({"from": sessions.PROGRAM, "text": note})
    conversation.append({"from": by, "text": words_said(words, by)})
    kept_path.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
    steps = ["research"]
    return {"reused": {"from": before["id"], "run": before["run"], "step": "swarm",
                       "stage": REVISE_STAGE, "revision": {"by": by, "words": words},
                       "steps": steps, "files": files, "fresh_sessions": fresh},
            "done": steps}


def changed_slides(before: Optional[dict], after: Optional[dict]) -> list[str]:
    """The ids of the slides that differ between two versions of a script,
    outline or deck source (changed, added or removed), in the order of
    `after`, then those removed."""
    old = {s.get("id"): s for s in (before or {}).get("slides") or [] if isinstance(s, dict)}
    new = [s for s in (after or {}).get("slides") or [] if isinstance(s, dict)]
    ids = {s.get("id") for s in new}
    out = [s.get("id") for s in new if old.get(s.get("id")) != s]
    return [i for i in out + [i for i in old if i not in ids] if i]


def slide_ids(doc: Optional[dict]) -> list[str]:
    return [s.get("id") for s in (doc or {}).get("slides") or [] if isinstance(s, dict)]


def record(job_id: str, part: str, value: dict) -> dict:
    """What a step of a revision redid, kept on the job under `revision`."""
    return pj._change(job_id, f"revision: {part} noted", lambda d: d.update(
        revision={**(d.get("revision") or {}), part: value}))
