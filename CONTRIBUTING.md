# Contributing to JARVIS

Thanks for your interest in contributing! Here's how to get involved.

## Getting Started

1. Fork the repo
2. Clone your fork
3. Follow the setup instructions in the README
4. Make your changes
5. Run the relevant automated checks, then exercise the affected workflow. Voice changes need an actual listening and concurrent-load trial.
6. Submit a PR

## Development direction

Start with the [roadmap](docs/roadmap.md). The priorities are the React workspace, voice reliability under load, agent coordination and the successor Denice direction. Roadmap evaluations are not promises of a release or approval to change the current voice.

- Improve Conversation, agent threads, workflow decisions and their shared state in `frontend/src/`.
- Diagnose voice failures across capture, browser scheduling, transport, recognition and playback.
- Keep agent message delivery and replies understandable after reconnects and restarts.
- Evaluate provider changes with the actual voice loop and concurrent workload.

## Code Style

Python application modules live in `jarvis/`. The main interface uses React and TypeScript in `frontend/src/`. Keep changes focused and follow the implementation conventions in [CLAUDE.md](CLAUDE.md).

- Keep voice responses short (1-2 sentences max)
- Don't add dependencies unless necessary
- Test your changes by actually talking to JARVIS
- Keep the personality consistent — British butler, dry wit, economy of language

## What NOT to Do

- Don't add telemetry or analytics
- Don't send data to external services beyond the existing API calls (Fish Audio)
- Don't add features that modify or delete user data in the services somebody has connected
- Don't break the existing voice loop

## Checks

```bash
.venv/bin/python -m pytest
.venv/bin/python -m pytest -m page
npm --prefix frontend run build
```

Run the checks relevant to the change. The default test suite excludes page and live-browser tests. Page tests use headless Chromium. Run tests marked `browser` explicitly when live browser/network coverage is needed. See `pytest.ini` for the marker definitions.

For voice issues, distinguish model-only benchmarks from complete-system behavior. Include the selected model, hardware, workload, capture or playback stage and diagnostic record when available. Remove credentials and private conversation content before sharing reports.

## Reporting Issues

Open an issue with:
- What you expected to happen
- What actually happened
- Your OS and Python version
- Any error messages from the terminal

## Questions?

Open an issue or start a discussion. Keep it simple.
