# JARVIS on Windows: run it from source

A source checkout of JARVIS on Windows 11 x64, run in the background: the
Python backend and the Vite dev server, opened in Chrome. This is the
contributor setup. macOS stays the platform the whole suite runs on; on
Windows, what is checked natively is listed under [What is checked](#what-is-checked)
and what is not under [Known gaps](#known-gaps).

Nothing here needs a `.env` file. A clean clone starts with its defaults;
everything you choose afterwards (the brain and its model, the voice, the
Fish key, the speech models, the team server) is in Settings.

## What you need

- Windows 11, x64, signed in as an ordinary user.
- [Git for Windows](https://git-scm.com/download/win). It also brings the
  `openssl` the certificate step uses.
- Python 3.12 (the `py` launcher, or python.org's installer).
- Node.js 22 with npm.
- Google Chrome. The voice page is tested in Chrome.
- Claude Code 2.1.224 or newer, signed in with your Claude subscription.
  Install it with its native installer, winget or npm; JARVIS finds
  `claude.exe` on PATH, in `~\.local\bin`, in winget's `Links` folder, or
  behind npm's `claude.cmd`. JARVIS never uses an API key.

Clone under your user profile (for example `C:\Users\<you>\src`), not
directly under `C:\`: JARVIS keeps its token and records in the checkout's
`data` folder and refuses a folder other users can change.

In PowerShell, use `npm.cmd` rather than `npm`: the execution policy can
block the `npm.ps1` script plain `npm` runs.

## Set up once

```powershell
git clone https://github.com/4thWAIV/jarvis.git
Set-Location jarvis
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd --prefix frontend ci
.\.venv\Scripts\python.exe -m jarvis.background certs
```

`certs` makes `cert.pem` and `key.pem` for `localhost` in the checkout with
Git for Windows' `openssl`. The Vite dev server proxies `/api` and `/ws` to
`https://localhost:8340`, so the backend must have them: without them it
serves plain HTTP and every proxied request fails while the page still
loads.

Optional, for features that need them (each can also be installed later
from Settings):

```powershell
.\.venv\Scripts\python.exe scripts\setup_speech_input.py      # English speech model (Settings, Voice and language: Download)
.\.venv\Scripts\python.exe scripts\setup_embedding_model.py   # vector search
.\.venv\Scripts\python.exe -m playwright install chromium     # read_page / look_at_page
```

## Start, check, stop

```powershell
.\.venv\Scripts\python.exe -m jarvis.background start
.\.venv\Scripts\python.exe -m jarvis.background status
.\.venv\Scripts\python.exe -m jarvis.background logs backend -n 100
.\.venv\Scripts\python.exe -m jarvis.background logs vite
.\.venv\Scripts\python.exe -m jarvis.background restart
.\.venv\Scripts\python.exe -m jarvis.background stop
```

- `start` launches the backend (`python -m jarvis --host 127.0.0.1`) and
  Vite, each in its own hidden console, so closing the PowerShell window
  that ran it leaves both running. It waits until both answer, then prints
  where they are. It refuses while either already runs, and while port 8340
  or 5173 is held by another program, naming the port: the page must be
  served from `http://localhost:5173`, because Chrome keeps the microphone
  permission per origin, port included.
- `status` names each process, its pid and its log. The backend is the
  process the server recorded for itself at startup, so one that restarted
  itself (the page's Restart Server) is still found.
- `stop` ends the two processes it can confirm are JARVIS's (by their
  recorded creation time, never a pid alone) with their own children, and
  nothing else.
- The logs are `data\logs\backend.log` and `data\logs\vite.log`; the
  knowledge engine, which holds JARVIS's records, logs to
  `data\knowledge-engine.log`.

If `start` says they did not come up, read `logs backend` and
`data\knowledge-engine.log`. "not a safe place for a secret" there means
the checkout sits in a folder other accounts can change (a drive's root, a
shared folder): clone it again under your user profile.

To run in the foreground instead, two windows:

```powershell
.\.venv\Scripts\python.exe -m jarvis --host 127.0.0.1
```

```powershell
npm.cmd --prefix frontend run dev
```

## First session

Open **http://localhost:5173** in Chrome and click the page to enable audio.

1. **Settings, Brains.** Claude is the default brain. If Claude Code is not
   signed in, the page says so; run `claude` in a terminal, sign in, and the
   brain picks it up. Then type a message in the Conversation and check a
   reply comes back. This is the first live check: CI has no credentials.
2. **Settings, Voice and language.** Download the English speech model if
   you skipped it, and choose the voice output: Fish Audio (its key goes in
   Settings) or local Kokoro (install it there). Without working synthesis,
   replies stay visible as text.
3. **Settings, Team.** Sign in to JarvisNet (the team server) with GitHub
   and approve this machine. The Network tab then shows this node.
4. **Your first package.** Tim's package arrives in the Inbox tab, or on
   its Work item. Open it, read it, and answer it there.
5. **A coding agent with JARVIS.** Install the coordination instructions,
   then start Claude Code in the folder of your assignment:

   ```powershell
   .\.venv\Scripts\python.exe -m jarvis.session_skill install
   ```

   The session makes its own key with `python -m jarvis.agent_key init`
   and sends it with the `submit` command that prints; switch the agent on
   in the Agents tab to onboard it. It then reports on its Work item and
   sends its result back as a message or package.

## Working on JARVIS

Branch from the commit you were given, run the focused tests for what you
change (`.\.venv\Scripts\python.exe -m pytest tests\test_<area>.py`; the
full suite runs on CI), and open a pull request. The Windows startup
workflow (`.github/workflows/windows-startup.yml`) runs this page's commands
on GitHub's Windows runner for every pull request.

## What is checked

On GitHub's hosted `windows-latest` runner, from a clean clone with no
`.env`, for every pull request: the setup above, `certs`, `start`, and from
later shells (so the processes have outlived the one that started them) the
backend over HTTPS, the page from Vite, `/api` and a WebSocket through
Vite's proxy, the brain path with Claude Code installed but not signed in,
a refused second start, the backend's own restart, `stop` leaving an
unrelated process alive, both ports free after it, an occupied port
reported, and start and stop again. Beside it, the native tests of what
that leans on: process identity, the background command, the record
engine, run cancellation, packages, agent keys (with Windows' own
`ssh-keygen`) and received files.

## Known gaps

Small gaps that do not stop the source workflow, with what they mean for you:

- **Checked with you, not by CI**: a live Claude turn, JarvisNet sign-in,
  receiving and answering a package, and a real microphone and speakers.
  CI holds no credentials and no audio devices.
- **Cancelling a run** ends its process tree through a Windows Job Object
  (checked natively). One case is not yet covered: when the run's own
  process has already exited, a process it left behind can outlive the
  cancel. If one lingers, end it from Task Manager.
- **A conversation package's shell steps** run under `/bin/bash`, so they
  do not run on Windows yet; page and agent packages, the kinds sent for
  decisions and work, do.
- **Pausing the record engine's background work** (ingest, compaction)
  while you talk has no effect on Windows; it only costs some CPU then.

Windows is a source checkout, as on macOS: there is no installer, wheel,
service or updater for it.
