---
name: talk-to-jarvis
description: How to reach the user through JARVIS, their voice assistant - write what you need from them to the needs board, and send them a message when you have something to tell them. Use whenever you have a decision, question or blocker waiting on the user, or a finished piece of work or report to give them.
---

# Talking to the user through JARVIS

JARVIS is the user's voice assistant. It watches two folders and tells the user out loud when something you write there arrives. Use them instead of waiting for the user to come back to your window.

## The needs board: what you are waiting on

Whenever you have a decision, a question or a blocker waiting on the user, write it to `{{needs_dir}}/<project>.md`, where `<project>` is the name of your project's folder.

Start the file with `# <project>` and an `Updated:` line with the date and time, then three headings: `## Decisions`, `## Questions`, `## Blockers`. Each item is one line under its heading:

```
- <slug>: <one sentence saying what the user is deciding>. Recommended: <your pick>.
```

Keep it to ten items at most. Rewrite the whole file whenever your list changes, remove an item once the user has answered it, and delete the file when nothing is left. JARVIS tells the user when your file changes and reads each item with its recommendation.

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
python3 {{workflow_report}} event <id> task_added "<one sentence>" --item <task> --title "<title>"
python3 {{workflow_report}} event <id> task_progress "<one sentence>" --item <task> --done 3 --total 8
python3 {{workflow_report}} event <id> decision_needed "<the question>" --key <slug> --option "<a>" --option "<b>" --recommendation "<your pick>"
python3 {{workflow_report}} answer <id> <slug>
```

`start` prints the workflow's id. The kinds are `phase_started`, `phase_finished`, `task_added`, `task_started`, `task_progress`, `task_finished`, `wait_started`, `wait_ended`, `decision_needed`, `decision_made`, `blocker_raised`, `blocker_cleared`, `agent_attached`, `note` and `workflow_finished` (with `--outcome succeeded`, `failed` or `cancelled`). Every event's summary is one plain sentence. Report at least every few minutes: a workflow that reports nothing for ten minutes shows as gone quiet. `answer` prints the user's answer to a decision once they have given it and exits 3 until then.
