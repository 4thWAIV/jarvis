# Windows preview

A preview of the native Windows port on branch `feature/windows-port`. This is not a support claim: macOS remains the supported platform (see the [README](../README.md)).

## Requirements

Versions are those observed on the test machine.

- Windows 11, x64.
- Git for Windows (tested 2.56).
- Python 3.12 through the `py` launcher (tested 3.12.10). `pyproject.toml` requires Python 3.11 or newer, but the port was qualified on 3.12 only.
- Node.js 22 with npm (tested 22.23 and npm 10.9), to build the page.
- Claude Code installed and signed in with your own Claude subscription: run `claude`, then sign in. JARVIS needs 2.1.224 or newer (tested 2.1.302, installed with winget, which puts `claude.exe` in `$env:LOCALAPPDATA\Microsoft\WinGet\Links`). JARVIS does not use an API key; it scrubs `ANTHROPIC_*` from the Claude processes it starts.
- A `.cmd` or `.bat` launcher for `claude` is refused: point JARVIS at `claude.exe` itself.

## Build from the branch

The branch exists only on the Windows test machine: get it as a git bundle, or use a checkout supplied with it. Then, in PowerShell:

```powershell
git clone --branch feature/windows-port C:\path\to\jarvis-windows-port.bundle jarvis-windows
Set-Location jarvis-windows
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build        # writes the page to jarvis\web
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w dist
```

Use `npm.cmd` in PowerShell: the execution policy can block the `npm.ps1` script that plain `npm` runs.

The result is `dist\jarvis-0.1.0-py3-none-any.whl` (the tested build had 157 files, including `jarvis\web`). Build the page before the wheel, or the installed app has no page.

## Install and launch

Install the wheel in a virtual environment of its own and launch it from outside the source tree:

```powershell
$Venv = "$env:USERPROFILE\jarvis-venv"
py -3.12 -m venv $Venv
& "$Venv\Scripts\python.exe" -m pip install .\dist\jarvis-0.1.0-py3-none-any.whl
Set-Location $env:USERPROFILE                    # anywhere outside the checkout
$env:JARVIS_BRAIN_MODEL = "opus"                 # the brain's model; without it, sonnet
& "$Venv\Scripts\python.exe" -m jarvis --host 127.0.0.1
```

- It serves the built page on loopback: open http://127.0.0.1:8340. Vite and certificates are not needed for this. Stop it with Ctrl+C in that window.
- An installed copy keeps its settings file at `$env:LOCALAPPDATA\JARVIS\.env` and its data in `$env:LOCALAPPDATA\JARVIS\data`. `JARVIS_DATA_DIR` and `JARVIS_ENV_FILE` choose others. For an isolated trial, create the folder and an empty settings file first, then set both in the same window before launching:

  ```powershell
  $env:JARVIS_DATA_DIR = "$env:USERPROFILE\jarvis-trial\data"
  $env:JARVIS_ENV_FILE = "$env:USERPROFILE\jarvis-trial\jarvis.env"
  ```

- The brain's model is `JARVIS_BRAIN_MODEL` (default `sonnet`; `opus` was used in testing). Development runs use `JARVIS_RUN_MODEL` or the model the brain asks for.
- If `claude` is not on PATH in the window that starts JARVIS (a window opened before Claude Code was installed may not have it yet), set `$env:JARVIS_CLAUDE_PATH` to `claude.exe` for the brain. Development runs still look `claude` up on PATH, so keep it on PATH.
- Without `FISH_API_KEY`, replies are text only (tested). Fish and local Kokoro voice output were not tested on Windows.
- The data folder must be one only you can change. JARVIS refuses to keep its token in a folder other users can modify (for example a checkout directly under `C:\`), and refuses junctions or links on the way to it. A folder under your user profile is fine.

## Optional: the English speech model

With JARVIS running, from a second PowerShell window:

```powershell
$Url = "http://127.0.0.1:8340"
Invoke-RestMethod -Method Post "$Url/api/speech/models/en/install" -Headers @{ Origin = $Url }
```

Then check its status once; run this again until `installed` is `True` or the install `state` is `failed`:

```powershell
(Invoke-RestMethod "$Url/api/speech/models").models | Where-Object language -eq "en" | Select-Object installed, install
```

- This downloads `sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06` (57,267,600 bytes) from the sherpa-onnx release and checks its pinned SHA-256 before extracting it into `<data>\models`. Tested: installed in about 8 s.
- After the first install, stop JARVIS (Ctrl+C) and start it again on the same data: the running server keeps reporting the model as not installed until it restarts. Then `(Invoke-RestMethod "$Url/api/speech/models").state` reads `ready` (tested: about 5 s after launch).
- The installed app's message may tell you to run `.venv/bin/python scripts/setup_speech_input.py`. That is the macOS checkout command; use the route above instead.

## Stop, restart and roll back

- Stop with Ctrl+C in JARVIS's window. It removes its `server-address.json` and stops its brain and speech worker. Restart with the same command and the same variables.
- Before installing a newer wheel, stop JARVIS, keep the older `.whl`, and copy the data folder to a separate pre-upgrade folder that the newer version never runs on. A newer version may update the database, and an older version is not guaranteed to read it.

  ```powershell
  Copy-Item -Recurse "$env:LOCALAPPDATA\JARVIS\data" "$env:USERPROFILE\jarvis-data-pre-upgrade"
  ```

- To roll back, stop JARVIS, install the older wheel into a fresh venv, and start that against the pre-upgrade copy:

  ```powershell
  $Old = "$env:USERPROFILE\jarvis-venv-rollback"
  py -3.12 -m venv $Old
  & "$Old\Scripts\python.exe" -m pip install .\previous\jarvis-0.1.0-py3-none-any.whl
  $env:JARVIS_DATA_DIR = "$env:USERPROFILE\jarvis-data-pre-upgrade"
  & "$Old\Scripts\python.exe" -m jarvis --host 127.0.0.1
  ```

## What was tested

On a Windows 11 Pro virtual machine, under an elevated administrator account:

- Text: typed messages and replies on the loopback page (Edge) and over its socket, with the Claude brain on `opus`; a first start and a second start on the same data.
- One-shot work: the brain's `spawn_run` in a registered folder ran a development run on `opus`, including one that read an input file and wrote a receipt file there.
- Fixed PCM: with the English model installed through the route above, Windows' built-in voices synthesised known sentences as 16 kHz mono PCM, and JARVIS's recogniser returned every word, both directly and streamed through the real `/ws/transcribe` socket (decoding took about 0.04 of real time).
- Owner-only private files (the tool token and the brain's `mcp.json`) and the folder reader's refusal of links and aliases, by native tests.

## Not yet tested or pending

- A standard (non-administrator) user account, and a second user on the same machine.
- Physical audio: a real microphone, browser capture and permissions, voice output and playback. Chrome was not installed; Chrome on macOS remains the tested voice page.
- Packets: the page's 100 ms PCM packets from a live capture are unverified.
- Work packages: opening a local zip (or a received one) fails in Windows extraction, which stays unimplemented until the pending extraction proposal is approved.
- JarvisNet: team server sign-in, the Network view and messages between people.
- Codex as a brain, cancelling runs (process termination), steering other Claude Code sessions, and project paths longer than 260 characters (Windows long paths stay off).
