"""
run_report.py — what each package run took and how well it went
(work-list.md item 38; construction-drafts.md "Quality first, and model
types", Tim 2026-11-21: reasonable monitoring, telemetry and metrics for
improvement). Written by the program from what the run already holds; no
new judge, no new model.

Two files beside each run (its folder, package_jobs.folder_of):

- `timing.json` (`TIMING`): each step's start, end and seconds (a step run
  again after waiting on the sender is one more entry, `resumed`), and in
  each step every one-off role call (marking, classification, extraction,
  summarization, retrieval, and every swarm turn) with its role, engine,
  model, tier, effort, seconds, tokens in, cached and out and how it ended
  (`answered`, `refused` by the brain's model list, `time_limit`,
  `rate_limited`, `failed`); the pages each paged role asked (pages.Report:
  pages, calls, asked again, halved, rate-limit hits and the wait); the
  app servers started; the swarm's stages with their times. Its `totals`
  sum them, and a step over `STEP_FAILS_AFTER_SEC` is flagged `too_slow`
  (Tim 2026-11-21: more than a few minutes for a step is a failure).
- `quality.json` (`QUALITY`): per pass of the swarm, each concern signed
  off or left, its items mapped to the 28 labels, the items repeated in
  the next pass versus fixed, passes to done, Tim's reader's questions
  raised versus answered, the script's facts with a checked source versus
  unanswered, the sender's questions asked and grouped with every merge at
  the 0.85 line (both questions and their score), the deck review's
  findings; then his verdict and his Reject words mapped to the labels
  once he answers (`verdict`).

`collect` is the collector the run's own process records into: a step
opened by `step` (package_jobs.run) takes every call made meanwhile
(`call`, from sessions.BrainChatSession), every page report (`pages`) and
every app server started (`app_server`). Outside a step nothing is kept.
`package` sums every run of a package; `of_work` gives each package run of
a Work item with its reports, read by the brain's `package_run_reports`
and the page's `GET /api/work/{id}/package-runs` alike.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

log = logging.getLogger("jarvis.run_report")

TIMING = "timing.json"
QUALITY = "quality.json"

# How each call ended.
ANSWERED, REFUSED, TIME_LIMIT, RATE_LIMITED, FAILED = (
    "answered", "refused", "time_limit", "rate_limited", "failed")

# Tim 2026-11-21: "MORE THAN a few minutes per step is A FAIL". A step that
# takes longer is flagged in the report, with its numbers.
STEP_FAILS_AFTER_SEC = 5 * 60.0


# ── the collector in the run's own process ────────────────────────────────

class _Step:
    def __init__(self, name: str):
        self.name = name
        self.began = time.time()
        self.calls: list[dict] = []
        self.pages: list[dict] = []
        self.app_servers = 0


_lock = threading.Lock()
_current: Optional[_Step] = None


def call(record: dict) -> None:
    """One role call, into the step under way (nothing outside a step)."""
    with _lock:
        if _current is not None:
            _current.calls.append(dict(record))


def pages(role: str, summary: dict) -> None:
    """What one paged role's pages took (pages.Report.summary)."""
    with _lock:
        if _current is not None:
            _current.pages.append({"role": role, **summary})


def app_server(engine: str) -> None:
    """A brain process started for a role (a Codex app server)."""
    if engine != "codex":
        return
    with _lock:
        if _current is not None:
            _current.app_servers += 1


def _read(path: Path) -> dict:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
        return found if isinstance(found, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, body: dict) -> None:
    from jarvis import package_evidence
    package_evidence.write_file(path, json.dumps(body, indent=2, ensure_ascii=False)
                                .encode("utf-8"))


def _tokens(record: dict) -> tuple[int, int, int]:
    return (int(record.get("tokens_in") or 0), int(record.get("cached") or 0),
            int(record.get("tokens_out") or 0))


def step_entry(step: _Step, ended: float, outcome: str, resumed: bool) -> dict:
    seconds = round(ended - step.began, 1)
    tin = sum(_tokens(c)[0] for c in step.calls)
    cached = sum(_tokens(c)[1] for c in step.calls)
    out = sum(_tokens(c)[2] for c in step.calls)
    return {"step": step.name, "began": step.began, "ended": ended, "seconds": seconds,
            "outcome": outcome, "resumed": resumed,
            "too_slow": seconds > STEP_FAILS_AFTER_SEC,
            "calls": step.calls, "pages": step.pages,
            "pages_asked": sum(int(p.get("pages") or 0) for p in step.pages),
            "retries": (sum(1 for c in step.calls if c.get("outcome") == REFUSED)
                        + sum(int(p.get("asked_again") or 0) + int(p.get("halved") or 0)
                              for p in step.pages)),
            "time_limits": sum(1 for c in step.calls if c.get("outcome") == TIME_LIMIT),
            "rate_limited": (sum(1 for c in step.calls if c.get("outcome") == RATE_LIMITED)),
            "app_server_starts": step.app_servers,
            "tokens": {"in": tin, "cached": cached, "out": out}}


def totals(steps: list[dict]) -> dict:
    """The steps summed: seconds, calls, pages, retries, time-limit and
    rate-limit hits, app servers, tokens; the steps too slow by name."""
    out = {"seconds": 0.0, "calls": 0, "pages": 0, "retries": 0, "time_limits": 0,
           "rate_limited": 0, "app_server_starts": 0,
           "tokens": {"in": 0, "cached": 0, "out": 0}, "too_slow": []}
    for s in steps:
        out["seconds"] = round(out["seconds"] + float(s.get("seconds") or 0), 1)
        out["calls"] += len(s.get("calls") or [])
        out["pages"] += int(s.get("pages_asked") or 0)
        for key in ("retries", "time_limits", "rate_limited", "app_server_starts"):
            out[key] += int(s.get(key) or 0)
        for key in ("in", "cached", "out"):
            out["tokens"][key] += int((s.get("tokens") or {}).get(key) or 0)
        if s.get("too_slow") and s.get("step") not in out["too_slow"]:
            out["too_slow"].append(s.get("step"))
    return out


def _stages(folder: Path) -> list[dict]:
    """The swarm's stages with their times (swarm.json `times`)."""
    return list(_read(folder / "swarm.json").get("times") or [])


@contextlib.contextmanager
def step(doc: dict, name: str) -> Iterator[None]:
    """A step of the run, timed, every call made meanwhile kept with it;
    written into the run's timing.json when it ends, however it ends."""
    global _current
    folder = Path(doc["folder"])
    entry = _Step(name)
    with _lock:
        _current = entry
    outcome = "done"
    try:
        yield
    except BaseException as e:
        outcome = f"ended: {type(e).__name__}"
        raise
    finally:
        with _lock:
            _current = None
        try:
            path = folder / TIMING
            body = _read(path) or {"job": doc.get("id"), "package": doc.get("package"),
                                   "run": doc.get("run"), "work": doc.get("work"),
                                   "steps": []}
            resumed = any(s.get("step") == name for s in body["steps"])
            body["steps"].append(step_entry(entry, time.time(), outcome, resumed))
            body["stages"] = _stages(folder)
            body["totals"] = totals(body["steps"])
            _write(path, body)
        except Exception:  # noqa: BLE001 - the report never stops the run
            log.warning("run_report: could not write the timing of %s", name, exc_info=True)


# ── the quality report ────────────────────────────────────────────────────

# How free text (a finding, his Reject words) is mapped to the 28 labels:
# the marking role's own sentences, each with its top labels (sessions
# MarkingSession; tests give their own).
LabelMapper = Callable[[list[str]], list[list[str]]]


def _mark_labels(texts: list[str], doc: dict) -> list[list[str]]:
    """Each text's labels: every label any of its sentences is marked with
    by the marking role, in the order first given."""
    if not texts:
        return []
    import asyncio
    from jarvis import briefing_labels, package_mark, sessions
    marking = package_mark.MARKING(doc)
    keyed = [sessions.Text(f"t{n}", t) for n, t in enumerate(texts)]
    marked = asyncio.run(marking.mark_files(keyed, briefing_labels.for_marking(), [], None))
    out = []
    for t in keyed:
        names: list[str] = []
        for part in marked.get(t.key) or []:
            for name in part.get("scores") or {}:
                if name not in names:
                    names.append(name)
        out.append(names)
    return out


def _same(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _repeats(passes: list[dict], embed) -> None:
    """Each pass's items marked repeated (said again, by meaning at the
    0.85 line, in the next pass by the same concern) or fixed (not said
    again); the last pass's items are neither (`left`)."""
    from jarvis import package_swarm
    for n, here in enumerate(passes):
        later = passes[n + 1] if n + 1 < len(passes) else None
        for item in here["items"]:
            item["then"] = "left" if later is None else "fixed"
        if later is None or not here["items"] or not later["items"]:
            continue
        vectors = embed([i["what"] for i in here["items"]] + [i["what"] for i in later["items"]])
        mine, theirs = vectors[:len(here["items"])], vectors[len(here["items"]):]
        for item, v in zip(here["items"], mine):
            for other, w in zip(later["items"], theirs):
                if other["concern"] == item["concern"] and \
                        _same(v, w) >= package_swarm.SAME_MEANING:
                    item["then"] = "repeated"
                    break


def _passes(result: dict, deck: bool = False) -> list[dict]:
    """The swarm's sign-offs by stage, in order (the deck review's, its
    stages named deck-, apart): each stage's concerns resolved or left,
    and every item left."""
    stages: dict[str, dict] = {}
    for s in result.get("signoffs") or []:
        if str(s.get("stage") or "").startswith("deck-") != deck:
            continue
        stage = stages.setdefault(s.get("stage") or "", {"stage": s.get("stage") or "",
                                                         "resolved": [], "left": [],
                                                         "items": []})
        (stage["resolved"] if s.get("state") == "resolved" else stage["left"]).append(
            s.get("concern"))
        for item in s.get("left") or []:
            stage["items"].append({"concern": s.get("concern"), "owner": s.get("owner"),
                                   "what": item.get("what") or "", "fix": item.get("fix") or ""})
    return list(stages.values())


def _facts(script: dict, folder: Path, roots: list[Path]) -> dict:
    """The script's facts: each with a source that is there (a file the
    run, its evidence or its research holds) versus unanswered or a source
    not found."""
    from jarvis import package_script
    checked, unanswered, missing = 0, 0, []
    for slide in script.get("slides") or []:
        for fact in slide.get("facts") or []:
            source = str((fact or {}).get("source") or "").strip()
            if not source or source == package_script.UNANSWERED:
                unanswered += 1
                continue
            path = source.split("#", 1)[0].split(" ", 1)[0]
            there = any((root / path).is_file() or Path(path).is_file() for root in roots)
            if there:
                checked += 1
            else:
                missing.append(source)
    return {"checked": checked, "unanswered": unanswered, "source_not_found": len(missing),
            "not_found": missing[:50]}


def quality(doc: dict, *, mapper: Optional[LabelMapper] = None, embed=None) -> dict:
    """The run's quality report, made from swarm-result.json, swarm.json,
    script.yaml and the deck review the run already holds, written to
    quality.json; its verdict kept from an earlier write."""
    from jarvis import package_script, package_swarm
    folder = Path(doc["folder"])
    result = _read(folder / package_swarm.RESULT)
    kept = _read(folder / package_swarm.STATE)
    if not result and not kept:
        return {}
    result = result or {**kept.get("swarm", {}), "times": kept.get("times") or []}
    deck = _read(folder / "deck-review.json")
    passes = _passes(result)
    deck_passes = _passes(deck, deck=True) if deck else []
    embed = embed or package_swarm.EMBED
    mapper = mapper or (lambda texts: _mark_labels(texts, doc))
    items = [i for p in passes + deck_passes for i in p["items"]]
    try:
        for item, labels in zip(items, mapper([i["what"] for i in items])):
            item["labels"] = labels
    except Exception as e:  # noqa: BLE001 - the report keeps what it has
        log.warning("run_report: findings not mapped to the labels (%s)", e)
    try:
        _repeats(passes, embed)
        _repeats(deck_passes, embed)
    except Exception as e:  # noqa: BLE001
        log.warning("run_report: repeats not worked out (%s)", e)
    swarm_items = [i for p in passes for i in p["items"]]
    reader = [i for i in swarm_items if i["owner"] == package_swarm.READER]
    asking = kept.get("asking") or {}
    script = package_script.read(folder / package_swarm.SCRIPT) or {}
    roots = [folder]
    try:
        from jarvis import package_evidence
        roots.append(package_evidence.place_of(doc).folder)
    except Exception:  # noqa: BLE001 - the run's own folder alone
        pass
    old = _read(folder / QUALITY)
    body = {
        "job": doc.get("id"), "package": doc.get("package"), "run": doc.get("run"),
        "result": result.get("result"), "passes_to_done": result.get("passes"),
        "passes": passes,
        "repeated": sum(1 for i in swarm_items if i.get("then") == "repeated"),
        "fixed": sum(1 for i in swarm_items if i.get("then") == "fixed"),
        "readers_questions": {"raised": len(reader),
                              "answered": sum(1 for i in reader if i.get("then") == "fixed"),
                              "left": sum(1 for i in reader if i.get("then") == "left")},
        "facts": _facts(script, folder, roots) if script else {},
        "sender_questions": {"asked": sum(len(g.get("asked") or [])
                                          for g in asking.get("groups") or []),
                             "groups": len(asking.get("groups") or []),
                             "merges": [m for g in asking.get("groups") or []
                                        for m in g.get("merges") or []]},
        "deck_review": {"result": deck.get("result"), "passes": deck.get("deck_passes"),
                        "stages": deck_passes, "findings": deck.get("findings") or []}
                       if deck else None,
        "verdict": old.get("verdict"),
    }
    _write(folder / QUALITY, body)
    return body


def verdict(doc: dict, answer: str, words: str = "", *, map_now: bool = True,
            mapper: Optional[LabelMapper] = None) -> dict:
    """His verdict on the delivered package, kept in its run's quality
    report: approve, or reject with his words mapped to the labels; with
    `map_now` False (the server, which never waits on a model) the words
    are kept and mapped later (`map_pending`, by the next run's process)."""
    folder = Path(doc["folder"])
    body = _read(folder / QUALITY) or {"job": doc.get("id"), "package": doc.get("package"),
                                       "run": doc.get("run")}
    labels: Optional[list[str]] = [] if not words.strip() else None
    if labels is None and map_now:
        labels = _mapped(doc, words, mapper)
    body["verdict"] = {"answer": answer, "words": words, "labels": labels, "at": time.time()}
    _write(folder / QUALITY, body)
    return body


def _mapped(doc: dict, words: str, mapper: Optional[LabelMapper]) -> Optional[list[str]]:
    try:
        return (mapper or (lambda texts: _mark_labels(texts, doc)))([words])[0]
    except Exception as e:  # noqa: BLE001 - his words are kept either way
        log.warning("run_report: his words not mapped to the labels (%s)", e)
        return None


def waits_mapping(doc: dict) -> bool:
    """His Reject words on this run are kept and not yet mapped."""
    said = _read(Path(doc["folder"]) / QUALITY).get("verdict") or {}
    return bool(said) and said.get("labels") is None and bool(str(said.get("words") or "").strip())


def map_pending(doc: dict, *, mapper: Optional[LabelMapper] = None) -> bool:
    """His Reject words on this run mapped to the labels, when they wait to
    be: True once mapped."""
    if not waits_mapping(doc):
        return False
    folder = Path(doc["folder"])
    body = _read(folder / QUALITY)
    said = body["verdict"]
    labels = _mapped(doc, said["words"], mapper)
    if labels is None:
        return False
    body["verdict"] = {**said, "labels": labels}
    _write(folder / QUALITY, body)
    return True


# ── reading them ──────────────────────────────────────────────────────────

def of_run(doc: dict) -> dict:
    """One run's reports: its job, state and step, timing.json and
    quality.json as written."""
    folder = Path(doc["folder"])
    return {"job": doc.get("id"), "package": doc.get("package"), "run": doc.get("run"),
            "state": doc.get("state"), "step": doc.get("step"),
            "timing": _read(folder / TIMING) or None, "quality": _read(folder / QUALITY) or None}


def summed(runs: list[dict]) -> dict:
    """Every run's timing summed, the runs' verdicts and passes listed."""
    steps = [s for r in runs for s in ((r.get("timing") or {}).get("steps") or [])]
    return {"runs": len(runs), "timing": totals(steps),
            "verdicts": [(r.get("quality") or {}).get("verdict") for r in runs
                         if (r.get("quality") or {}).get("verdict")],
            "passes_to_done": [(r.get("quality") or {}).get("passes_to_done") for r in runs]}


def package(package_id: str) -> dict:
    """Every run of a package with its reports, and their sum."""
    from jarvis import package_jobs
    runs = sorted(package_jobs.runs_of(package_id), key=lambda d: d.get("created_at") or 0)
    shown = [of_run(d) for d in runs]
    return {"package": package_id, "runs": shown, "summed": summed(shown)}


def of_work(work_id: str) -> dict:
    """Every package made for a Work item, each with its runs' reports and
    their sum, newest package first."""
    from jarvis import package_jobs
    by_package: dict[str, list[dict]] = {}
    for d in package_jobs.runs_of_work(work_id):
        by_package.setdefault(d["package"], []).append(d)
    out = []
    for pid, runs in by_package.items():
        runs.sort(key=lambda d: d.get("created_at") or 0)
        shown = [of_run(d) for d in runs]
        out.append({"package": pid, "runs": shown, "summed": summed(shown),
                    "latest": max(d.get("created_at") or 0 for d in runs)})
    out.sort(key=lambda p: -p["latest"])
    return {"work": work_id, "packages": out}
