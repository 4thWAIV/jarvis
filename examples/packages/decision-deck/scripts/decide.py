"""Read a decision deck's decisions and record the owner's answers.

The decisions are in deck-data.js, the same file the page draws from. The
record is results/decisions.json, with results/decisions.md and
results/decisions.js beside it, written exactly as the page writes them; the
page reads results/decisions.js back every few seconds, so an answer recorded
here shows on screen.

    decide.py show                       every decision and what is recorded
    decide.py show <decision>            one decision, word for word
    decide.py <decision> <option> [words...]
                                         record an answer; <option> is an
                                         option's id, its letter (a, b, ...),
                                         recommended, later or none; the
                                         words are his own, kept as the note
    decide.py all-recommended            the recommended option for every
                                         decision with no answer yet
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RESULTS = HERE / "results"
DATA_PREFIX = "window.DECK = "
RECORD_PREFIX = "window.JARVIS_DECISIONS = "
SPECIAL = {"later": "Decide later", "none": "None of these"}


def deck() -> dict:
    text = (HERE / "deck-data.js").read_text(encoding="utf-8")
    start = text.index(DATA_PREFIX) + len(DATA_PREFIX)
    return json.JSONDecoder().raw_decode(text, start)[0]


DECK = deck()
DECISIONS = DECK.get("decisions", [])


def by_id(ident: str) -> dict | None:
    return next((d for d in DECISIONS if d["id"].lower() == ident.lower()), None)


def option_of(d: dict, ident: str) -> dict | None:
    return next((o for o in d.get("options", []) if o["id"] == ident), None)


# A child question applies only when its parent's answer is an option that
# opens it (option "opens"), and its parent applies.
PARENT: dict = {}
for _d in DECISIONS:
    for _o in _d.get("options", []):
        for _child in _o.get("opens", []):
            PARENT.setdefault(_child, {"decision": _d["id"], "options": []})["options"].append(_o["id"])


def applies(d: dict, record: dict) -> bool:
    p = PARENT.get(d["id"])
    if not p:
        return True
    e = record.get(p["decision"])
    return bool(e) and e["answer"] in p["options"] and applies(by_id(p["decision"]), record)


def opens_text(d: dict) -> str:
    p = PARENT[d["id"]]
    parent = by_id(p["decision"])
    return parent["id"] + " is " + " or ".join(option_of(parent, o)["name"] for o in p["options"])


def clean(raw: object) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for d in DECISIONS:
        e = raw.get(d["id"])
        if not isinstance(e, dict) or not isinstance(e.get("answer"), str):
            continue
        if e["answer"] not in SPECIAL and not option_of(d, e["answer"]):
            continue
        out[d["id"]] = {"answer": e["answer"], "at": str(e.get("at") or ""),
                        "choice": str(e.get("choice") or ""), "note": str(e.get("note") or ""),
                        "recommended": bool(e.get("recommended"))}
    return out


def load() -> dict:
    path = RESULTS / "decisions.json"
    return clean(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}


def dumps(record: dict) -> str:
    return json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def markdown(record: dict) -> str:
    """The page's markdown(), line for line."""
    lines = ["# " + DECK.get("title", ""), ""]
    for d in DECISIONS:
        e = record.get(d["id"])
        lines += ["## " + d["id"], "", d["question"], ""]
        if not e:
            lines.append("Not answered." if applies(d, record)
                         else f"Not asked: it applies only when {opens_text(d)}.")
        elif e["answer"] == "later":
            lines.append(f"Decide later. Recorded {e['at']}.")
        elif e["answer"] == "none":
            lines.append(f"None of these. Recorded {e['at']}.")
        else:
            which = "the recommended option" if e["recommended"] else "not the recommended option"
            lines.append(f"Answer: {e['choice']} ({which}). Recorded {e['at']}.")
        if e and e["note"]:
            lines += ["", "Note: " + e["note"]]
        lines.append("")
    return "\n".join(lines)


def save(record: dict) -> None:
    RESULTS.mkdir(exist_ok=True)
    text = dumps(record)
    for name, body in (("decisions.json", text), ("decisions.md", markdown(record)),
                       ("decisions.js", RECORD_PREFIX + text.rstrip() + ";\n")):
        part = RESULTS / (name + ".part")
        part.write_text(body, encoding="utf-8")
        part.replace(RESULTS / name)


def entry(d: dict, answer: str, note: str) -> dict:
    o = option_of(d, answer)
    return {"answer": answer, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "choice": o["name"] if o else "", "note": note, "recommended": answer == d.get("recommended")}


def answer_text(d: dict, e: dict | None, record: dict) -> str:
    if not e:
        return "not answered" if applies(d, record) else f"not asked yet: it applies when {opens_text(d)}"
    if e["answer"] in SPECIAL:
        said = SPECIAL[e["answer"]]
    else:
        said = e["choice"] + (" (recommended)" if e["recommended"] else "")
    return said + (f', his words: "{e["note"]}"' if e["note"] else "")


def sentence(text: str) -> str:
    return text if text.endswith(('.', '."', '?"', '!"')) else text + "."


def show_one(d: dict, record: dict) -> None:
    print(f"{d['id']}: {d['question']}")
    if d["id"] in PARENT:
        print(f"  Asked only when {opens_text(d)}.")
    if d.get("context"):
        print(f"  Context: {d['context']}")
    for letter, o in zip("abcdefghij", d.get("options", [])):
        rec = " (recommended)" if o["id"] == d.get("recommended") else ""
        opens = f" Then asks: {', '.join(o['opens'])}." if o.get("opens") else ""
        print(f"  {letter}) {o['id']}: {o['name']}{rec}: {o['says']}{opens}")
    print(f"  Recommended because: {d.get('reason', '')}")
    print(f"  Recorded: {answer_text(d, record.get(d['id']), record)}")


def show_all(record: dict) -> None:
    done = sum(1 for d in DECISIONS if d["id"] in record and record[d["id"]]["answer"] != "later")
    print(f"{DECK.get('title', '')}: {done} of {len(DECISIONS)} answered.")
    for d in DECISIONS:
        print(f"{d['id']}: {d['question']} Recorded: {sentence(answer_text(d, record.get(d['id']), record))}")


def resolve(d: dict, said: str) -> str | None:
    said = said.lower()
    if said in SPECIAL:
        return said
    if said == "recommended":
        return d.get("recommended")
    options = d.get("options", [])
    for o in options:
        if o["id"].lower() == said:
            return o["id"]
    letters = dict(zip("abcdefghij", options))
    return letters[said]["id"] if said in letters else None


def main(argv: list) -> int:
    if not argv or argv[0] in ("show", "summary") and len(argv) == 1:
        show_all(load())
        return 0
    if argv[0] == "show" and len(argv) == 2:
        d = by_id(argv[1])
        if not d:
            print(f"No decision {argv[1]}; they are: {', '.join(x['id'] for x in DECISIONS)}.")
            return 1
        show_one(d, load())
        return 0
    if argv[0] == "all-recommended" and len(argv) == 1:
        record = load()
        added, changed = [], True
        while changed:
            changed = False
            for d in DECISIONS:
                if d["id"] in record or not applies(d, record) or not option_of(d, d.get("recommended", "")):
                    continue
                record[d["id"]] = entry(d, d["recommended"], "")
                added.append(d)
                changed = True
        if not added:
            print("Every decision that applies already has an answer; nothing was recorded.")
            return 0
        save(record)
        for d in added:
            print(f"Recorded: {d['id']}: {sentence(answer_text(d, record[d['id']], record))}")
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    d = by_id(argv[0])
    if not d:
        print(f"No decision {argv[0]}; they are: {', '.join(x['id'] for x in DECISIONS)}.")
        return 1
    answer = resolve(d, argv[1])
    if not answer:
        ids = ", ".join(o["id"] for o in d.get("options", []))
        print(f"{d['id']} takes one of {ids}, recommended, later or none. Nothing was recorded.")
        return 1
    record = load()
    record[d["id"]] = entry(d, answer, " ".join(argv[2:]).strip())
    save(record)
    print(f"Recorded: {d['id']}: {sentence(answer_text(d, record[d['id']], record))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
