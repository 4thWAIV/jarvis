---
name: talk-to-jarvis
description: How to reach the user through JARVIS, their voice assistant - send them a message about the work item it belongs to, and send every decision as a package made with make_package. Use whenever you have something to tell them, or a decision, question or blocker waiting on them.
---

# Talking to the user through JARVIS

JARVIS is the user's voice assistant. Every message you send them goes one way, signed with your own key, and lands on the work item it is about (its Messages tab), or in their Inbox when it is about no item. Use it instead of waiting for the user to come back to your window.

## Your own key: how JARVIS knows it is you

Everything you send JARVIS is signed with your own key, so it can tell it came from you. Make it once per session, and submit it, both yourself in your own session (JARVIS checks your own transcript for the submit command, so a key nobody's transcript shows submitting is never trusted):

```
{{agent_key}} init --session <your session id>
{{agent_key}} submit --session <your session id> --fingerprint <the SHA256:... init printed>
{{agent_key}} status --session <your session id>
```

`init` makes the key pair in your own config home (`jarvis/agent-keys/<session id>/`; the private half never leaves it, and you never read or copy another agent's) and prints the fingerprint. After `submit` the key waits for the user: switching your agent on in JARVIS's Agents tab is how they accept it. `status` says where it stands. Until it is accepted, everything you send is held and the user is told, in an error package, that a key JARVIS does not know tried to send it; once they accept it, what was held goes to them. Give `--as <your session id>` to `message`, `work_report.py` and `workflow_report.py` and they sign every request with it; `make_package` signs with the key of its `--session`. A resumed session keeps its key, a new session makes its own. Your subagents sign with your key; they never make keys of their own. A subagent is named as a package's sender, beside you, only when its own transcript shows it running `make_package` itself.

## Messages: the one way to tell the user something

```
{{message}} --as <your session id> --work <item id> "<what it says>" [--package <zip>]
```

A message says something short (at most 2000 characters): a finished piece of work, a report, a blocker. Give `--work` with the work item it is about; while you have a piece on an open item JARVIS refuses a message without it and names the item. With no item of yours open, leave `--work` out and it goes to the user's Inbox. It prints the message's id, or why it was refused (exit 1). The user can reply to every message; the reply comes to your session as an ordinary message.

A message never asks a bare question: one that asks something (a sentence ending in "?") without a package is refused. Every decision or question goes to the user inside a package that shows them what they need to decide (see "Packages"). Never ask "Do you approve the plan?" unless the package shows the plan.

## Packages: every decision, and any presentation

Every choice you put to the user goes as a decision package, and any presentation or update as a presentation package. `make_package` sends it as a message carrying it, on the work item `--work` names. Give the factory an evidence handoff; its fresh author writes the package content or folder. Every kind goes through one command and no other way:

```
{{make_package}} --handoff <handoff.json> --engine <claude|codex> --session <your session id> --work <item id> [--workflow <id>] [--says "<one or two sentences>"]
```

Use your own engine: a Claude caller selects `claude`, and a Codex caller selects `codex`. The command launches a fresh `human-package-author` session before building, then keeps your session and work item for delivery and answers. `--check-only` validates source without launching an author.

`{{making_a_package}}` is the only description of what to write and what the command does; read it before your first package. Supply the audience, the questions and the evidence: what the choice is, each option shown as what the user would see, what happens either way, and your pick with its reason. The command builds the package, checks it end to end in an isolated JARVIS, and only then sends it: a message on the work item (saying `--says`, else the package's kind and title) carrying the package, never shown and never spoken until the user opens it. Always give `--session <your session id>`: the package and the request are signed with that session's key, and the user's answers come back to your session as a message once they have answered and said to send them. Give `--work <id>` whenever it is about a work item (the same rule as a message), and `--workflow <id>` when it belongs to a workflow you report (it waits on the answers, which come back as its decisions, read with `workflow_report.py answer <id> <decision id>`). Every package carries the user's vote: one that asks nothing ends with the standard one, Got it or I have questions, which the command adds; their questions come back to you with their answers. An agent package sent with `--work` carries a key for that item, and its vote is asked on the work item itself; a conversation package cannot carry a vote, so it may not name a work item. A newer version of a package you sent replaces the older one while it is open: that is how you take a question back or change it. When the user is done with a package they sign it off, and whatever they answered comes back to you (unanswered questions marked unanswered), or, for a package that asks nothing, a message that they have reviewed it. Never open, show or answer a package in JARVIS yourself. If anything fails, nothing is delivered: fix what it says and run it again. Never build, zip, attach or deliver a package by hand.

Architecture goes to the user as a canonical architecture document, never as slides or a page of your own: write the YAML, check it with `{{language_document}} --as <your session id> validate --source <architecture.yaml> --json '<work_id, scope_revision, author, coverage>'`, and send the exact revision it returns with `{{make_package}} --document <document_id> --revision <revision> --handoff <handoff.json> --engine <claude|codex> --session <your session id> --work <item id>`. The fresh author writes its plain explanation; JARVIS makes the page from the checked revision. `{{making_a_package}}`, "Architecture documents", is the whole path, including `capabilities` first and `ground` afterwards.

## Workflows: reporting long work as it goes

When you run a long piece of work in phases, such as a multi-step build or a workflow of several agents, report each step to JARVIS's workflow monitor so the user can see its progress. Give `--as <your session id>` on every call: each request is signed with your own accepted key, and every event shows as yours. `agent_attached` may name only your own session; any other is refused. Start it with `--work <item id>`, the work item it is doing: what it needs of the user goes there as a message.

```
python3 {{workflow_report}} --as <your session id> start "<name>" --project <project> --work <item id> --phase <phase> --phase <phase>
python3 {{workflow_report}} event <id> agent_attached "<who is running it>" --session <your session id>
python3 {{workflow_report}} event <id> phase_started "<one sentence>" --item <phase>
python3 {{workflow_report}} event <id> phase_added "<one sentence>" --item <phase> --title "<title>"
python3 {{workflow_report}} event <id> task_added "<one sentence>" --item <task> --title "<title>"
python3 {{workflow_report}} event <id> task_progress "<one sentence>" --item <task> --done 3 --total 8
python3 {{workflow_report}} event <id> blocker_raised "<what blocks it>" --key <slug>
python3 {{workflow_report}} event <id> wait_started "<what you wait for>" --key <slug> --until package:<package or inbox id>
python3 {{workflow_report}} answer <id> <decision id>
```

`start` prints the workflow's id. The kinds are `phase_added` (a phase not given at `start`), `phase_started`, `phase_finished`, `task_added`, `task_started`, `task_progress`, `task_finished`, `wait_started`, `wait_ended`, `blocker_raised`, `blocker_cleared`, `agent_attached`, `note` and `workflow_finished` (with `--outcome succeeded`, `failed` or `cancelled`). Every event's summary is one plain sentence. Report at least every few minutes: a workflow that reports nothing for ten minutes shows as gone quiet. A blocker is also a message to the user on the workflow's work item. A decision is never an event: send it as a package with `--workflow <id> --work <item id>`, and `answer` prints the user's answer to one of its decisions once they have given it (exit 3 until then).

Whenever you wait on the user, link the wait to what will answer it with `--until`: `package:<package or inbox id>` (until they sign the package off), `package:<id>:<decision id>` (one of its decisions), `work:<item id>:<key>` or `workflow:<workflow id>:<key>`. When the user answers it, by any route, JARVIS ends the wait itself; do not report `wait_ended` for it. A wait on something other than the user (a test run, a build) needs no link.

## Work items: who is doing each piece

When you take on a piece of work that lives in a ZIP work package or a folder on this Mac, track it on JARVIS's Work tab, so the user sees its state and exactly who is on each part of it. Give `--as <your session id>` on every call: each request is signed with your own accepted key, and every report shows your agent's name as who reported it. An agent package you send with `make_package --work <id>` carries a key for that one item, so whoever works in the unpacked package reports on it (`work_report.py` and `message` find `.jarvis/work-key` by themselves); `{{agent_key}} grant --work <id> --into <folder> --session <your session id>` makes one for a folder. It ends when the item closes or after 30 days (`{{agent_key}} renew` extends it).

```
python3 {{work_report}} start "<title>" --folder <path> --area "<area>"        (or --zip <path>; --area is optional)
python3 {{work_report}} piece <id> <key> "<one sentence>" --title "<title>" --who agent:<your session id> --state working
python3 {{work_report}} piece <id> <key> "<one sentence>" --worker "<who did it, in your words>" --after <key>,<key> --evidence <commit|path|url>
python3 {{work_report}} update <id> "<one sentence>" --state working
python3 {{work_report}} piece <id> <key> "<what you wait for>" --who tim --state waiting_on_tim --until package:<package or inbox id> --then working
python3 {{work_report}} piece <id> <key> "<what blocks it>" --state blocked --until "<the blocker in words>"
python3 {{work_report}} piece <id> <key> "<one sentence>" --state working --cleared        (a blocker in words is gone)
python3 {{work_report}} piece <id> <key> "<what to check>" --state verifying --evidence <commit|path|url>
python3 {{work_report}} answer <id> <key>
python3 {{work_report}} answers --since <ISO time or epoch seconds>
python3 {{work_report}} close <id> "<one sentence>"
```

`start` prints the item's id; the path must be a zip or folder that is there now, and JARVIS never opens or runs it. A state is `planned`, `ready` (nothing holds it; it is yours to pick up), `working`, `verifying` (the work is in, ready for the user's review), `waiting_on_tim` or `blocked`; `done` is the user's approval, never yours to report. Who is on a piece is `agent:<session id>`, `agent:<session id>/sub:<agent id>` (one of your subagents, by the agentId the Agent tool gave you: JARVIS reads its own files, so the piece shows Working while its turn is open), `run:<run id>`, `workflow:<workflow id>` or `tim`, and must be one JARVIS knows. A working report is shown as Working only while the agent or subagent on the piece is in a turn (waiting on the model or running a tool); otherwise it reads "Not working now". Every summary is one plain sentence, and the latest one is what the Work tab shows. Items are grouped by `--area`. `--worker` is your own words for who did a piece, shown beside you. `--after` names the pieces of the same item this one waits on. Each `--evidence` (a commit, a path or a URL) is added to the piece's evidence; give it when a piece is verifying.

A decision about a work item is a decision package sent with `--work <id>` (see "Packages"): it is a message on that item, and the user answers it in the package. `work_report.py decision` and `withdraw` are gone and say so. Tell the user anything else about the item with a message (`--work <id>`), and a blocker as a piece with `--state blocked` and `--until` naming what blocks it. When the work is abandoned, `close` moves the item to History for good; an item the user has approved done moves there by itself a few minutes after. `answer` prints the user's answer to a question asked on the item (a package's `understood-<inbox id>` vote) once they have given it and exits 3 until then; it exits 4 when the package was signed off without an answer.

The Work record is authoritative, and JARVIS refuses a report that does not fit it. A piece that waits on the user (`--state waiting_on_tim`) or is blocked (`--state blocked`) is held, and must say what holds it with `--until` (repeat it for several): `package:<package or inbox id>` (until they sign the package off), `package:<id>:<decision id>`, `work:<item id>:<key>` or `workflow:<workflow id>:<slug>`; a blocker may instead be words. Nothing you report moves a held piece on: once every one is answered, by any route, JARVIS moves it to `--then` (`ready` by default) itself; a blocker in words is cleared by reporting the piece's next state with `--cleared`. So never keep working inside a held piece: split the work that can go on into a piece of its own. An item with pieces takes its state from them, so never report the item itself waiting or blocked. Done is the user's alone: take a piece or item as far as `verifying`, with `--evidence` of what to check, and they approve it, say it is not done yet (it comes back to working with their note) or add work (a new piece in their words). Their note and any added work are written for you to `{{answers_dir}}/<project>/<item id>-review-<time>.md`, and every answer to a question asked on your items to `{{answers_dir}}/<project>/<item id>-<key>.md`; `answers --since <time>` lists them in one call. Act on an answer at once: report a piece or update straight away.
