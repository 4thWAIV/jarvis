# What the JARVIS server says or shows on its own

Every line of speech the JARVIS server speaks, or sends to the voice page, itself: words the server writes, not words the brain writes. Taken from server.py, preflight.py and speech.py on 2026-10-21. Nothing in the code was changed to make this list.

`<name>` is a session's name (its official name if one has been set), `<project>` a run's project, `<key>` a key such as Return. File and line point at the `speech.say` (or the message sent to the page) that says it; where the words are kept in a constant, that line is given too.

"Brain told" means the brain gets a note about it with its next turn. Today only two lines do that. For every other line the brain does not know it was said, so it cannot answer Tim's follow-up about it.

## The four options

- **Remove**: say nothing at all.
- **Replace with a visual indicator**: show it on the voice page instead of speaking it.
- **Let the brain decide**: tell the brain what happened and let it choose whether and how to say it.
- **Keep spoken, notify the brain**: the server still says it, and the brain is told it was said and the exact words, so it can answer a follow-up question about it.

Each line below has one recommended option.

## Counts

46 lines in all.

| Option | Lines |
|---|---|
| Remove | 1 |
| Replace with a visual indicator | 4 |
| Let the brain decide | 1 |
| Keep spoken, notify the brain | 40 |

| Trigger | Lines |
|---|---|
| Sessions | 6 |
| Runs JARVIS started | 5 |
| Read-backs after a tool | 16 |
| Clearing and compacting | 7 |
| The brain's own health | 8 |
| Start-up and other | 4 |
| Brain told today | 2 |

## By option

### Remove (1)

- Gathering my thoughts — one moment, sir.  (shown on the page, not spoken) (server.py:1341 (words at 678))

### Replace with a visual indicator (4)

- Passed to <name>, sir. (+ It'll ask you to approve it first — say the word and I'll set your sessions to accept them.) (server.py:4111)
- Rebooting my language systems, one moment. (server.py:730)
- One thing needs attention, sir: <phrase>. / Two things need attention, sir: <a>, and <b>. / <n> things need attention, sir: … (server.py:1936 (words in preflight.py:705))
- My voice is failing, sir.  (shown on the page, not spoken) (speech.py:1160)

### Let the brain decide (1)

- Good morning, sir. / Good afternoon, sir. / Good evening, sir. (server.py:7421 (words at 740))

### Keep spoken, notify the brain (40)

- <name> has answered, sir — shall I read it? (server.py:1519)
- <name> has <n> thing(s) for you, sir. (server.py:1543)
- <name> is waiting on a permission prompt / a dialog / input / <reason>, sir. (server.py:1592)
- <name> is waiting on <reason>, sir — that one needs your own keystroke. (server.py:1592)
- <name> has stopped and wants you, sir. (server.py:1592)
- <name> has finished, sir. / <N> conversations have finished, sir: a, b and c. (server.py:1681)
- The work in <project> is done, sir. / Work in a, b and c is done, sir. (server.py:1681)
- The work in <project> stopped to ask a question, sir, so nothing was built. (server.py:1760)
- The work in <project> finished, sir, but I can't see that it changed anything. (server.py:1760)
- The work in <project> ran out of time, sir. (server.py:1775)
- The work in <project> failed, sir. (server.py:1775)
- Telling <name>: <message> (server.py:4055)
- Cancelled, sir. (server.py:4070, 4079)
- <name> didn't answer its socket, sir — it may have just exited. (server.py:4114)
- I couldn't deliver that to <name>, sir. (server.py:4118)
- Running <command> in <project>, sir. (+ That isn't a command the project documents, mind.) (server.py:4169)
- Cancelled, sir. (server.py:4178, 4185)
- Running in a Terminal window, sir. (server.py:4196)
- Terminal wouldn't open, sir. (server.py:4200)
- Pressing <key> on <name> — this will bring that window forward. (server.py:4278)
- Cancelled, sir. (server.py:4287, 4294)
- Pressed <key> on <name>. (server.py:4300)
- <name> isn't in a Terminal window I can reach, sir — another application is hosting it, so that one needs your own hand. (server.py:4303)
- macOS won't let me send keystrokes, sir — I'd need accessibility permission in System Settings. (server.py:4308)
- <name> has no terminal of its own any more, sir — I pressed nothing. (server.py:4312)
- I couldn't press that for <name>, sir. (server.py:4316)
- Clear the whole conversation, sir? Say yes to confirm. (server.py:1151 (words at 695))
- Very good, sir — nothing was cleared. (server.py:1139, 1170 (words at 696))
- Cleared, sir — nothing of that conversation left. Go ahead. (server.py:1201 (words at 691))
- I couldn't clear it, sir. (server.py:1196)
- My context is past <budget> thousand tokens, sir. Shall I compact it? (server.py:1278 (words at 1234))
- Very good, sir. I'll carry on as I am. (server.py:1304 (words at 1211))
- My language systems are down, sir. Check the server log. (server.py:734)
- Claude Code's login has expired, sir — run `claude` in a terminal and log in, then restart me. (server.py:734, 2056 (words at 661))
- I've hit the usage limit until <time>, sir. (server.py:736, 2076)
- One moment, sir — my language systems are still starting. (server.py:2056)
- My language systems are down, sir. (server.py:2056)
- I lost my train of thought, sir. Say that again? (server.py:2072, 2082)
- My language systems returned an error, sir. Check the server log. (server.py:2079)
- I'm afraid I've nothing to repeat yet, sir. (server.py:7483 (words at 669))

## By trigger

### Sessions (6)

| File and line | Exact words | Trigger | Brain told | Option |
|---|---|---|---|---|
| server.py:1519 | <name> has answered, sir — shall I read it? | A session JARVIS sent a message to has replied. | Yes | Keep spoken, notify the brain |
| server.py:1543 | <name> has <n> thing(s) for you, sir. | A project's needs board in data/needs/ changed and lists at least one item. | Yes | Keep spoken, notify the brain |
| server.py:1592 | <name> is waiting on a permission prompt / a dialog / input / <reason>, sir. | A session stopped on a prompt. Spoken as urgent. | No | Keep spoken, notify the brain |
| server.py:1592 | <name> is waiting on <reason>, sir — that one needs your own keystroke. | Same, when only Tim can answer it. | No | Keep spoken, notify the brain |
| server.py:1592 | <name> has stopped and wants you, sir. | A session stopped and wants Tim, with no reason given. | No | Keep spoken, notify the brain |
| server.py:1681 | <name> has finished, sir. / <N> conversations have finished, sir: a, b and c. | Sessions finished; said together at the next pause. | No | Keep spoken, notify the brain |

### Runs JARVIS started (5)

| File and line | Exact words | Trigger | Brain told | Option |
|---|---|---|---|---|
| server.py:1681 | The work in <project> is done, sir. / Work in a, b and c is done, sir. | A run JARVIS started succeeded; said with the finished sessions. | No | Keep spoken, notify the brain |
| server.py:1760 | The work in <project> stopped to ask a question, sir, so nothing was built. | A run ended without error but stopped on a question. Urgent. | No | Keep spoken, notify the brain |
| server.py:1760 | The work in <project> finished, sir, but I can't see that it changed anything. | A run ended without error but changed nothing. Urgent. | No | Keep spoken, notify the brain |
| server.py:1775 | The work in <project> ran out of time, sir. | A run timed out. Urgent. | No | Keep spoken, notify the brain |
| server.py:1775 | The work in <project> failed, sir. | A run failed. Urgent. | No | Keep spoken, notify the brain |

### Read-backs after a tool (16)

| File and line | Exact words | Trigger | Brain told | Option |
|---|---|---|---|---|
| server.py:4055 | Telling <name>: <message> | A message to a session, read back before it is sent. | No (it knows it asked, not that it was read back) | Keep spoken, notify the brain |
| server.py:4070, 4079 | Cancelled, sir. | Tim cancelled a message during its read-back or the pause after it. | No | Keep spoken, notify the brain |
| server.py:4111 | Passed to <name>, sir. (+ It'll ask you to approve it first — say the word and I'll set your sessions to accept them.) | The message was sent. The second sentence only when the session will ask Tim to approve it. | No | Replace with a visual indicator |
| server.py:4114 | <name> didn't answer its socket, sir — it may have just exited. | The session did not answer when the message was sent. | No | Keep spoken, notify the brain |
| server.py:4118 | I couldn't deliver that to <name>, sir. | Sending the message failed. | No | Keep spoken, notify the brain |
| server.py:4169 | Running <command> in <project>, sir. (+ That isn't a command the project documents, mind.) | A command, read back before it runs. The second sentence when the project does not document it. | No (it knows it asked, not that it was read back) | Keep spoken, notify the brain |
| server.py:4178, 4185 | Cancelled, sir. | Tim cancelled a command during its read-back or the pause after it. | No | Keep spoken, notify the brain |
| server.py:4196 | Running in a Terminal window, sir. | The command started. | No | Keep spoken, notify the brain |
| server.py:4200 | Terminal wouldn't open, sir. | Terminal failed to open. | No | Keep spoken, notify the brain |
| server.py:4278 | Pressing <key> on <name> — this will bring that window forward. | A key press on a session's prompt, read back before it is sent. | No (it knows it asked, not that it was read back) | Keep spoken, notify the brain |
| server.py:4287, 4294 | Cancelled, sir. | Tim cancelled a key press during its read-back or the pause after it. | No | Keep spoken, notify the brain |
| server.py:4300 | Pressed <key> on <name>. | The key press was sent. | No | Keep spoken, notify the brain |
| server.py:4303 | <name> isn't in a Terminal window I can reach, sir — another application is hosting it, so that one needs your own hand. | The session's window could not be found. | No | Keep spoken, notify the brain |
| server.py:4308 | macOS won't let me send keystrokes, sir — I'd need accessibility permission in System Settings. | macOS refused the key press (no Accessibility permission). | No | Keep spoken, notify the brain |
| server.py:4312 | <name> has no terminal of its own any more, sir — I pressed nothing. | The session no longer has a terminal. | No | Keep spoken, notify the brain |
| server.py:4316 | I couldn't press that for <name>, sir. | The key press failed for another reason. | No | Keep spoken, notify the brain |

### Clearing and compacting (7)

| File and line | Exact words | Trigger | Brain told | Option |
|---|---|---|---|---|
| server.py:1151 (words at 695) | Clear the whole conversation, sir? Say yes to confirm. | Tim said exactly "start fresh". | No | Keep spoken, notify the brain |
| server.py:1139, 1170 (words at 696) | Very good, sir — nothing was cleared. | Tim did not say yes, or 20 seconds passed. | No | Keep spoken, notify the brain |
| server.py:1201 (words at 691) | Cleared, sir — nothing of that conversation left. Go ahead. | The conversation was cleared. | No (a new, empty brain) | Keep spoken, notify the brain |
| server.py:1196 | I couldn't clear it, sir. | Clearing failed. | No | Keep spoken, notify the brain |
| server.py:1278 (words at 1234) | My context is past <budget> thousand tokens, sir. Shall I compact it? | The brain's context passed its budget; asked at a pause, again after ten turns if declined. | No | Keep spoken, notify the brain |
| server.py:1304 (words at 1211) | Very good, sir. I'll carry on as I am. | Tim declined compacting. | No | Keep spoken, notify the brain |
| server.py:1341 (words at 678) | Gathering my thoughts — one moment, sir.  (shown on the page, not spoken) | Compacting is under way. | No | Remove |

### The brain's own health (8)

| File and line | Exact words | Trigger | Brain told | Option |
|---|---|---|---|---|
| server.py:730 | Rebooting my language systems, one moment. | The brain process is restarting (at most once a minute). | No | Replace with a visual indicator |
| server.py:734 | My language systems are down, sir. Check the server log. | The brain failed. Urgent. | No (the brain is down) | Keep spoken, notify the brain |
| server.py:734, 2056 (words at 661) | Claude Code's login has expired, sir — run `claude` in a terminal and log in, then restart me. | The brain failed on an expired login, or Tim spoke while it was down for that reason. | No (the brain is down) | Keep spoken, notify the brain |
| server.py:736, 2076 | I've hit the usage limit until <time>, sir. | The subscription's usage limit was reached. | No | Keep spoken, notify the brain |
| server.py:2056 | One moment, sir — my language systems are still starting. | Tim spoke before the brain was ready; his words are dropped. | No | Keep spoken, notify the brain |
| server.py:2056 | My language systems are down, sir. | Tim spoke while the brain was down. | No (the brain is down) | Keep spoken, notify the brain |
| server.py:2072, 2082 | I lost my train of thought, sir. Say that again? | A turn raised an error, timed out, or the brain died mid-turn. | No | Keep spoken, notify the brain |
| server.py:2079 | My language systems returned an error, sir. Check the server log. | A turn ended in an error. | No | Keep spoken, notify the brain |

### Start-up and other (4)

| File and line | Exact words | Trigger | Brain told | Option |
|---|---|---|---|---|
| server.py:7421 (words at 740) | Good morning, sir. / Good afternoon, sir. / Good evening, sir. | A voice page connects (at most once a minute). | No | Let the brain decide |
| server.py:1936 (words in preflight.py:705) | One thing needs attention, sir: <phrase>. / Two things need attention, sir: <a>, and <b>. / <n> things need attention, sir: … | Start-up checks found a failure (Claude Code missing, outdated or logged out; no Accessibility or Screen Recording permission; no Fish Audio key; a leftover Anthropic key; cross-session messages not switched on). | No | Replace with a visual indicator |
| server.py:7483 (words at 669) | I'm afraid I've nothing to repeat yet, sir. | Tim asked to hear the last thing again and nothing has been said yet. | No | Keep spoken, notify the brain |
| speech.py:1160 | My voice is failing, sir.  (shown on the page, not spoken) | Text-to-speech failed for a sentence; the sentence itself is shown instead. | No | Replace with a visual indicator |

## Why these options

- **Keep spoken, notify the brain** is the default: these are things Tim needs to hear when they happen (a session waiting on him, a run that failed, a read-back he may cancel), and the only thing wrong with them today is that the brain cannot answer "which one?" or "what did you just say?" afterwards. The two lines that already notify the brain (`_announce_reply`, `_announce_board`) show how: a note added with the next turn.
- **Replace with a visual indicator** where the page already shows it or easily could: "Passed to <name>" is what the ray from the orb to the agent already shows; the brain restarting is the orb's state; the start-up checks belong in the Diagnostics panel; "My voice is failing" is already shown, not spoken, and would become an icon (Tim wants no text on the page).
- **Remove** "Gathering my thoughts": it is shown, not spoken, and the orb's compacting state already says the same.
- **Let the brain decide** the greeting: whether to greet, and how, is a matter of manner, which is the brain's.

## Not on the voice page

When no voice page is connected, a session waiting on Tim is also sent as a macOS notification with the same words (server.py `_notify_needs_you`). The page's own words ("listening...", "restarting...", microphone errors) are written by the page, not the server, and are not in this list.
