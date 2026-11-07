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
{{make_package}} <content.json or folder> --session <your session id>
```

`{{making_a_package}}` is the only description of what to write and what the command does; read it before your first package. In short: write only the substance (titles, questions, options, your recommendation and its reason, pictures beside the file), and the command works out the rest, checks it end to end in an isolated JARVIS, and only then puts it in the user's JARVIS, unshown, and sends them a message naming it and the words that open it. If anything fails, nothing is delivered: fix what it says and run it again. Never build, zip, attach or deliver a package by hand, and never send a presentation with choices in it but nothing to vote with.

## Messages: what you have to tell them

To tell the user something that is not a decision, question or blocker, such as a finished piece of work or a report, send it as a message. Write one file per message to `{{messages_dir}}/<project>/<short-name>.md`, where `<project>` is the name of your project's folder.

The first line is `# ` followed by the subject in one sentence. Everything after it is the message.

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
python3 {{workflow_report}} answer <id> <slug>
```

`start` prints the workflow's id. The kinds are `phase_added` (a phase not given at `start`, added after the others), `phase_started`, `phase_finished`, `task_added`, `task_started`, `task_progress`, `task_finished`, `wait_started`, `wait_ended`, `decision_needed`, `decision_made`, `blocker_raised`, `blocker_cleared`, `agent_attached`, `note` and `workflow_finished` (with `--outcome succeeded`, `failed` or `cancelled`). Every event's summary is one plain sentence. Report at least every few minutes: a workflow that reports nothing for ten minutes shows as gone quiet. `answer` prints the user's answer to a decision once they have given it and exits 3 until then.

## Work items: who is doing each piece

When you take on a piece of work that lives in a ZIP work package or a folder on this Mac, track it on JARVIS's Work tab, so the user sees its state and exactly who is on each part of it. This needs an access key in the `work` category in `JARVIS_ACCESS_KEY`; the user makes one with `python -m jarvis.access_keys create <label> work`.

```
python3 {{work_report}} start "<title>" --folder <path> --area "<area>"        (or --zip <path>; --area is optional)
python3 {{work_report}} piece <id> <key> "<one sentence>" --title "<title>" --who agent:<your session id> --state working
python3 {{work_report}} piece <id> <key> "<one sentence>" --worker "<who did it, in your words>" --after <key>,<key> --evidence <commit|path|url>
python3 {{work_report}} update <id> "<one sentence>" --state working
python3 {{work_report}} decision <id> <key> "<the question>" --option "<a>" --option "<b>" --recommendation "<your pick>" --package <package id or name>
python3 {{work_report}} answer <id> <key>
python3 {{work_report}} withdraw <id> <key> "<why>"
python3 {{work_report}} close <id> "<one sentence>"
```

`start` prints the item's id; the path must be a zip or folder that is there now, and JARVIS never opens or runs it. A state is `planned`, `working`, `verifying` (the work is in and being checked), `waiting_on_tim`, `blocked` or `done`. Who is on a piece is `agent:<session id>`, `run:<run id>`, `workflow:<workflow id>` or `tim`, and must be one JARVIS knows. Every summary is one plain sentence, and the latest one is what the Work tab shows; a piece's summary is also that piece's latest note. Items are grouped by `--area` (on `start` or `update`; an empty one goes back to the source's folder). When your subagents do a piece, keep yourself as `--who` and say which one with `--worker`, for example `--worker "logo build agent"`: it is shown beside you as your own words. `--after` names the pieces of the same item this one waits on (an empty value clears it). Each `--evidence` (a commit, a path or a URL) is added to the piece's evidence; give it when a piece is verifying or done.

Every decision, blocker or question about a work item goes on that item, never on the needs board: a decision with `decision` (`--package` names a page package the user can open from it, by id or name), a blocker as a piece or update with `--state blocked` and a one-sentence summary saying what blocks it. Only the user answers a decision. Only you, the asker, can withdraw one, with `withdraw` and why (for example, it was asked before the user had seen what it is about); it is kept as withdrawn, not as answered, and a new question needs a new key. When the work is finished or abandoned, `close` moves the item to History for good (nothing is deleted, and nothing more can be reported to it); a done item moves there by itself a few minutes after it is done. `answer` prints the user's answer once they have given it and exits 3 until then.
