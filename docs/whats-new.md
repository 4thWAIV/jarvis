# The new JARVIS workspace

Updated 2026-10-26. This guide describes functionality present in the checkout. The [roadmap](roadmap.md) describes the development direction and successor engines.

## Conversation is the main view

The centre opens on Conversation. It combines earlier conversation with live speech, typed input, captures and expandable sources. Agent messages have delivery and reply states, so their progress can be followed in the conversation. Selecting an agent's name opens that agent's thread.

The left column holds the orb, voice status, microphone controls and brain account usage. The right panel has Agents, Diagnostics and Workflows tabs. Detailed views open in the centre while the surrounding controls remain available.

![Conversation and agents in the current interface, using fictional sample data.](images/workspace-conversation.png)

## Agents and workflows stay in context

The agent interface covers Claude Code and Codex sessions. Threads bring messages and replies together instead of requiring the user to reconstruct an exchange from separate announcements.

A workflow opens into its phases, tasks, progress, attached agent transcripts, waits, blockers, decisions and event history. Decisions can be answered in the workspace. The monitor reads reports from the sessions doing the work; it does not independently establish that every reported task is correct.

![A workflow opened in the centre, using fictional sample data.](images/workspace-workflow.png)

## Voice health has its own explanation

The Diagnostics tab summarizes whether speech is being heard, whether recognition is falling behind and how much headroom remains. Opening it reveals the pipeline, timeline, incidents, system load, notifications and grouped UI messages. The quiet-page comparison helps investigate whether interface activity accompanies drops. The displayed interval can be saved as JSON.

Above the voice summary, the Diagnostics tab lists each major subsystem with its state: communications with Claude Code and Codex sessions, voice input, the brain, voice output, the tool channel, the workflow monitor and the page's own connection. Communications count as down when the session watcher stops, messages fail to reach two or more different sessions in a row, or the loops that collect session messages and needs boards stop. A down subsystem is shown in red, the Diagnostics tab turns red whichever tab is open, and JARVIS says when communications go down and when they recover. Selecting a subsystem opens its checks in the centre.

Speech input currently uses local Kroko streaming recognition through sherpa-onnx. Fish Audio remains the speaking voice. Local recognition does not make the conversational provider or speech output offline.

![Voice diagnostics in the current interface, using fictional sample data.](images/workspace-diagnostics.png)

These captures render the real frontend with sample records; they are not production measurements.

## More than one conversational provider

Claude and Codex brain providers share the application interface. Settings expose provider configuration, and brain account usage appears in the left column. Development runs retain a separate execution path; conversational provider selection does not switch every execution engine.

## Packaged application layout

Python modules now live under `jarvis/`. Start the backend with `python -m jarvis`; the frontend build is packaged under `jarvis/web`. Configuration and data stay outside installed package files. The [README](../README.md#run-the-workspace) contains the current checkout setup commands.

## What follows

The priorities are a complete conversation-centred workspace, reliable voice under concurrent load, understandable agent coordination and the path from the Prototype Engine to Denice. Speech-output alternatives and eye tracking remain evaluations. See the [roadmap](roadmap.md) for status distinctions.

## When explaining this by voice

Answer the user's question from the current interface and relevant roadmap item. Keep shipped functionality separate from future work. Do not describe a planned engine or evaluated speech model as already installed.
