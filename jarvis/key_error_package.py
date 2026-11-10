"""The error package JARVIS makes for Tim when something comes signed by a
key it does not know as an onboarded agent's (or, after the cut-over, comes
unsigned), or an onboarded agent's package is not proven written by a
fresh author (author_proof.py; each attempt's `why`): a page package saying
which session and key tried, what it tried to send, when and why it is
held, and offering his acts.

  Switch it on    switching the agent on accepts its key, and what was held
                  goes on (only offered while its key waits for him)
  Drop it         an onboarded key's: what was held is dropped, the key kept
  Refuse          refuse and revoke the key; what was held is dropped

The page is JARVIS's own and sandboxed like every page package: its buttons
save `results/act.json` through the frame's bridge (packageBridge.ts), and
the server acts on it (server._key_error_act), as his click. Every word of
the agent's (its name, a package's title, a message's subject) is escaped
text, never markup. Nothing here touches the record: it only builds the zip.
"""
from __future__ import annotations

import html
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

ACT_FILE = "act.json"
ONBOARD, DROP, REFUSE = "onboard", "drop", "refuse"
NAME_PREFIX = "key-error"


@dataclass(frozen=True)
class Attempt:
    kind: str          # package, message, board, request
    what: str          # what it tried to send
    when: str          # a local time, already worded
    why: str = ""      # why it is held, when not for its key (author_proof)


@dataclass(frozen=True)
class Facts:
    subject: str                   # key:<fingerprint> or session:<id>
    agent: str                     # the agent's one name, or ""
    session: str                   # the session it is for, or ""
    fingerprint: str               # "" when it was unsigned
    key_state: str                 # pending, waiting, revoked, unknown, unsigned
    attempts: list[Attempt] = field(default_factory=list)
    work: str = ""                 # the Work item it was for, by id and title

    @property
    def can_onboard(self) -> bool:
        return self.key_state == "waiting" and bool(self.session)

    @property
    def onboarded(self) -> bool:
        """Its key is an onboarded agent's: held for what it sent, not its key."""
        return self.key_state == "onboarded"


def name_for(subject: str) -> str:
    """The package's name: one per subject, so a newer version replaces it."""
    tail = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[-40:]
    return f"{NAME_PREFIX}-{tail or 'unknown'}"


_KINDS = {"package": "A package", "message": "A message", "board": "A needs board",
          "request": "A report"}
_STATES = {
    "pending": "Its key was submitted but its own transcript has not yet proven it.",
    "waiting": "Its key is proven and waits for you: switching the agent on accepts it.",
    "revoked": "Its key was revoked.",
    "unknown": "JARVIS holds no key by that fingerprint.",
    "unsigned": "It was not signed at all.",
    "onboarded": "Its key is onboarded; what it sent is held for the reason beside it.",
}
HEADING = {False: "Held: signed by a key JARVIS does not know as an onboarded agent's",
           True: "Held: not proven written by a fresh author"}


def _e(text: str) -> str:
    return html.escape(str(text or ""), quote=True)


def page(facts: Facts) -> str:
    who = facts.agent or (f"session {facts.session}" if facts.session else "an unknown sender")
    rows = "".join(f"<li><b>{_e(_KINDS.get(a.kind, a.kind))}</b>: {_e(a.what)} "
                   f"<span class=when>{_e(a.when)}</span>"
                   + (f"<br><span class=why>{_e(a.why)}</span>" if a.why else "") + "</li>"
                   for a in facts.attempts)
    if facts.onboarded:
        onboard = (f'<button id=drop data-act="{DROP}">Drop what is held, keep its key</button>'
                   '<p class=note>It goes on by itself if its author\'s proof checks within '
                   'minutes of arriving; otherwise it waits for you.</p>')
    elif facts.can_onboard:
        onboard = (f'<button id=onboard data-act="{ONBOARD}">Switch {_e(who)} on and accept '
                   f'its key</button>')
    else:
        onboard = ('<p class=note>It cannot be onboarded yet: the agent must run its own '
                   '<code>agent_key init</code> and <code>submit</code>, in its own session, '
                   'and then you switch it on.</p>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{HEADING[facts.onboarded]}</title>
<style>
body {{ font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 24px; color: #e5e7eb;
       background: #0b1220; }}
h1 {{ font-size: 20px; color: #f87171; margin: 0 0 12px; }}
dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; }}
dt {{ color: #9ca3af; }} dd {{ margin: 0; overflow-wrap: anywhere; }}
code, .fp {{ font-family: ui-monospace, monospace; font-size: 13px; }}
.when, .why {{ color: #9ca3af; font-size: 13px; }}
.acts {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 20px; }}
button {{ font: inherit; padding: 8px 14px; border-radius: 6px; cursor: pointer;
          border: 1px solid #374151; background: #111827; color: #e5e7eb; }}
#onboard {{ border-color: #34d399; }} #refuse {{ border-color: #f87171; }}
#done {{ margin-top: 16px; color: #34d399; }}
</style></head>
<body>
<h1>{HEADING[facts.onboarded]}</h1>
<dl>
<dt>Who</dt><dd>{_e(who)}</dd>
<dt>Session</dt><dd class=fp>{_e(facts.session or "none named")}</dd>
<dt>Key</dt><dd class=fp>{_e(facts.fingerprint or "none: it was unsigned")}</dd>
<dt>Its key</dt><dd>{_e(_STATES.get(facts.key_state, facts.key_state))}</dd>
{f"<dt>Work item</dt><dd>{_e(facts.work)}</dd>" if facts.work else ""}
</dl>
<p>What it tried to send, held until you choose:</p>
<ul>{rows}</ul>
<div class=acts>
{onboard}
<button id=refuse data-act="{REFUSE}">Refuse and revoke the key</button>
</div>
<p id=done hidden></p>
<script>
document.querySelectorAll("button[data-act]").forEach(function (b) {{
  b.addEventListener("click", function () {{
    parent.postMessage({{jarvis: "package", type: "save", name: "{ACT_FILE}",
                        data: JSON.stringify({{act: b.dataset.act}})}}, "*");
    var done = document.getElementById("done");
    done.hidden = false;
    done.textContent = b.dataset.act === "{ONBOARD}" ? "Switching it on." :
      b.dataset.act === "{DROP}" ? "Dropping what is held." : "Refusing its key.";
  }});
}});
</script>
</body></html>
"""


def build(facts: Facts, version: str, out: Path) -> Path:
    """The zip of the error package for `facts`, at `out`."""
    name = name_for(facts.subject)
    manifest = {"name": name, "version": version, "runs": "page", "entry": "index.html",
                "purpose": ("JARVIS holds what an onboarded agent sent, not proven written by "
                            "a fresh author, until its proof checks or Tim drops it."
                            if facts.onboarded else
                            "JARVIS holds what an agent sent, signed by a key it does not know "
                            "as an onboarded agent's, until Tim switches the agent on or "
                            "refuses its key."),
                "returns": ["results/**"]}
    zip_path = out / f"{name}-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/package.json", json.dumps(manifest, indent=2) + "\n")
        z.writestr(f"{name}/index.html", page(facts))
    return zip_path


def act_of(data: bytes) -> str:
    """The act a saved act.json asks for, or ""."""
    try:
        act = json.loads(data.decode("utf-8")).get("act")
    except (ValueError, AttributeError, UnicodeDecodeError):
        return ""
    return act if act in (ONBOARD, DROP, REFUSE) else ""
