# The new JARVIS workspace

Updated 2026-11-17. This guide describes functionality present in the checkout. The [roadmap](roadmap.md) describes the development direction and successor engines.

## Agents ask before they guess

An agent's package is now made as a job: the agent gets on with its work
while a fresh author researches and writes the package, using JARVIS's own
records of what you said and decided. When the author needs a fact only the
sending agent has, it asks that agent and waits for the answer, then goes on
in the same session. Anything still unknown after two rounds of questions
reaches you marked UNANSWERED instead of being guessed.

## Every package that asks you something opens on where things stand

A deck that asks you to decide something now opens on a slide JARVIS draws
itself: what it is about, what happened and why, what you already approved
that it touches, what changes for you if you say yes, and the bottom line.
An agent cannot send one without all four. Before it reaches you, a second
fresh reader that was not there is given only the slides before the first
question and must be able to say what it is about, what you approved
before, what happened, what the options are and what changes under each,
what is recommended and what you are asked. What it cannot answer goes back
to the author once; a package a reader still cannot follow never reaches
you.

## JARVIS lays out every deck and proves it fits

Decks are no longer laid out by the agent that wrote them or checked by
photographing them in a browser. The agent writes the words, pictures and
questions; JARVIS draws every slide in its own layouts and font and works
out, before anything is sent, that every word and picture fits at every
size the package view can be, from the smallest centre view to a whole
screen. Your JARVIS works it out again when a deck arrives and opens none
that does not fit. A page an agent made by hand, which JARVIS did not lay
out, says "Layout not checked" in its bar.

## Every slide of a deck is in its navigation

A deck's Chapters, Previous and Next now go through every slide the deck
draws, numbered as the deck numbers them: each decision's explanation and
its question are separate entries, each question marked, and the Approve or
Reject slide is reachable too. Before, a decision's explanation and its
question shared one chapter that opened on the explanation, and Next went
straight past every question slide.

## You can reject any package

Every package now ends with Approve and Reject on the package itself, even
when it asks its own questions, and those two buttons sit in the bar above
the package for a deck as well as a page. You can turn down a package
because it is not good enough, with your words on what to change, without
answering its questions first. Architecture packages that ask you to
approve the design keep that question in the vote's place.

## JARVIS sums up a whole message, however long

Asking JARVIS to read you a message used to give him only the first nine
hundred or so characters of it, so a long message from an agent or a
person was cut short. He now takes a long message a page at a time and,
once he has every page, tells you who it is from and what it is about and
sums up the whole of it: what it says, what it asks of you and any package
it carries. The Read button and asking him in your own words do exactly
the same. A message counts as read only when you have heard his summary to
its end.

## An agent can send you a package about no Work item

An agent that has work open can still send you a package or message that is
about none of it: it says so with `--general`, and it lands in your Inbox
instead of on a Work item. Leaving out both the Work item and `--general` is
still refused, so an agent that forgot to name its item is still told to.

## A deck fills its frame, and the border is its one navigation

A decision deck or presentation now lays its slides out to the whole
frame, edge to edge at any size, instead of drawing a fixed slide scaled
into the middle of it: on a wide display its pictures and options take the
extra room. Words stay between 13 and 25 px, so they are never tiny in a
small frame or huge in a large one.

The deck no longer draws its own slide numbers along the top or Previous,
Next and a status line along the bottom. The border above it is the one
way to move: its Previous, Next and Chapters change the deck's slide, and
its count always says the slide you are on, however you got there (the
arrow keys, the story, a question JARVIS is waiting on). How many
questions are answered is on each question's slide and on the answers
slide, a save shows under the frame, and an answer that needs your words
says so on its own slide. A deck opened outside JARVIS keeps its own bars.

## A package's presentation uses the whole display, and each question opens beside its part

A package now opens taking over the page: everything but the slim rail of
tab icons on the right goes to its presentation, the left column folding to
the orb alone at the page's edge (Team server and Brains move into the bar
along the top, and Restore layout brings the column back). There is no
side column any more: the presentation takes the whole width below its
icon border, and with nothing open nothing covers it. The border dims
after a few seconds (still there, every button still works) and brightens
when you move the pointer to it or tab into it.

Questions open on demand, one card at a time. Questions, in the row under
the border, opens the first one you have not answered; when the story
waits on a question, its card opens by itself. The part of the
presentation the question is about stays lit and everything else dims,
and the card beside it, joined to it by a line, carries the question, why
it is yours, the evidence, the options (the recommended one marked), a
field for your words, and Approve and Reject for the package itself. A
question about no one part gets its card in the middle of the view. The
card says "Question 2 of 3", with Back and Next between them; Next after
the last, or Escape, closes it and gives the keyboard back where it was.
An architecture package's explanation opens the same way, as a card in the
middle of the view that scrolls on its own. While JARVIS tells the story,
each part he is talking about is lit and his words are shown beside it,
with Pause and Stop.
## A package's author check is found wherever it is

JARVIS confirms a package's fresh author checked it by reading that
author's own transcript. It read only the last 8 MB, so an author whose
screenshot review came after its check had its package held although the
check was there. It now reads the whole transcript a line at a time and
finds the check wherever it is; a 30 MB transcript takes about a tenth of a
second.
## Every package asks Approve or Reject; Got it is gone

A package that asks you nothing of its own now ends with Approve or Reject
instead of Got it. Approve records at once. Reject opens a field for what
to change, and goes only with your words, which go back to the agent that
sent it; on a package sent for a Work item they are also on that item, as
your answer. In Spanish they are Aprobar and Rechazar. A package that
already asks for your approval (a review of a Work item, a merge, an
architecture design) asks it once, with no second vote.

Got it can no longer be offered, chosen or recorded. A package that arrived
before this and still waits on Got it asks Approve or Reject in its place,
and every other question in it works as before; an answer you already gave
to Got it stays on record as you gave it.
## Status briefings lead with what needs you, built from the Work record

An agent preparing a status presentation on your Work items now builds it
from the items' own records, not from what it remembers:
`python -m jarvis.status_briefing --work <id> --out <folder>`. For each item
the presentation starts with what needs you (decisions, packages waiting,
held and stalled pieces, who is on each and since when), then what is
moving, then what finished since a given time, and every fact carries its
evidence: the commit, pull request or file the agent reported. An item that
needs nothing of you says so in one line. `--check` shows first which
pieces have no note or no evidence on record.
## Work that is stuck has an owner and a way out, on the page and by voice

A piece of work held on something nobody will answer no longer needs its
agent or a terminal. A Work item's header has Release hold (a held piece goes
on), Reassign piece (to another agent on your list) and Close item (it moves
to History), beside Approve, Not done yet and Add work; JARVIS does the same
when you ask by voice, and each is recorded as yours. A hold with no link to
an answer that has lasted over a day, and a stalled item, now show in Updates
waiting naming who holds it.

A message waiting in the queue behind an earlier one has Send now and Cancel
on the agent's thread (and by voice). Send now still waits while the session
is in the middle of a turn, and says so.

A message to a person that could not go after you approved it now shows as
failed in the Inbox, with the reason and Retry, and in Updates waiting,
instead of sitting as approved forever.

Diagnostics now watches the Work view and the agent keys' loop: a Work view
that could not be built shows on the Work tracker row and is tried again.
## More things that block work now tell you and can be fixed from the dashboard

Diagnostics has a new Setup row listing every startup check that did not
pass: the talk-to-jarvis skill your agents need to reach you, Accessibility,
Screen Recording and the rest, each in plain words. When the skill is
missing or out of date, Setup has Install skill, and JARVIS installs it
when you ask by voice; no terminal command is needed.

When messages between people keep failing to get through, the Team server
row says so (it never turns red for it) and an update appears in Updates
waiting, gone once a delivery succeeds. When the knowledge engine's file
reader keeps crashing and restarting, the Knowledge engine row is degraded
and says how often and with what exit code.

A run you started from the dashboard that fails or runs out of time is now
an update too, as runs JARVIS starts always were. If you chose Spanish but
its speech model is missing, so JARVIS started in English, an update says
so until the language changes. A work-item key a few days from its 30-day
end is an update naming its Work item, and the item's Overview has Renew
beside Revoke (JARVIS renews it by voice too). A workflow blocker reported
by something other than an agent's own key is an update on the workflow,
cleared once the blocker is.
## A new language kit no longer voids your architecture approval, and says when it arrives

The architecture language kit is published again on every change its
publisher makes, and most releases change nothing in the language itself.
Your approval of an architecture design used to count for nothing as soon
as any new kit came into force, so the package had to be made and asked
again with nobody telling you why. Now your approval, and a question still
waiting for it, stand while the new kit's language files (`metalang.yaml`,
`arch.yaml`, `propose.yaml`) are the same as those the design was checked
under. A kit that changes any of them still makes the approval stale, and
that kit waits for your review in Settings, Updates as before.

When JARVIS brings in a new kit by itself, it now tells you: one update in
Updates waiting (shown, never spoken) naming the kit now in force and the
one it replaced, so a kit never changes unseen.
## JARVIS no longer rewrites records when nothing changed

Every record keeps every version, and a few writers rewrote whole records
when only a time had moved, so their history grew without end (one day's
usage record reached hundreds of megabytes). Now each writes only when
something you can see changed: a weekly usage reading is kept when its
figures change, or once every ten minutes otherwise, so the burn-down
still has a point every ten minutes; the messaging sync position is
written only when it moves; and the folder catalog's "last checked",
"seen" and discovery times are kept in memory, still shown as they are,
and written at most every fifteen minutes.
## The knowledge engine stops timing out on records with long histories

The earlier fix read a record's newest version without its history, but
only on some paths: reading one record through the engine's item route,
and every list of records, still unpacked every version each record has
ever had. Listing the brains' usage for the day took nearly five seconds of
the engine's time, reading those 24 records eight, and the server asks for
such lists many times a minute, so the engine sat at full CPU and the
page's and the brain's reads timed out. Every read of the newest records
now leaves the history alone: on a copy of the live records the same set of
lists and reads costs the engine 2.4 seconds instead of 14, and the usage
records are read in 0.07 seconds instead of 8. When the engine is busy,
its health now says which requests keep it busy (each kind of request's
count and time over the last five minutes), and `kill -USR1` on the engine
writes what every one of its threads is doing to
`knowledge-engine-stacks.log` beside its log.
## Decks use the whole slide

A deck's words grow with the space it has, and a slide with pictures gives
them every pixel its words leave, so a picture is as large as the slide
allows instead of a small box.

## A package's page keeps most of the view

A package opens with its own page filling the centre. The story's
controls and an architecture package's links to its source, checker
evidence and explanation share one row above it, and its questions and
explanation open in a column beside it, so
answering a question or reading the explanation never shrinks the
document you came to read.

## Nothing stops in silence: every stop says why and offers a way on

When JARVIS cannot start on its records, the page now says so instead of
showing nothing. If the knowledge engine that holds the records does not
answer, JARVIS stays up and the page covers the workspace with "JARVIS could
not open its records", why (the engine's own reason and the end of its log)
and a Try again button that starts JARVIS again. A records move that failed,
or a start that failed after the records opened, shows the same Try again;
you no longer need a terminal to retry.

A brain that stopped (its login expired, or it crashed too often) now has
Start brain on its Diagnostics row, and JARVIS starts it when you ask by
voice. Signing a brain in again from Settings starts it again by itself.

Diagnostics has a new Updates row: if the record that holds the updates
meant for you cannot be opened, the row is down and says why, rather than
updates going missing without a word.

## A language kit waiting for your review tells you, and you activate it in Settings

When a newer architecture language kit changes its language files, JARVIS
keeps the working kit and holds the new one for your review. It used to say
so only in a status file; now it is an update in Updates waiting (shown,
never spoken), naming the kit's version and the files that changed, once
per kit rather than at every six-hourly check. Settings, Updates shows the
held kit and why, with Activate: press it, confirm, and the new kit is
active; documents already written stay on the kit they were checked with.
You can also tell JARVIS to activate it. The update goes away by itself
once the kit is active, and the status no longer claims a review is
needed for the kit already running.

## The knowledge engine no longer slows down as records are rewritten

Every record keeps all of its versions, and some are rewritten every few
seconds or minutes (the brains' usage for the day, folder checks, the
message sync position). Reading such a record used to unpack its whole
history, hundreds of megabytes for one day's usage, so the engine got
slower with every write until the page and the brain's reads timed out
while the engine still said it was running. Reading a record now reads
only its newest version: a folder discovery pass costs the engine a
quarter of what it did, and a read of the day's usage takes milliseconds
instead of seconds.

## Package checks run while the screen is locked

The check every package passes before it reaches you now keeps the display awake, and wakes it if it is asleep. While the display slept under a locked screen the browser's audio stopped, so a package built overnight failed its check for no fault of its own.

## A start setting the page cannot reach goes back by itself

When you change the address, port, HTTPS or the page's extra origins in
Settings, Server and restart, JARVIS now tries the new setup and keeps the
last one that worked to fall back on. If no page reaches JARVIS at the new
setup within 60 seconds, it goes back to the one that worked and restarts
on it, so you are never locked out. Settings, Server counts down while a
new setup is being tried, and says when JARVIS went back, from what, to
what and why; Updates waiting and Diagnostics (the Server row's Start
setup) say so too, and JARVIS answers the same when you ask about its
settings. Opening the page at the new setup confirms it. The command-line
`--host`, `--port` and `--ssl` are still for one run only and are never
tried or kept.

## JARVIS reads you your messages himself, and finds the one you mean

Read on a message no longer plays it past the conversation. It asks JARVIS
for you, "Read me message 42.", which shows in the Conversation as your own
request; JARVIS reads you the message and sums it up in a sentence, so when
you ask about it afterwards he knows what you heard. It is marked read only
once you have heard his reply to the end; cut him off and it stays new. If
JARVIS's brain is down, the reply says why instead.

You can name a message by when it came: "read the message from 3:42", "the
one at 15:42 from Bridge", "mark yesterday's 9:05 done". The time is your
Mac's own time, to the minute, with "today" or "yesterday" if you like.
JARVIS reads, replies to, marks or opens exactly that message; when two fit
(the same minute, or 3:42 in the morning and the afternoon) he asks which,
and when none does he says so and names the nearest.

Read-backs for your approval (a message before it is sent, a command, a
keypress) and a package's story are still read word for word, and JARVIS
now knows what was read and how it went, so you can ask him about it.

## Work reports answer at once, and land once

An agent's report to the Work tab no longer waits while JARVIS brings the
Work list up to date: the report is written and answered at once, and the
list catches up a moment later on its own. A report whose answer is slow to
come back is no longer taken for failed: every report carries a key of its
own, so the agent asks JARVIS whether it landed and sends it again only if
it did not, and JARVIS never records the same report twice. The same holds
for workflow reports and agents' messages to you.

Diagnostics has a new row, Work tracker, showing how long reports and reads
of the Work list have taken in the last five minutes. It turns amber once
one takes over two seconds, and Updates notes it if a report takes over
five.

## Closing or replacing a package ends what waited on it

Setting a package aside now ends everything that waited on it, as signing it
off always did: a Work item's piece waiting on the package moves on, a
workflow's wait on it ends, and the item's "Is it clear?" question about it
closes, shown as closed when you set its package aside rather than as an
answer. When an agent sends a newer version that replaces an open package,
whatever waited on the old one now waits on the newer one, which is the one
you can open, and the newer one stays with the same Work item. Waits left
open by packages closed or replaced before this update are cleared the next
time JARVIS starts. An agent may end a wait it placed itself; a wait JARVIS
or another agent placed still ends only with your answer.

The check a decision package passes before it reaches you now measures
every picture on every slide, not only the options' pictures, at both
screen sizes. An SVG picture must say its size (width and height in pixels
on its `<svg>`); one that does not is refused before the package is built,
since it can show as a blank slide.

## Every setting is in Settings

Everything you can choose about JARVIS is now a control in Settings, and
nothing asks you to edit a `.env` file or set an environment variable. New
sections hold what used to live only in `.env`: Agents (how long a run may
take or stay silent, skipping permission prompts, the time to cancel a send,
the session homes JARVIS watches), Projects (where new projects go and where
JARVIS looks for them) and Safety (refusing to act after reading others'
words). You gains the weather place and units and the browser JARVIS opens;
Voice the speech models folder and muting the microphone while JARVIS
speaks; Brains the effort, turn limit, compaction point, program paths and
usage reading; Knowledge the run context, the folder catalog and the engine's
memory; Team the local team servers; Server the address, port, HTTPS,
starting the brain, extra page origins and the API console, with Restart now.
A start-up setting says it takes effect at the next start, and says so again
once changed. If a new address or port cannot be used, JARVIS goes back to the
last one that worked. Ask JARVIS about any of them, or to change one; the
program paths, the address, the extra origins and the Fish key are changed
only by your click. The Fish key is kept in your Keychain. The first start
copies whatever your old `.env` held into Settings, once, and says when in
Settings → Server; after that the file is not read.

## A package opens with its purpose, and every decision with its lead-in

Every deck an agent sends you now opens by saying what it is for and its
answer or what it asks, before any data: its first slide shows the
conclusion and JARVIS says the purpose over it. A deck that opens on a
table, a set of figures or a question is refused before it reaches you.
Each decision now comes after a slide of its own, "Before you decide",
giving the question, why it is yours to decide and not an agent's, and
the facts it rests on; a decision without them is refused too. An
architecture package's explanation opens the same way.

## The knowledge index builds itself, and says how far it is

Settings → Knowledge (and the Knowledge view) shows the index being built:
which pass it is on, how many items and how many sections have their
vectors out of how many, and about how long the rest will take; "paused while you talk" while you
and JARVIS are speaking, and "paused in Settings" when you pause indexing
there. It builds your messages, conversation and Work first, newest first,
then JARVIS's notes, then your folders' files, most recently changed first.
The first pass gives every item one vector, so everything can be found by
meaning early; each pass after it adds the next sixteen sections of every
file, so coverage rises evenly across files, until every section of every
file is indexed. Nothing is left out, however long the file; a file that
changes goes back to the first pass.
It uses half of one processor core whatever else the Mac is doing, so it
always moves and never slows the Mac, and a restart carries on where it
stopped. A search made while it is
still building says that meaning search may miss what is not indexed yet.
Ask JARVIS "how far is the knowledge index?" for the same figures.

JARVIS's server also does far less on its own when no page is open: it no
longer reads every package, the whole package inbox or the whole voice
record every few seconds, and it asks each Codex session for its folder once.

## Agents on Codex as well as Claude

JARVIS can start an agent on Codex as well as on Claude Code: an agent run, a build or an agent package goes on the Codex app server already running on this Mac, in your own Codex setup, never asking and with no sandbox, as Claude agents run. JARVIS watches it, stops it and retries it on the same engine. Say "on Codex" or "on Claude" for one agent; otherwise Settings → Brains decides, under "Who runs each kind of agent": for the package author, agent runs, builds and agent packages, a brain and one of its models, the same as the agent asking, or the brain with the least token stress. Token stress is the plus or minus the brain board shows against the week's pace, shown there beside each brain; "Override if the chosen brain is in token stress" moves an agent off a brain that is behind its pace. JARVIS refuses to start a Codex agent on an app server version it has not been checked against, and says so.

## Two appearances across the app

Settings → Server → Appearance offers Classic Refined, a quiet navy palette, and
Graphite, a neutral dark palette with a soft blue-violet accent. Conversation,
folders, agent threads, Queue/review, workflows, Network, Diagnostics, settings
and the run dashboard share readable text, surfaces and focus treatments. Tim's
layout, type sizes, controls and interactions remain in place. The orbs and
semantic state colors keep their identities.

The choice is saved in this browser, applies before the page is drawn and follows
changes made in another JARVIS tab on the same origin. It does not change server
settings or anyone else's appearance.

## Architecture packages that walk you through

An architecture package now states the purpose its sender gave, and its
author's explanation opens in place above the page (Read the explanation),
drawn as formatted text with safe links rather than as raw Markdown in a new
tab. Its author may also write a walk-through JARVIS tells over the rendered
page and ask real questions, not only Got it: each question appears in the
package's head with its options, is answered by a click or by voice, and
follow-up questions appear once an answer opens them. The standard vote is
still asked once, last. Signing off early keeps every unanswered question
marked unanswered. When its sender asks for it, an architecture package also
asks for your approval of the design, apart from Got it and first in its
head: Approve, or Request changes with what to change. Your answer is kept
with the exact checked revision you gave it to, and once a newer revision or
kit is checked it is shown stale and counts for nothing. Pointing at a declaration or a member needs a kit whose
page loads the story helper; until then a walk-through narrates without
pointing.

## Conversation is the main view

The centre opens on Conversation. It combines earlier conversation with live speech, typed input, captures and expandable sources. Agent messages have delivery and reply states, so their progress can be followed in the conversation. Selecting an agent's name opens that agent's thread.

The left column holds the orb, the team server's health and brain account usage. The microphone controls (Start listening, Mute, Mic off) sit at the lower right of the Conversation's message box, right of Send, with Stop and the voice health dot to Send's left (on any other centre view the microphone controls stay in the same lower-right corner); what JARVIS is doing (thinking, speaking) is the row above the message box. The right panel has six labelled tabs in two rows: Folders, Agents and Queue, then Work, Network and Diagnostics. Detailed views open in the centre while the surrounding controls remain available.

## Your records move into the new engine once

The first start after the update that brought the knowledge engine's records
moves them out of `data/jarvis.db` into the engine (`data/knowledge`): it
copies every table, checks the copy against the file, and checks that the
file can be made again from the engine. On a large file this takes a few
minutes (about two and a half for 240,000 rows). JARVIS answers meanwhile: the
page shows "Moving your records into the new engine", the step it is on
(copying, checking, checking the way back) and about how long is left, and
JARVIS's voice and agents wait until it is done. JARVIS first checks there is
enough free disk for the engine and its temporary copies, and refuses with how
much it needs if there is not, before anything is written.

`data/jarvis.db` is never changed by the move; it stays beside the engine.

**A slow Mac no longer stops the move.** A busy machine can make one write of
the copy take longer than usual; the move now waits for it, and a write that
still times out is checked and tried again (never written twice) before the
move gives up and says why.

**An older `jarvis.db` moves too.** A file from before the newest message
columns and agent keys used to fail the check ("conversation_items: ... first
difference ... (changed)") although nothing differed: the check compared it
with today's columns. It now reads the records back in the file's own shape.

**Stopping during the move is safe.** Ctrl-C, closing the terminal or shutting
the Mac down part way loses nothing: the next start clears what was copied and
copies again from the beginning, even if `jarvis.db` changed in between.

### If the records move fails

The page says that it failed and why, and the server stays up saying so. What
to do depends on the step that failed:

- **Stopped part way, or a write that timed out** (the copy step, "timed out"
  or a lost connection in the reason): start JARVIS again; the next start
  clears what was copied and copies again from the beginning.
- **The check or the check back failed** (the reason says the records differ
  from jarvis.db, or names a table): starting again repeats the same check on
  the same file and fails the same way. Report it with the reason shown, and
  go back to the version you had before until a fix is in.

To go back to the version you had before:

1. Stop JARVIS.
2. Find the version you had: `git reflog` lists the update as
   `merge ...: Fast-forward`; the line below it is the commit you had.
3. Go back to it: `git reset --keep <that commit>`. If the update changed the
   dependencies, install them again: `.venv/bin/pip install -r requirements.txt`
   and `cd frontend && npm ci` (and `npm run build` if the page is served from
   built files).
4. Move the engine's folder aside, since the version before may not read it:
   `mv data/knowledge data/knowledge.moved-aside`.
5. Start JARVIS. It runs on `data/jarvis.db`, exactly as before the update.

After a move that succeeded, JARVIS writes its records to the engine only. To
take what it wrote since back to an earlier version, write the records out as a
SQLite file first, with JARVIS running:
`.venv/bin/python -m jarvis.knowledge export-sqlite --out data/jarvis-from-engine.db`,
then stop it, go back as above and put that file in place of `data/jarvis.db`
(keep the old one).

## Every message from an agent, on its Work item

An agent reaches you one way: a signed message about the Work item it belongs to, shown on that item's Messages tab, or in the Inbox (the tab that was Queue) when it is about no item. Every decision comes as a package with its background, never a bare question; Open shows it and you answer there. Needs boards and message files are gone. Ask JARVIS "what's new?" and it reads your new messages in the Inbox's order, each item's first; "read it", "next", "open it" (a decision package's story is told and waits for your answer) and "reply" act on the message you are on. A message read to you to its end (Read, "read it", or JARVIS reading an agent's answer to you) is marked read, and leaves the Inbox's new list for its Read section, closed under it; a message you are done with leaves the Inbox. Hide messages, beside "From agents", folds them away so people's messages stay in view; this browser remembers it.

## Usage history: each brain's limits over time

Clicking a brain's box in the left column opens that brain's weekly usage history in the centre, titled with its name ("Anthropic usage"); each brain has its own view. It shows the same weekly burn-down as the box, week after week: this week first, then Earlier week and Later week move through the weeks before it in order, and This week comes back. Each week is drawn across its whole span: what is left of the week, all of it at the top, against the dashed straight line from all left at the week's start to none at its reset, green while ahead of that line and red while behind it, with the reset marked at the week's end. The readings are joined as one line, across any stretch without one; nothing unmeasured is drawn as zero. Above the chart is the reading in the box's terms: what is left and how far ahead of or behind the line it is (for an earlier week, what it ended with). Hovering says the time and what was left. Only the week is kept: the five-hour window's history is not. Every weekly reading is kept for two weeks, then one summary per hour for good. JARVIS can read the same history for either brain when you ask (`usage_history`).

## Take over: a workflow or a package gets the page

A workflow, a subagent or a page package shown in the centre can take over the page: Take over beside its ×, Option+Shift+T, or asking JARVIS. The centre widens over the right panel, which folds to a rail of its six tab icons (each keeps its mark; an icon opens its tab over the takeover, closed by Escape or a click outside). The centre tabs give way to one bar: the item's name, what JARVIS is doing, the notices as chips, a chip for a message waiting for your yes (it opens the message as a sheet; saying yes works as always), the microphone with Stop and the health dot, and Restore layout. Restore layout, the same key (inside a package's page too), showing another centre view, opening another package or closing the item brings the normal layout back, and a reload always starts normal. A package whose package.json says `"layout": "wide"` is made for it and offers Take over; its page is told the room it has.

## Work: who is doing each piece

The tab that was Workflows is now Work. Each item of work in progress shows its title, its state and one useful update, with an open decision first. An item wraps a ZIP work package or a folder on this Mac; JARVIS only checks that it is there and never opens or runs it. Selecting an item opens it in the centre, with the list still in view: its source, its latest update, its decisions to answer, and its pieces, each naming exactly who is on it (an agent by its name, a run, a workflow, or you). Close goes back to Conversation. Agents report to it with `work_report.py` and an access key in the `work` category, and you can ask JARVIS what is in progress or answer a decision by voice. The Workflows list is still there, under the Work list.

## Nothing is sent until you say yes

A message JARVIS reads back to you, to an agent or to a person, now goes only
once you approve it: the first thing you say after hearing it read back in full
must be a plain yes ("yes", "go ahead", "do it"), or you press Send on its box.
A read-back you skipped or cut short, a yes said before the reading, or a yes
after you said something else does not count, and a change to the message
needs a new read-back. JARVIS enforces this itself; he cannot send in the same
breath as the read-back.

JARVIS now reads a message back as soon as he knows who it is for, without
asking first, and the read-back opens by naming them ("To chitauri."). Your yes
covers that recipient only: "send it to chitauri" approves it only when
chitauri is who the read-back named, sending it anywhere else is refused, and
changing who it is for needs a new read-back and a new yes.

## Settings in the centre

Settings opens in the centre as a tab beside Conversation, from the ⋮ menu (Check for updates opens it at Updates), and no longer covers the orb or the side columns. Its sections are in the order you come to them: You, Voice and language, Brains, Team, Updates and Server, with a row of their names under the title to jump to each. Each row is a short label and its controls; a state is shown only when it needs your attention, such as a brain check that is failing or a server that is not answering. With no Fish Audio key, first-time setup still opens by itself and walks through Voice and language, then You. JARVIS can point at any Settings control when you ask where something is.

## Folders

The centre's Folders view is a catalog of the folders JARVIS knows on this Mac. Add existing folder takes an absolute path or one starting with ~/, shows where it resolves and how JARVIS will read it, and registers it only when you say so; nothing is created, moved or changed on disk. Each folder keeps its ID through restarts and moves: if it disappears it stays listed as Missing, and Reconnect location points the same folder at its new place (a different directory has to be confirmed). Remove from catalog stops JARVIS reading it and deletes nothing.

Selecting a folder shows its location, whether it is there now, how it is read, its contents a page at a time with a preview of each file a window at a time, the Claude Code and Codex sessions whose records name it (kept after they stop) and its location history. Hidden, credential and key files are never shown; a session history folder is read as its transcripts and memory notes, shown as what was said. The Discovered list offers folders found in your sessions' own records, with Register and Ignore; seeing one grants nothing. The right panel's Folders tab is a compact list of the same folders; choosing one opens it in the centre. JARVIS can list, browse and read registered folders when you ask, and register or remove one only on your word. It tells a registered folder from a project, and looks in both before saying it can't find something you named. It can also start a run in a registered folder, to keep research there for instance: the run works in the folder's current location, and only in an ordinary folder that is there now, never in a session history or JARVIS's own notes. The run is recorded with the folder's ID. Builds still go to projects. Projects that use these folders come in a later release.

![Conversation and agents in the current interface, using fictional sample data.](images/workspace-conversation.png)

## Mute and mic off

Mute stops sending what the microphone hears, and a red "Muted" takes the level meter's place until it is pressed again. The microphone stays open while muted, so unmuting starts listening again without asking the browser for it; the browser's own recording indicator therefore stays on. The mic-off button beside it releases the microphone, which turns that indicator off, and shows "Mic off"; turning it on asks for the microphone again and takes longer than unmuting.

## Connect to your team

Settings has a Team section. Enter your team server's address and choose Sign in with GitHub: the team server's page opens, you sign in there and approve this Mac after checking the code JARVIS shows. JARVIS then shows the team, your role and when it last reached the server, checking every 30 seconds; it says so when the server can't be reached or your session has ended. The session is kept in your Keychain. Disconnect removes it and signs out on the server when it can be reached. Administrators manage members from the same section: the roster is shown as soon as the section opens, grouped as Signed in, Pending (not signed in yet) and Suspended. Adding someone says "<login> added; pending until they sign in" and lists them under Pending. When the team server refuses an add or any other member change, the page says beside it that the change did not happen and why, and a refused add keeps the person and role chosen so Add can be pressed again. Member changes need no fresh GitHub sign-in. If a code expires or you cancel, the address stays filled in, so starting again is one click. The left column's Team Server tile says Ready, Not ready, No contact or Sign in, and the Diagnostics tab has a Team server row saying the team, who you are and when JARVIS last reached it; a team server that can't be reached is shown as struggling, never down. Voice and agents carry on as before whether or not the team server is reachable. Messages between people have no switch: they work whenever this JARVIS is signed in to the team server, and while it is not, the section says that signing in turns them on. Running a team server is described in [the operator runbook](team-server.md).

## See your team's network

Once paired, JARVIS reports to the team server every five seconds, browser open or not: this Mac's CPU and memory, its brain accounts' usage and when a person last used it. The team server answers with every paired JARVIS on the team, and that answer is all the Network tab shows. Each JARVIS has one compact tile: its name, online, delayed or offline, CPU and memory, the brains' remaining allowance and pace against the week, and how long ago someone last used it (a click, a key, typing or accepted speech, never what was said or typed). Your own is outlined and marked "THIS MAC", and its figures too come from the team server, so they match what everyone else sees. Two JARVIS on two of your Macs show as two tiles. Selecting a tile opens that JARVIS in the centre.

The Team Server tile in the left column opens the server's health in the centre: its database round trip and writes, uptime, CPU and memory. When the team server can't be reached the tiles keep their last values and say they are stale, and the Team Server tile says No contact as soon as the five-second exchange fails; no JARVIS is called offline because your own link dropped.

## A reminder to sign in

JARVIS works as before when it is not signed in to a team server, and reminds you to sign in. Whenever the Team Server tile says Sign in (never paired, signed out, or part way through signing in), a line at the top of the centre area says "Sign in to your team server: messages between people and the Network view need it". It moves the view below it down rather than covering it. Sign in starts signing in again with the address you used before, opening the team server's approval page and Settings at Team where the code to check is shown; with no address saved it opens Settings at Team. Later hides the line until the page is reloaded or JARVIS restarts. Once you are signed in it goes by itself. Once each time JARVIS starts, if it is still not signed in about 30 seconds later, it also says so once, as it says any other notice: "You're not signed in to the team server, sir; messages between people are off until you are."

## Ask JARVIS about anything on the page

What the page shows or does, JARVIS can now do by voice. He can say who is on your team and who is signed in, pending or suspended, which JARVIS on the team network are online and how busy they are, and who you can message right now; ask him to write to someone and he drafts it to a name from that list. He reads what people have sent you and what you sent them, a conversation at a time, marks a message read once he has read it to you, opens, puts back, dismisses or hands the turn on a conversation in the Queue, and discards, cancels or retries one of your unsent messages, when you ask. For an administrator he looks up a GitHub account and adds, promotes, suspends, re-enables or removes a member, or ends their sessions, only on your word. He reports how each part of JARVIS is doing from the Diagnostics tab, voice input with today's figures; answers or dismisses a workflow's decision when you give the answer; runs a finished run again; and shows a folder in full, renames it, changes its role, reconnects it or checks it now, and lists, ignores or restores the folders discovery found. Signing in to the team server, API keys and restarting JARVIS on its own stay on the page.

## Update from GitHub

A JARVIS running from a checkout of the GitHub repository can update itself, to the version you choose. Settings has an Updates section, which the ⋮ menu's Check for updates opens. It says which version is running ("Running PR 39: Find the running server from the workflow reporter" and its commit) and lists every newer version on main, newest first: each is a merged pull request, with its number, title, date, commit and whether its tests passed, and the newest is tagged LATEST. Only a version whose tests passed can be chosen; the others are listed greyed, saying they can be chosen once their tests pass or can't be chosen because they failed. The newest one that passed starts chosen. Install and restart asks once more, saying how many pull requests the choice brings in, that the page reloads by itself, that a failed step moves JARVIS back and changes nothing, and that going back to an older version is not offered; "Yes, install PR 41" goes ahead. A refusal is said in red under the list. JARVIS installs the Python requirements or the page's packages when they changed, rebuilds the page when it is served from built files, and restarts; the page reloads itself as it does after Restart Server. If the page's packages changed while you run `npm run dev`, it says to restart that. It refuses, and says why, when the checkout is not on main, has changes or commits of its own, or has untracked files the update would overwrite. Ask JARVIS "is there an update?" and he lists the versions; tell him to update himself, to the latest or to "pull request 41": he says he is updating, and is back on the new code a few seconds later. From a terminal, `.venv/bin/python -m jarvis.update` does the same, `--to 41` installs pull request 41's version, and `--check` only lists the versions; with JARVIS stopped it only updates. It finds the running JARVIS on whatever address and port it was started with, and if that JARVIS is running but cannot be reached, it stops without updating. An installed copy is not offered the update and says so.

## Work packages

A teammate can send you work to run as a .zip in an ordinary message. When a message has one, JARVIS offers to open it. On your yes, he downloads it, unpacks it into its own folder and follows its instructions with you, running each step only when you say to. A step that needs you, such as recording your voice, opens in a Terminal window. At the end he drafts a reply with the results attached and asks you to review it, as for any other message. You can also ask him to attach a file you name to a message. The format and an example, a Spanish speech test, are in [work packages](work-packages.md).

A package can also run two other ways. An agent package hands its brief to a coding agent that works in the package's folder on its own: on your yes JARVIS starts it, tells you when it has finished (or failed), and asks whether to draft the reply with its results. A page package is its own small web page: on your yes JARVIS opens it in the centre. You can also open any package yourself with Open package on its file in the Queue; a page package then opens in the centre at once, and its file has Open page from then on. A package zip that is a file on your Mac opens the same way: ask JARVIS to open the package at its path, or choose the .zip in a registered folder in the Folders view and press Open package. An open package page stays open when the page reloads, until you close it. The page runs in a sealed frame that cannot reach the rest of JARVIS; it can save its results into the package's folder and, when it asks, hear JARVIS's microphone, including while JARVIS is muted. Examples of both are in [work packages](work-packages.md).

## Speak Spanish to JARVIS

Settings, Voice now has a Language control: English or Español. Español can be chosen once the Spanish speech model is installed; until then the row below it offers "Download (124 MB)" and shows how the download is going, and the download is checked against the SHA-256 the sherpa-onnx release publishes before anything is unpacked. Changing the language reloads the speech model, which takes a few seconds, and JARVIS then says so in the new language: "Ahora hablo en español, señor." The choice is kept by JARVIS itself, not in your .env, and holds after a restart. By voice, say exactly "switch to Spanish" while he is in English, or "cambia a inglés" while he is in Spanish; only the whole phrase counts, and Settings always works, so a misheard phrase cannot leave you stuck. You can also ask JARVIS to change the language or to download the model. The Honorific setting in Settings, You is now used, and has Señor as a choice: Sir means "sir" in English and "señor" in Spanish, and None means no form of address. English stays the default and works as before.

JARVIS reads accented words whole. Before, "sí" was heard as the letter "s" and "espérate" as two fragments, so no Spanish yes, no or cancel could ever count. In Spanish: sí, claro, adelante, hazlo, dale or "de acuerdo" to say yes; no, espera, luego, después, nunca, negativo or todavía to say no; espera, espérate, cancela, alto or detente to stop him; repite or otra vez to hear the last answer again; and exactly "empezar de cero" to clear the conversation, still confirmed with a yes. Agent names and memories with accents are found with or without them.

In Spanish, Fish Audio is told the text is Latin American Spanish, so it reads numbers, dates and units the Spanish way in the same JARVIS voice. Spanish abbreviations such as Sra., Dra., Ud. and pág., and times written "3 p. m.", no longer split a sentence into separately spoken pieces. "Por supuesto", "Claro que sí" and the other Spanish counterparts of JARVIS's banned phrases are never spoken. The brain answers in Spanish, calls you "señor", says English tool results in Spanish and searches its memory in both languages before saying it doesn't have something. Messages you send agents stay in the words and language you used.

## Agents and workflows stay in context

The agent interface covers Claude Code and Codex sessions. Threads bring messages and replies together instead of requiring the user to reconstruct an exchange from separate announcements.

A workflow opens into its phases, tasks, progress, attached agent transcripts, waits, blockers, decisions and event history. Decisions can be answered in the workspace. The monitor reads reports from the sessions doing the work; it does not independently establish that every reported task is correct.

![A workflow opened in the centre, using fictional sample data.](images/workspace-workflow.png)

## Subagents appear with the workflows

The subagents a Claude Code session dispatches are read from the session's own files: what each was asked to do, its type, and whether its transcript shows it finished, still being written, or gone quiet. A subagent of a session attached to a running workflow is listed in that workflow's Overview, and its transcript follows the attached agents' in the Transcript tab. Any other subagent has its own row in the Workflows list, in the same style, naming the session that dispatched it; a finished one stays for a couple of minutes, as a finished workflow does. Opening the row shows its transcript in the centre. An agent with working subagents shows the count on its box in the Agents tab. These rows are worked out when read and are not added to the workflow record. Codex sessions keep no subagent files, so none are shown for them.

![A subagent's transcript opened from its row in the Workflows list, using fictional sample data.](images/workspace-subagent.png)

## Voice health has its own explanation

The Diagnostics tab summarizes whether speech is being heard, whether recognition is falling behind and how much headroom remains. Opening it reveals the pipeline, timeline, incidents, system load, notifications and grouped UI messages. The quiet-page comparison helps investigate whether interface activity accompanies drops. The displayed interval can be saved as JSON.

Above the voice summary, the Diagnostics tab lists each major subsystem with its state: communications with Claude Code and Codex sessions, voice input, the brain, voice output, the tool channel, the workflow monitor and the page's own connection. Communications count as down when the session watcher stops or messages fail to reach two or more different sessions in a row. A down subsystem is shown in red, the Diagnostics tab turns red whichever tab is open, and JARVIS says when communications go down and when they recover. Selecting a subsystem opens its checks in the centre.

Speech input currently uses local Kroko streaming recognition through sherpa-onnx. Fish Audio remains the speaking voice. Local recognition does not make the conversational provider or speech output offline.

![Voice diagnostics in the current interface, using fictional sample data.](images/workspace-diagnostics.png)

These captures render the real frontend with sample records; they are not production measurements.

## Two speech models, one at a time

Speech input knows two models, English and Spanish, and holds one in memory at a time. Besides Settings, the Spanish model (124 MB) can be installed with `.venv/bin/python scripts/setup_speech_input.py --language es`. When the language changes, whatever was being said at that moment is not heard, the page says speech recognition is switching and listens again once the model has loaded, and the voice record notes the switch and how long it took. `JARVIS_STT_MODEL_DIR`, if you set it, now names the folder that holds one folder per model.

## More than one conversational provider

Claude and Codex brain providers share the application interface. Settings expose provider configuration, and brain account usage appears in the left column. Development runs retain a separate execution path; conversational provider selection does not switch every execution engine.

## Packaged application layout

Python modules now live under `jarvis/`. Start the backend with `python -m jarvis`; the frontend build is packaged under `jarvis/web`. Configuration and data stay outside installed package files. The [README](../README.md#run-the-workspace) contains the current checkout setup commands.

## What follows

The priorities are a complete conversation-centred workspace, reliable voice under concurrent load, understandable agent coordination and the path from the Prototype Engine to Denice. Speech-output alternatives and eye tracking remain evaluations. See the [roadmap](roadmap.md) for status distinctions.

## When explaining this by voice

Answer the user's question from the current interface and relevant roadmap item. Keep shipped functionality separate from future work. Do not describe a planned engine or evaluated speech model as already installed.
