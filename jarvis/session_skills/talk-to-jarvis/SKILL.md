---
name: talk-to-jarvis
description: How to reach the user through JARVIS, their voice assistant - write what you need from them to the needs board, and send them a message when you have something to tell them. Use whenever you have a decision, question or blocker waiting on the user, or a finished piece of work or report to give them.
---

# Talking to the user through JARVIS

JARVIS is the user's voice assistant. It watches two folders and tells the user out loud when something you write there arrives. Use them instead of waiting for the user to come back to your window.

## The needs board: what you are waiting on

Whenever you have a decision, a question or a blocker waiting on the user that is not tied to a work item, write it to `{{needs_dir}}/<project>.md`, where `<project>` is the name of your project's folder. A need tied to a specific work item (a decision, a blocker or a question about it) goes on that work item instead, never on the needs board: see "Work items" below.

Start the file with `# <project>` and an `Updated:` line with the date and time, then three headings: `## Decisions`, `## Questions`, `## Blockers`. Each item is one line under its heading:

```
- <slug>: <one sentence saying what the user is deciding>. Recommended: <your pick>.
```

Keep it to ten items at most. Rewrite the whole file whenever your list changes, remove an item once the user has answered it, and delete the file when nothing is left. JARVIS tells the user when your file changes and reads each item with its recommendation.

## Packages: choices, presentations and anything else for the user

Any choice you put to the user, beyond a line on the needs board, goes to them as a decision package, and any presentation or update as a presentation package. Both are one JSON file of content; a page of your own, an agent's task or steps for JARVIS to follow are a folder. Every kind goes through one command and no other way:

```
{{make_package}} <content.json or folder> --session <your session id> [--workflow <id>] [--work <id>]
```

`{{making_a_package}}` is the only description of what to write and what the command does; read it before your first package. In short: write only the substance (titles, questions, options, your recommendation and its reason, pictures beside the file), and the command works out the rest, checks it end to end in an isolated JARVIS, and only then sends it to the user's package inbox: listed in their backlog and counted in their updates, never shown and never spoken until they ask for it. The command prints its inbox id. Always give `--session`: the user's answers come back to your session as a message, once they have answered and said to send them. Give `--workflow <id>` when the package belongs to a workflow you report (it waits on the answers, which come back as its decisions, read with `workflow_report.py answer <id> <decision id>`), and `--work <id>` when it belongs to a work item (its questions show on that item's Decisions tab). A newer version of a package you sent replaces the older one while it is open. When the user is done with a package they sign it off: whatever they answered comes back to you (unanswered questions marked unanswered), or, for a package that asks nothing, a message that they have reviewed it; either message is read back to them and sent on their yes. Never open, show or answer a package in JARVIS yourself, and never write to the user that one is waiting: the inbox tells them. If anything fails, nothing is delivered: fix what it says and run it again. Never build, zip, attach or deliver a package by hand, and never send a presentation with choices in it but nothing to vote with.

## Messages: what you have to tell them

To tell the user something that is not a decision, question or blocker, such as a finished piece of work or a report, send it as a message. Write one file per message to `{{messages_dir}}/<project>/<short-name>.md`, where `<project>` is the name of your project's folder.

The first line is `# ` followed by the subject in one sentence. The second line is `From: <your session id>`. It is only a hint: JARVIS tells the user who sent it from your own transcript, which must show you writing the file, so write it yourself with your own file tool (a subagent's message is shown as "<subagent> (<its parent agent>)"). A file no listed agent's transcript (or its subagent's) shows being written is not delivered, since the user could not reply to it: JARVIS renames it `<short-name>.held` where you wrote it. If you find your file renamed so, write it again yourself, by its full path. The user can reply to every message you send; the reply comes to your session as an ordinary message. Everything after that is the message.

Write the file once and do not edit it afterwards. JARVIS picks it up within seconds, deletes the file and tells the user a message came in. A file that is still there after a minute has not been picked up, usually because JARVIS is not running.

## Workflows: reporting long work as it goes

When you run a long piece of work in phases, such as a multi-step build or a workflow of several agents, report each step to JARVIS's workflow monitor so the user can see it and JARVIS can always tell them where it stands. This needs an access key in the `workflows` category in `JARVIS_ACCESS_KEY`; the user makes one with `python -m jarvis.access_keys create <label> workflows`.

```
python3 {{workflow_report}} start "<name>" --project <project> --phase <phase> --phase <phase>
python3 {{workflow_report}} event <id> agent_attached "<who is running it>" --session <your session id>
python3 {{workflow_report}} event <id> phase_started "<one sentence>" --item <phase>
python3 {{workflow_report}} event <id> phase_added "<one sentence>" --item <phase> --title "<title>"
python3 {{workflow_report}} event <id> task_added "<one sentence>" --item <task> --title "<title>"
python3 {{workflow_report}} event <id> task_progress "<one sentence>" --item <task> --done 3 --total 8
python3 {{workflow_report}} event <id> decision_needed "<the question>" --key <slug> --option "<a>" --option "<b>" --recommendation "<your pick>"
python3 {{workflow_report}} event <id> wait_started "<what you wait for>" --key <slug> --until decision:<slug>
python3 {{workflow_report}} answer <id> <slug>
```

`start` prints the workflow's id. The kinds are `phase_added` (a phase not given at `start`, added after the others), `phase_started`, `phase_finished`, `task_added`, `task_started`, `task_progress`, `task_finished`, `wait_started`, `wait_ended`, `decision_needed`, `decision_made`, `blocker_raised`, `blocker_cleared`, `agent_attached`, `note` and `workflow_finished` (with `--outcome succeeded`, `failed` or `cancelled`). Every event's summary is one plain sentence. Report at least every few minutes: a workflow that reports nothing for ten minutes shows as gone quiet. `answer` prints the user's answer to a decision once they have given it and exits 3 until then.

Whenever you wait on the user, link the wait to what will answer it with `--until`: `decision:<slug>` (a decision of this workflow, asked first), `work:<item id>:<key>` (a work item's decision), `workflow:<workflow id>:<slug>`, `package:<package or inbox id>` (until they sign the package off) or `package:<id>:<decision id>` (one of its decisions). When the user answers it, by any route, JARVIS ends the wait itself, recorded as their answer; do not report `wait_ended` for it. Never wait on the user with no link: a wait with no link to an answer is flagged on their screen after a day. A wait on something other than the user (a test run, a build) needs no link.

## Work items: who is doing each piece

When you take on a piece of work that lives in a ZIP work package or a folder on this Mac, track it on JARVIS's Work tab, so the user sees its state and exactly who is on each part of it. This needs an access key in the `work` category in `JARVIS_ACCESS_KEY`; the user makes one with `python -m jarvis.access_keys create <label> work`.

```
python3 {{work_report}} start "<title>" --folder <path> --area "<area>"        (or --zip <path>; --area is optional)
python3 {{work_report}} piece <id> <key> "<one sentence>" --title "<title>" --who agent:<your session id> --state working
python3 {{work_report}} piece <id> <key> "<one sentence>" --worker "<who did it, in your words>" --after <key>,<key> --evidence <commit|path|url>
python3 {{work_report}} update <id> "<one sentence>" --state working
python3 {{work_report}} decision <id> <key> "<the question>" --option "<a>" --option "<b>" --recommendation "<your pick>" --package <package id or name>
python3 {{work_report}} piece <id> <key> "<what you wait for>" --who tim --state waiting_on_tim --until decision:<key> --then working
python3 {{work_report}} piece <id> <key> "<what blocks it>" --state blocked --until "<the blocker in words>"
python3 {{work_report}} piece <id> <key> "<one sentence>" --state working --cleared        (a blocker in words is gone)
python3 {{work_report}} piece <id> <key> "<what to check>" --state verifying --evidence <commit|path|url>
python3 {{work_report}} answer <id> <key>
python3 {{work_report}} answers --since <ISO time or epoch seconds>
python3 {{work_report}} withdraw <id> <key> "<why>"
python3 {{work_report}} close <id> "<one sentence>"
```

`start` prints the item's id; the path must be a zip or folder that is there now, and JARVIS never opens or runs it. A state is `planned`, `ready` (nothing holds it; it is yours to pick up), `working`, `verifying` (the work is in, ready for the user's review), `waiting_on_tim` or `blocked`; `done` is the user's approval, never yours to report. Who is on a piece is `agent:<session id>`, `agent:<session id>/sub:<agent id>` (one of your subagents), `run:<run id>`, `workflow:<workflow id>` or `tim`, and must be one JARVIS knows. When a subagent does a piece, put it on that piece as `--who agent:<your session id>/sub:<agent id>`, the agentId the Agent tool gave you when you dispatched it: JARVIS reads that subagent's own files, so the piece shows Working while it is writing, its name, and what it is doing now, and says so once it finishes or goes quiet. A working report on your own session is never shown as Working, since JARVIS cannot see that work. Every summary is one plain sentence, and the latest one is what the Work tab shows; a piece's summary is also that piece's latest note. Items are grouped by `--area` (on `start` or `update`; an empty one goes back to the source's folder). When your subagents do a piece, keep yourself as `--who` and say which one with `--worker`, for example `--worker "logo build agent"`: it is shown beside you as your own words. `--after` names the pieces of the same item this one waits on (an empty value clears it). Each `--evidence` (a commit, a path or a URL) is added to the piece's evidence; give it when a piece is verifying.

Every decision, blocker or question about a work item goes on that item, never on the needs board: a decision with `decision` (`--package` names a page package the user can open from it, by id or name), a blocker as a piece with `--state blocked`, a one-sentence summary and `--until` naming what blocks it. Only the user answers a decision. Only you, the asker, can withdraw one, with `withdraw` and why (for example, it was asked before the user had seen what it is about); it is kept as withdrawn, not as answered, and a new question needs a new key. When the work is abandoned, `close` moves the item to History for good (nothing is deleted, and nothing more can be reported to it); an item the user has approved done moves there by itself a few minutes after. `answer` prints the user's answer once they have given it and exits 3 until then. A decision whose `--package` the user signs off is answered with the answer they gave in that package to the question of the same key (or to its only question); if they signed it off without answering, `answer` exits 4: the decision is closed without an answer, which is not a choice of theirs.

The Work record is authoritative, and JARVIS refuses a report that does not fit it. A piece that waits on the user (`--state waiting_on_tim`) or is blocked (`--state blocked`) is held, and must say what holds it with `--until` (repeat it for several): `decision:<key>` (one of this item's open decisions, asked first), `work:<item id>:<key>`, `workflow:<workflow id>:<slug>`, `package:<package or inbox id>` (until they sign the package off) or `package:<id>:<decision id>`; a blocker may instead be words. Nothing you report moves a held piece on: once every one is answered, by any route, JARVIS moves it to `--then` (`ready` by default) itself; a blocker in words is cleared by reporting the piece's next state with `--cleared`. So never keep working inside a held piece: split the work that can go on into a piece of its own. An item with pieces takes its state from them (working while any piece works, waiting on the user only when nothing can go on), so never report the item itself waiting or blocked. Done is the user's alone: you can never report done. Take a piece or item as far as `verifying` (ready for their review), with `--evidence` of what to check, and they approve it, say it is not done yet (it comes back to working with their note) or add work (a new piece in their words). Their note and any added work are written for you to `{{answers_dir}}/<project>/<item id>-review-<time>.md`.

When the user answers one of your decisions, JARVIS writes it to `{{answers_dir}}/<project>/<item id>-<key>.md` (`<project>` is the last name of the item's source folder, or of the folder its zip is in), and `answers --since <time>` lists every answer to your own questions in one call. Look there instead of asking again, and act on an answer at once: report a piece or update straight away, since an answered decision with nothing reported after it shows the item as waiting for you to start and, after half an hour, as "answered, no follow-up".
