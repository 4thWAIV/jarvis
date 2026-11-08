# Work item states

One table of what a Work item (and each of its pieces) can be, what that
means to Tim and how it looks; then every action, from him or from an agent,
with the state before, the state after and what he sees change. The record
(`jarvis/work_store.py`) works every state out from its events; nothing on
the page is a word an agent typed into a state.

## States

| State | What it means to Tim | How it looks on the page |
|---|---|---|
| Planned | Work is lined up; nobody has started it. | Grey "Planned" pill. |
| Ready | Nothing holds it up any more (his answer is in); its agent has yet to pick it up. | Blue "Ready" pill. After his answer with no report since: "Answered — waiting for *agent* to start". |
| Working | A subagent on the piece is writing right now: JARVIS sees its own files. | Blue "Working" pill that glows; under the piece, "*Release agent* is working now · Using Bash" (its last tool or line). |
| Activity unconfirmed | An agent says it is working on it. JARVIS cannot see the work happening, so it does not claim it is. | "Activity unconfirmed" pill, no animation; hovering says why. On a subagent's piece: "*name* has finished", "*name* has not written anything for a while", or "*name* is waiting on its own background work" (its turn is over but a command or monitor it started is still running). |
| Waiting on you | Nothing can go on until you answer: a decision, a held piece, or a package question. | Amber pill and amber edge; the update line is the question; "Decision needed" banner. |
| Blocked | Held by something that is not you (CI, a licence), named. | Red pill and red edge; Blocking names what holds it. |
| Ready for review | The work is in and nothing stands in the way: yours to approve. | Purple "Ready for review" pill; **Approve** shows in the item's header. |
| Done | You approved it. Only you can make anything done. | Green pill; "You approved it: done, *time*"; it moves to History after five minutes. |
| Stalled | Nothing waits on you and nobody is moving it: no claim of work heard from in ten minutes. | Orange "Stalled" pill; "Stalled: nobody is working on this"; a silent update. A piece claimed working with no report for a day shows "Stalled" too. |

Beside the state, the item also says:

| Line | When | What he sees |
|---|---|---|
| Needs you | Anything waits on you. | At the top of the item, every act and its place, the first first ("Answer *question* on the Decisions tab" with Go there, "Open the *package* review package" with Open, "Approve, Not done yet or Add work in this item's header"); the first one in its list row as "You: …"; `work_status` says the same words. |
| *N* decisions also wait on you | Work goes on while decisions are open. | Amber line under the title, on the row and the Overview. |
| What your package answer did | His latest act was in a review package. | "Approved by you in the package, 16:33", "Not done yet, you said in the package, …", "Work added by you in the package, …", "Review closed without a verdict, …" or "Your answer in the package was not applied, …: why", on the row, Overview and Decisions tab. |
| Answered, no follow-up | He answered and nothing was reported for 30 minutes. | Amber line on Blocking, naming the agent; a silent update for the agent. |
| Waiting, no link to an answer | Something was left waiting on him (from before links existed) for over a day. | On Blocking, beside the held piece. |

## Actions

| Action | Who and where | Before | After | What Tim sees change |
|---|---|---|---|---|
| Report a piece working | Agent (`work_report.py piece --state working`) | Planned or Ready | Activity unconfirmed | The pill reads "Activity unconfirmed"; nothing animates. |
| Put a subagent on a piece | Coordinator (`piece --who agent:<session id>/sub:<agent id> --state working`) | Planned or Ready | Working while the subagent writes | The piece glows "Working" with the subagent's name and what it is doing now; once it finishes or goes quiet, the piece says so and reads Activity unconfirmed. |
| Report a piece ready for review | Agent (`--state verifying --evidence …`) | Activity unconfirmed | Ready for review (once nothing else is open) | Pill turns purple, **Approve** appears in the header. |
| Report a piece waiting on him | Agent (`--state waiting_on_tim --until decision:<key>`) | any but held | Waiting on you | Amber pill; Blocking names the decision it waits for. Refused if it names nothing. |
| Report a piece blocked | Agent (`--state blocked --until "<what>"`) | any but held | Blocked | Red pill; Blocking names the blocker. |
| Try to move a held piece on | Agent | Waiting on you or Blocked | unchanged | Nothing: refused, naming what holds it. |
| Clear a blocker | Agent (`--cleared`) | Blocked | the state given | Red goes; the new state shows. |
| Report done | Agent | any | unchanged | Nothing: refused. Done is his alone. |
| Ask a decision | Agent (`work_report.py decision`) | any | Waiting on you (if nothing else can go on) or unchanged with "1 decision also waits on you" | Banner "Decision needed", Decisions tab counted. |
| Answer a decision | Tim: Decisions tab, or by voice | Waiting on you | Ready (the pieces that waited on it) | Banner goes; pieces that waited read Ready; "Answered — waiting for *agent* to start". |
| Withdraw a decision | Agent that asked it | Waiting on you | Ready | It moves to the answered list as withdrawn; pieces that waited read Ready. |
| Approve | Tim: header, or by voice (`review_work`) | Ready for review | Done | Green "Done", "You approved it: done, *time*". |
| Not done yet | Tim: header, or by voice | Ready for review or Done | Activity unconfirmed (back to work) | "Not done yet, you said: *note*"; the agent gets the note. |
| Add work | Tim: header, or by voice | any | Ready (the new piece) | A new piece in his words; the agent is told. |
| Answer a review package: approve | Tim: package by click, voice or Decisions tab | Ready for review | Done | "Approved by you in the package, *time*". If it cannot be applied: "Your answer in the package was not applied: *why*". |
| Answer a review package: not done / add work | Tim: package | Ready for review | Activity unconfirmed / Ready | "Not done yet, you said in the package…" / "Work added by you in the package…". |
| Sign off a review package unanswered | Tim: Done on the package | Ready for review | unchanged | "Review closed without a verdict", with Approve, Not done yet and Add work beside it (said nothing if he reviewed it another way since). |
| Restart JARVIS | — | any | answers already on record applied once | The items move as if he had just answered. |
| Nobody reports for ten minutes | — (time passing) | Activity unconfirmed, Ready or Planned, nothing waiting on him | Stalled | Orange "Stalled: nobody is working on this"; a silent update. |
| An agent reports again | Agent | Stalled | the state its report gives | The orange goes; the silent update is resolved. |
