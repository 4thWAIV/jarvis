# One channel: every message to Tim is one kind of thing, and it goes to its Work item first

Status: proposal for Tim's review (2026-11-10), revision 2: his two revisions (decisions as packages; one PR in three builds) are folded in. It replaces the scope of the earlier one-channel plan (`docs/planning/one-channel/design.md` on the `docs/one-channel` branch), whose phases 1-3 are merged. That plan kept packages, needs boards, Work decisions and people's messages as separate things that write into a shared record. This one makes them all the same thing: a message.

## The problem today

An agent can reach Tim six different ways, and each shows up somewhere different:

| Way an agent reaches him | Where he sees it today |
|---|---|
| Session message file (`data/messages/<project>/*.md`) | A line in Updates waiting (centre) |
| Needs board file (`data/needs/<project>.md`) | A line in Updates waiting; the brain reads it on request |
| Work item decision (`work_report.py decision`) | The item's Decisions tab, its row, a line in Updates |
| Package (`make_package`) | The Queue tab's Packages section, a line in Updates, the item's Decisions tab when sent with `--work` |
| Workflow decision or blocker | The Workflows view under the Work tab |
| An agent's answer to his message | The agent's thread, a line in Updates |

A teammate's message (a person) comes through the Queue tab. Nothing puts these in one place, and nothing sends a message about a piece of work to that piece of work.

## What he will see

### 1. A message is one thing

Every message to him has the same shape, whoever sent it and whatever it carries:

- **From:** the proven sender (an agent by its signature, a person by the team server, JARVIS itself).
- **About:** the Work item it belongs to, or none.
- **Says:** the text, short.
- **Carries** (optional): a package, opened in the centre with Open.
- **Decision** (optional): only inside a package. A message that needs his decision always carries a package with the background he needs: what the choice is, each option shown as what he would see, what happens either way, and the agent's pick with its reason. The route refuses a decision without one.
- **State:** new, read, answered, done.

A package is a message that carries a package. A decision is a message carrying a decision package. A needs-board entry is a message that belongs to no item (a decision among them carries its package). There is no bare question anywhere: "Do you approve the plan?" is never asked unless the package shows the plan. An agent's answer is a message from that agent. There is no separate package inbox, needs board or decision list.

### 2. On the Work item: the Messages tab (first tab)

Every message about an item is in that item, in time order, as a conversation:

```
┌ Work tracker (this view) ─────────────── Ready for review ┐
│ [Messages 2]  Overview  Pieces  Blocking  Reports          │
│                                                            │
│  Diagnostics · 14:02                                  NEW  │
│  What is left on this item. Two pieces are open.           │
│  ┌ decision package: How far should one channel go? ┐      │
│  │ 9 slides of background · 1 question   [Open]      │      │
│  └───────────────────────────────────────────────────┘      │
│                                              [Reply]       │
│                                                            │
│  You · 14:20                                               │
│  "Not good enough: where is the design?"                   │
│                                                            │
│  Diagnostics · 14:41                                  NEW  │
│  The design is ready for your review.                      │
│  ┌ package: One channel by Work item ─────────── [Open] ┐  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

- His answers and replies are in the same thread, so he sees what he said and what came back.
- The Decisions tab goes. A decision is a message carrying its package; Open shows the background and the vote in the centre, and his answer is recorded in the thread ("You chose: Also retire the files"). Open decisions are counted on the tab (`Messages 2`) and on the row.
- The item's row in the Work list shows the count of new messages beside its state, and the first new message's first line as its update.

### 3. Messages that belong to no item: Inbox

The right panel's Queue tab becomes **Inbox**. It holds, in one list, newest first:

- each Work item with new messages, as one line ("Work tracker (this view): 2 new"), which opens that item's Messages tab;
- the messages that belong to no item (what was the needs board, and anything general), each with Read and Reply, and Open when it carries a package;
- people's messages (teammates), in their threads, as today.

The Packages section and the needs boards are gone: a package is in its item's thread, or in Inbox when it belongs to no item.

### 4. In the centre: one count

Updates waiting shows one line, "3 new messages", with Open, which goes to Inbox. JARVIS's own facts (a run finished, CI, communications down) stay in Updates, because they are not anyone's words to him.

### 5. By voice

"What's new?" reads the same thing in the same order: each item with new messages, then the general ones. "Read it", "Open it", "Reply" act on the message he is on (a decision package's story is told and waits for his answer, as packages do today), through the same functions as the buttons.

## How a message gets to its Work item

- An agent sends every message through one signed request, `POST /api/messages`, with `work` (the item id), `says` and optional package bytes (a decision package for any decision). The signature proves the sender, as packages do now.
- `work` is required whenever the agent has a piece on an open item; JARVIS refuses a message without it from an agent that has exactly one open item and names it in the refusal. An agent with no open piece sends with no `work`, and the message goes to Inbox.
- A work-item key (from a package sent with `--work`) can only send messages about its own item.
- Workflows send the same way. A workflow's decision is a message from its reporter carrying a decision package, and a blocker or wait on him is a message, about the Work item the workflow is doing (`workflow_report.py` takes `--work`, and a workflow is started for one item). A workflow for no item goes to Inbox. The Workflows view keeps showing phases and progress, but no longer holds anything that needs him.
- His reply goes back to the sender as a message to that agent, through the existing read-back and his yes.

## What goes away

- Message files (`data/messages/`) and needs-board files (`data/needs/`), their watchers, `.sig` files and holds: replaced by the signed route.
- `work_report.py decision` and `withdraw`, and the script provisions around them (`--package` links, the follow-up and review helpers): a decision is a decision package sent as a message; withdrawing is a message that closes it. The answer's record stays.
- Workflow decisions and blockers shown in the Workflows view and added to the needs board (`with_decisions`): become messages carrying decision packages on the workflow's Work item.
- The package inbox as its own list (`QueuePackages.tsx`, its Updates line): a package is delivered as a message (`make_package` posts it to `/api/messages`).
- The Updates message lines (`MessageLine`, `PackageLine`): one count.
- The Decisions tab: folded into Messages.
- The talk-to-jarvis skill's two folders: it teaches one command, `python -m jarvis.message --work <id> "<text>" [--package <zip>]`, and that every decision is a package made with `make_package`.

What stays as it is: the conversation record underneath (phases 1-3), signatures and agent keys, the read-back and his yes before anything is sent, people's messaging through the team server, attention's rule for what is spoken.

## The work

Estimates are agent time (a phase is 1-2 agent hours) plus CI (about 40 minutes a run).

1. **The message and its route.** One record shape on the conversation log (from, about, says, carries, state); decisions only inside a carried package; `POST /api/messages`, signed; the `jarvis.message` command. Existing session messages, board entries, decisions and packages are written as messages from now on. 2 agent hours.
2. **The item's Messages tab and the row count.** `WorkDetail.tsx` Messages tab with Open and Reply in place, and his decision recorded in the thread; the Decisions tab removed; the row's count. 2 agent hours, with page tests.
3. **Inbox.** The Queue tab becomes Inbox: items with new messages, general messages, people's threads; the Packages section removed; Updates shows one count. 2 agent hours, with page tests.
4. **Agents move over, old paths removed.** The skill rewritten and reinstalled for every agent; `make_package` and `work_report.py` send through the route; files, boards, watchers and their holds removed. 1.5 agent hours.
5. **Voice.** "What's new", read, answer and reply over the same record. 1 agent hour.

About 8.5 agent hours. One PR built in three rounds, so three CI builds (see "How it is built" below); each round is a piece on the Work item.

## The one question for Tim

Is this what he wants to see: each message in its Work item's Messages tab first and the rest in one Inbox, every decision as a package with its background, built as one PR in three builds?

## Tim's revisions (2026-11-10, from the 1.0.1 package; folded in above)

- Decisions are not asked as bare questions on Work items. The work_report.py decision path, and the script provisions added around it, do not work well.
- Every decision reaches him as a package with its background: what he needs to know to decide, shown rather than described, with the noise filtered out.
- A question such as "Do you approve the plan?" is never asked unless the plan itself is shown to him in that package.

## How it is built: one PR, child branches, three CI builds (Tim, 2026-11-10)

- One branch, `feat/one-channel`, and one PR from it to main for the whole build.
- Each round of work is done on a child branch off the PR branch (`feat/one-channel-1`, `-2`, `-3`).
- Round 1: done and tested locally on its child branch, merged into the PR branch, pushed. The PR opens and CI runs (build 1).
- Work goes on meanwhile on the next child branch, never waiting on CI.
- When a round is ready: read the PR's CI. If it passed, merge the round into the PR branch and push (the next build). If it failed, fix the failure on the PR branch first, merge that fix into the child branch, reconcile both, and push once everything is cross-merged.
- Three pushes in all, so three CI builds: round 1 (the message record, its signed route and the command), round 2 (the Work item's Messages tab and Inbox on the page), round 3 (agents moved over, old paths removed, voice).
- Nothing is pushed while CI runs on the PR, and no run is cancelled.
