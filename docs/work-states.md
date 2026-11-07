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
| Activity unconfirmed | An agent says it is working on it. JARVIS cannot see the work happening, so it does not claim it is. | "Activity unconfirmed" pill, no animation; hovering says why. |
| Waiting on you | Nothing can go on until you answer: a decision, a held piece, or a package question. | Amber pill and amber edge; the update line is the question; "Decision needed" banner. |
| Blocked | Held by something that is not you (CI, a licence), named. | Red pill and red edge; Blocking names what holds it. |
| Ready for review | The work is in and nothing stands in the way: yours to approve. | Purple "Ready for review" pill; **Approve** shows in the item's header. |
| Done | You approved it. Only you can make anything done. | Green pill; "You approved it: done, *time*"; it moves to History after five minutes. |

Beside the state, the item also says:

| Line | When | What he sees |
|---|---|---|
| *N* decisions also wait on you | Work goes on while decisions are open. | Amber line under the title, on the row and the Overview. |
| What your package answer did | His latest act was in a review package. | "Approved by you in the package, 16:33", "Not done yet, you said in the package, …", "Work added by you in the package, …", "Review closed without a verdict, …" or "Your answer in the package was not applied, …: why", on the row, Overview and Decisions tab. |
| Answered, no follow-up | He answered and nothing was reported for 30 minutes. | Amber line on Blocking, naming the agent; a silent update for the agent. |
| Waiting, no link to an answer | Something was left waiting on him (from before links existed) for over a day. | On Blocking, beside the held piece. |

## Actions

| Action | Who and where | Before | After | What Tim sees change |
|---|---|---|---|---|
| Report a piece working | Agent (`work_report.py piece --state working`) | Planned or Ready | Activity unconfirmed | The pill reads "Activity unconfirmed"; nothing animates. |
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
