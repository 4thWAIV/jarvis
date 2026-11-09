# Voice package action release decision

## Owner and authority

Sol owns package implementation on `feature/jarvis-groks-packages`, Work `work-afc13119d082`. Root owns human delivery, Work reporting and the live checkout. The implementation approval is `authorize-package-implementation` / `approve-and-build` in `data/packages/jarvis-groks-packages-approve-and-build-1.0.0-local658/results/decisions.json`. Root requested a concrete blocking-decision source while independent release work continues. This assignment is source-only. No package has been delivered by this worker.

## Audience and question

Tim reviewed the implementation presentation, according to root's report. He needs to decide whether an extra pause before a package action is acceptable. He does not need to choose a provider flag. An approval of the pause permits changing the approved request-check bound and retesting; it does not approve the completed voice flow, release, a message or a package answer.

## Primary evidence

- `../technical-spec.md`, section `followups-and-authorization`, contains: "For voice, use a separate bounded intent-classification pass through the existing brain provider, with a strict schema and a two-second deadline. A timeout leaves reading available and returns `intent_unavailable` for mutation."
- `jarvis/package_authority.py` uses a fresh, tool-free session and `asyncio.timeout(2)`. The persistent default and timeout have not changed.
- `../review/runtime-diagnostic.json` contains synthetic direct requests, parsed provider outputs and measured durations. The supported-model diagnostic ran outside the production gate, not as an authorization or message send. Sender question: 3.269 seconds. Discussion: 3.067 seconds. The classifier separates the implicit current package from message subject after the prompt clarification.
- `../release.md` records the default Anthropic authentication failure, the primary OpenAI model rejection, offered models, the two-second offered-default timeouts and the non-prompting display permission result.
- `../review/release-voice-tests.txt` records ordinary conversation, authority and socket regression results after removing unavailable-classifier notices from normal user text.
- `../review/release-browser-tests.txt` records the built frontend and actual observation/MCP paths after main reconciliation.
- `jarvis/transmission.py`, `jarvis/capture_service.py` and `jarvis/package_followups.py` retain separate capture read-back/approval and recipient/evidence binding.

## Origin findings and proposal

Observed: the configured `gpt-6-sol` is rejected by the account. Offered-model requests did not authorize an action within two seconds. Diagnostic `gpt-5.6-luna` responses beyond the production deadline parsed correctly in about three seconds. Reading remains available; package mutations are refused when the check expires.

Proposed: permit a brief wait for the check, with a five-second maximum selected from the measured three-second responses, and retest before release. Alternative: retain the current bound and hold voice-action release until a valid configuration meets it. The proposal is an amendment to the approved design, not an implemented setting. The author should challenge whether the evidence supports this recommendation or whether more latency measurement is necessary before asking Tim.

Unknown: production success rate within five seconds under normal load, successful spoken read-back and subsequent external response, and screen permission from the running server's launch environment. Root owns reconciliation of the unsupported persistent model; this package does not ask Tim to choose a technical model ID. These unknowns prevent claiming deployment readiness. They do not prevent asking whether the short pause may be implemented and tested.

## Source and authoring limitation

`content.json` and its two SVGs are an originating draft, with self-review only. They distinguish current refusal from proposed waiting and state what the answer authorizes. The options picture outcomes rather than claiming to be screenshots of the current UI. The production-guide source check is required; rendered fit, narration and vote behavior remain for the factory run.

The human-package-author skill says: "The originating agent prepares the handoff in [references/handoff.md](references/handoff.md) and launches a fresh second agent with this skill explicitly named." This worker's assignment says "No subagents." No fresh author was launched here, and no independent comprehension review is claimed. Root may assign an available fresh author this handoff and the installed `~/.codex/skills/human-package-author/SKILL.md` before delivery.

## Return and delivery

Decision key: `package-action-wait`. `allow-short-pause` permits a maximum five-second check followed by runtime retesting while retaining capture approval. `keep-current-limit` retains two seconds and holds voice-action release until a configuration meets that bound. The rest of authorized integration and PR checks continue independently.

Root reviews or independently reauthors the source, then uses the existing production command with its own session and `--work work-afc13119d082`. Link the resulting decision package on Work and wait only on this decision for the bound change. Use `docs/making-a-package.md` as the production guide. Do not change the deadline on an unanswered or merely signed-off package.
