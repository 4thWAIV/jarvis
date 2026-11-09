# Agent keys — design for approval

Work item `work-658350a8e1bd`, piece `design`. Prepared 8 November 2026 against
main at `2784484`. Nothing here is built yet.

## What Tim asked for

His words, as relayed:

> "Every agent creates its own key and submits it to JARVIS, so JARVIS can
> recognise that a package came from that agent. Then, when we onboard the
> agent, JARVIS assigns that key to it, so the agent has a key that JARVIS
> knows belongs to that agent. From that point, agents can be assigned rights
> for particular projects or activities. For now, give all agents the same
> capabilities they have under the current system by default; we can tighten
> those rights later. Inside Work items, we can create additional keys
> granting specific rights to work within that item, and those keys could be
> created as part of making the package."

## The answer in one paragraph

Each agent makes an Ed25519 key pair with the Mac's own `ssh-keygen`. The
private half stays in a 0600 file in that agent's own config home and never
leaves it. The agent submits the public half, JARVIS checks the agent's own
transcript to see which session submitted it, and Tim onboards it with one
click on the agent's box (or by voice). From then on the agent signs
everything it sends: each report request, each package and each message
file. JARVIS keeps only public keys, so it never holds or sends a secret for
an agent. Rights live in one table and are checked in one function, which
replaces the access-key categories. A new agent gets the categories agents
use today. `make_package --work <id>` makes an extra key pair limited to that
one Work item, revoked automatically when the item closes. This replaces
access keys and their labels, the Keychain copies, `reporters.py`'s label
lookup, the transcript proof for packages and messages, the `?session=`
claim, the decision stamp and `make_package`'s use of the tool token. It
cannot replace the browser's Origin gate, the brain's tool token or the team
server session.

## Where things stand today (main, `2784484`)

- `access_keys.py`: bearer keys `jarvis_key_…`, SHA-256 digests in
  `data/access-keys.json`, seven categories (`captures`, `agents`, `runs`,
  `workflows`, `work`, `packages`, `watch`), each a list of
  (method, path pattern). A key has a label; the label is who reports.
- `reporters.py`: a label is turned into an agent through
  `agent_directory.named(label)`; reports are recorded as `key:<label>`; a
  key "owns" the sessions of the agents its label names.
- `web_auth.admission`: Origin, then tool token, then access key.
- `data_paths.ensure_tool_token`: one 0600 file; the brain's channel, the
  update command, restart, `make_package`'s delivery and the decision stamp
  all use it.
- `decision_stamp.py`: HMAC over the package's file digests, keyed by the
  receiving JARVIS's tool token.
- `session_message_sender.py`: who wrote a message file, a needs board, or
  ran `make_package` is proven by finding the act in that session's own
  transcript (or its subagent's).
- `server._package_sender`: a package's `?session=` claim is proven by the
  access key's label or by the transcript showing `make_package` run on the
  source.
- Diagnostics and Planning keep their access keys in the Keychain
  (`jarvis-work-access-key`, `jarvis-workflows-access-key`) and pass them in
  `JARVIS_ACCESS_KEY`; the Keychain prompts come from there.

## 1. The agent's key

### Key pair or shared secret

| | Key pair (recommended) | Shared secret (today's access keys) |
|---|---|---|
| What JARVIS stores | The public key only | A digest, but the agent sends the secret itself on every request |
| What travels | Signatures, which expire in 60 s | The secret, in every `Authorization` header |
| A package proves its sender | Yes: the signature is inside the zip and checks anywhere the public key is known | No: a bearer key cannot sign a file; we prove by transcript instead |
| A leaked request or log line | Useless after 60 s | Usable until revoked |
| Same proof for requests, packages, message files, the decision check | Yes, one mechanism | No: four mechanisms today |
| Cost | One `ssh-keygen` call per signature or check (measured 0.13 s) | A hash |

Recommendation: **the key pair**. Tim's own aim is that JARVIS can
"recognise that a package came from that agent"; only a signature does that
from the package itself. It also means JARVIS never holds or carries an
agent's secret, and one mechanism replaces four.

### Where the crypto comes from

- Checked: JARVIS's environment has no `cryptography`, PyNaCl or similar
  (`.venv/bin/pip list`; `pyproject.toml` declares none). Python's `ssl`
  module cannot sign. The team server alone pins `cryptography==50.0.2`; that
  is a separate service and does not reach JARVIS.
- What fits with no new dependency: **`/usr/bin/ssh-keygen`**, which ships
  with macOS (here OpenSSH 10.3). `ssh-keygen -t ed25519` makes the pair and
  `ssh-keygen -Y sign` / `-Y verify` sign and check with a namespace, which
  stops a signature made for one purpose being reused for another. Tested in
  a scratch folder on this Mac: key made at mode 0600, signed, verified, no
  prompt, 0.13 s per verify. Ubuntu CI runners and Windows 10+ ship the same
  tool, so tests and the Windows backlog are covered.
- It is called through one seam, `agent_keys.RUNNER`, as `osascript` is
  through `bundle_readers/_quartz.py`; tests fake it, and one test marked
  `ssh_keygen` runs the real tool.
- If verifying through a subprocess ever proves too slow under load,
  `cryptography` (already vetted for the team server) would verify in
  process. That would be a new JARVIS dependency and needs Tim's yes; it is
  not needed for this design.

### Where the agent keeps its key

- `<config home>/jarvis/agent-keys/<session id>/id_ed25519` (0600) and
  `id_ed25519.pub`, folders 0700, created with `O_EXCL`/`O_NOFOLLOW` as the
  tool token is.
- `<config home>` is the agent's own provider home: the Claude Code config
  root its transcript is under (`CLAUDE_CONFIG_DIR`, else `~/.claude`), or
  the Codex home for a Codex thread. The command finds it from the session
  id, the way `session_watch` already does.
- Never the Keychain, never `ssh-agent`, no passphrase: nothing ever
  prompts. `ssh-keygen` is always run with `-f <file>`, so it reads the file
  and nothing else.
- `repo_read.sensitive_reason` gains `jarvis/agent-keys`, so no folder
  read, browse, attachment or bundle tool ever reads a key, even inside a
  registered folder.

## 2. Submit and onboard

### Submitting

The talk-to-jarvis skill tells a new agent to run, in its own session, two
commands:

```
python -m jarvis.agent_key init --session <your session id>
python -m jarvis.agent_key submit --session <your session id> --fingerprint <SHA256:… printed by init>
```

- `init` makes the pair (or reuses the one there) and prints its
  fingerprint and the exact `submit` line to run.
- `submit` posts the public key to `POST /api/agent-keys`, signed by that
  same key (proof that the submitter holds the private half). This one route
  is admitted with no Origin and no token, because a new agent has nothing
  else; it only ever records a pending submission. Pending submissions are
  capped at 50, oldest dropped.

### Proving which session submitted it

The existing transcript proof (`session_message_sender.proven_by`), made
stricter: the claimed session's own transcript must hold an **assistant
tool call** (not just any line) whose command is `jarvis.agent_key submit`
and contains both its session id and the fingerprint. Text another agent
manages to put into this transcript (a message, a file it reads) is not a
tool call the session made, so it cannot prove the claim.

- Checked on the session watcher's polls for up to ten minutes after the
  submission, since the transcript line is written after the command
  starts. Unproven after ten minutes, the submission is dropped.
- Only the claimed session is looked in (`only_claimed`), as today.
- A subagent's transcript does not count: a subagent never submits a key
  (see below).

### Onboarding: what Tim sees and does

- A proven submission puts **"New key"** with **Accept** on that agent's box
  in the Agents tab, and one silent attention item ("Planning has a key
  waiting"), counted in Updates waiting, never spoken.
- Accept is one click. The box then shows a small key mark; the agent's
  detail shows the fingerprint, when it was onboarded and its rights, with
  **Revoke**.
- By voice: `agent_keys` reads them, the acting `onboard_agent` and
  `revoke_agent_key` do what the buttons do, through the same functions
  (the page-parity rule).
- Whether Accept is its own button or is folded into activating the agent
  is Q1 below.
- If an access key's label names exactly this agent
  (`agent_directory.named`), onboarding also records that the agent was
  that label, so the decisions it asked under `key:<label>` stay its own
  (answers, withdrawal). Routine choice, no extra click.

### Renamed, restarted, subagents

- **Renamed**: no effect. The key is bound to the session id; the name
  shown is always looked up in the agent directory, as today.
- **Restarted with `--resume`**: same session id, same key file, still
  onboarded.
- **A fresh session** (new session id, even with the same name and folder):
  a new agent to JARVIS, as it is today in the agents table. It makes and
  submits its own key, and Tim accepts it with one click. Its old session's
  key stays until revoked or until that agent is dropped from the list
  (`AGENT_ENDED_KEEP_SEC`, 30 days), when the key is revoked with it.
- **Subagents**: covered by the parent's key. They run in the parent's
  session, and on one Mac under one user they can read its key file anyway,
  so a separate key would claim a separation that is not there. A subagent
  that sends something signs with the parent's key, and may name itself in
  a signed `worker` field, shown as the agent's own words in italics (as
  `--worker` is today). So a package from a subagent shows as "Planning",
  with "logo build agent" beside it, not as today's transcript-proven
  "Feynman (Planning)".
- **Codex threads**: the same, with the Codex home and the rollout as the
  transcript.

## 3. Rights

### One place

- `agent_rights.py` holds the right definitions (today's seven categories,
  moved there unchanged with their route patterns) and the one check,
  `allows(principal, method, path, query)`.
- The grants are rows in JARVIS's database (schema `agent_keys_schema`,
  through `run_store.migrate`): `agent_rights(principal, right, scope,
  granted_at, granted_by)`. A principal is an agent (`agent:<session id>`)
  or a work-item key (`workkey:<fingerprint>`). `scope` is `*` now;
  `project:<name>` and `work:<id>` later.
- `web_auth.admission` gains `BY_AGENT_KEY`: the signature checks out, the
  key is onboarded and not revoked, and `agent_rights.allows` says yes.
  Every route keeps its existing code; only how a caller is admitted and
  who it is changes.
- `access_keys.py`'s categories move into `agent_rights.py`, and the module
  goes once the cut-over is done (phase 6).

### Default rights

Every onboarded agent gets the categories agents use today, with scope `*`.
Which set that is, is Q2 below. Nothing an agent can do today stops working
on the day it moves.

### Later: projects and activities

- A grant's `scope` narrows a right: `work` on `project:jarvis` admits only
  reports whose Work item's source is in that project's folder
  (`work_api` already knows `projects_for_path`); `runs` on
  `project:jarvis` admits only runs started there.
- "Activities" are the rights themselves, split finer where needed (for
  example `work.report` and `work.start`).
- The agent's detail gets the list of its rights with add and remove, and
  the brain gets `set_agent_rights`. That screen is phase 7 and is not
  needed for anything above.

### Signed requests

- Header: `Authorization: JARVIS-Signature key="SHA256:…", created=<unix>,
  nonce="<16 random bytes>", body="<sha256 of body>", sig="<base64>"`.
- Signed text: namespace `jarvis-request@v1`, then method, path, query,
  Host, created, nonce, body digest.
- Refused if `created` is more than 60 s from now, or the nonce was seen in
  the last 120 s (kept in memory; the 60 s window covers a restart).
- The gate wraps the request's body stream and refuses it if its bytes do
  not match the signed digest, before the route acts on it.
- WebSocket handshakes (`watch`, `/ws/runs`) sign the same way with an empty
  body.
- `work_report.py`, `workflow_report.py` and `make_package` sign with the
  key for `--session` (or `JARVIS_AGENT_SESSION`). During migration they
  fall back to `JARVIS_ACCESS_KEY` when no key file exists.

### Who reported

`reporters.py` stops reading labels. `reporters.recorded` returns
`agent:<session id>` (or `workkey:<fingerprint>`), `shown` names it through
`agent_directory.names_by_session`, and `Reporter.owns(session)` is simply
"the session is the key's own". Old records keep `key:<label>` and are
still shown through the old label lookup, read only.

## 4. Work-item keys

- `make_package --work <id>` (and `python -m jarvis.agent_key grant --work
  <id>`, the same function) makes a fresh key pair for that item.
- The package carries the private half at `.jarvis/work-key` and, in its
  signature file, a grant signed by the sending agent's key: "this public
  key may report on Work item `<id>`".
- When JARVIS receives the package it checks that the sender is onboarded,
  holds `work`, and the item exists and is open. It then records the public
  key as principal `workkey:<fingerprint>` with `work` scoped to
  `work:<id>`: reporting events and pieces on that item, reading answers to
  that item's decisions, and sending packages with `--work <id>`. It cannot
  start new items or touch any other item.
- **Who holds it**: whoever works in the unpacked package. On this Mac
  that is the run of an agent package (`run_package_agent`, in the
  package's folder). `work_report.py` finds `./.jarvis/work-key` by itself
  when it has no session key.
- **Expiry and revocation**: revoked in the same transaction as the item's
  `closed` event, or when it moves to History as done. It also stops after
  30 days, and the item's owner can renew it. Tim sees it on the item's
  Overview ("1 item key, from Planning") with **Revoke**; by voice,
  `revoke_agent_key` takes it too.
- This is the one place a private key travels, inside a package. That is
  what Tim asked for ("created as part of making the package"), and it is
  bounded: one item, this JARVIS only, automatically dead at close. A
  package forwarded to a teammate carries a key their JARVIS does not know.
- Routine choice: the key is only made when the package is an agent
  package or Work package someone will work in. A decision or review
  package for Tim gets none, since he answers it in JARVIS.

## 5. What it replaces, and what it cannot

### Replaced

| Today | Becomes |
|---|---|
| Access keys (`access_keys.py`, `data/access-keys.json`) and their labels | Onboarded agent keys, and rights rows |
| `jarvis-work-access-key`, `jarvis-workflows-access-key` in the Keychain | A 0600 key file per agent; no prompts |
| `reporters.py` label → agent lookup | The key's own agent |
| `?session=` claim and the transcript proof for packages (`_package_sender`) | The package's signature (`agent-signature.json` in the zip) |
| Transcript proof for message files and needs boards (`session_message_sender.proven_by` on writes) | `<file>.sig` written by `python -m jarvis.agent_key sign <file>`; unsigned files follow Q3 |
| Decision stamp (`decision-check.json`, HMAC with the tool token) | A signed statement in `agent-signature.json`: "these files passed decision check v<n>". The isolated check JARVIS is seeded with the agent's public key, so the same signature works there. |
| `make_package` reading the tool token to deliver and stamp | A signed request with the `packages` right |

The transcript proof stays for one job: proving which session submitted a
key.

On the decision stamp: today anything that can read the tool token can
stamp; afterwards anything that can read an agent's key can sign. That is
the same strength, and the signature also says which agent vouched for the
check. So it is replaced.

### Not replaced, and why

- **The browser's Origin gate.** The browser is Tim himself, and a web page
  cannot forge JARVIS's Origin. Giving the browser a key would mean storing
  a secret in it for no gain.
- **The brain's tool token.** The brain is JARVIS's own child process,
  speaking for Tim with every right, and the update command and restart act
  as Tim. A key pair there proves nothing the file does not already prove.
  After this design the token has two jobs (the brain's tools; update and
  restart) instead of four, and no agent has a reason to read it.
- **The team server session.** It proves Tim to a remote service through
  GitHub, a different trust domain; agent keys are local to this JARVIS.
  Teammates' agents' keys being published through the team server is a
  later idea, not part of this.
- Not touched: the staging test key, the knowledge engine token, the Fish
  key and `.env`.

### How strong this is on one Mac under one user

Every agent runs as Tim. 0600 stops other macOS users, not other agents:
any agent can read any other agent's key file, just as it can read the tool
token today. So, plainly:

- A signature proves which agent sent something **only against agents that
  keep to their own key**. An agent that is broken, or told by injected text
  to read another's key folder, can sign as that agent.
- What it does give: an honest agent can never be mistaken for another, by
  accident or by a stale label; nothing secret goes over HTTP or into logs;
  a stolen request is useless after 60 s; and each agent can be revoked on
  its own.
- Today's proofs are no stronger: the tool token, the Keychain copies (once
  any agent's program is allowed, every agent using it can read them) and
  transcripts (readable by every agent) are all within any agent's reach.
- To make it strong needs the operating system's help, each of which is a
  later choice: Claude Code's sandbox with other agents' key folders on its
  deny-read list; a separate macOS user per agent; or keys in the Secure
  Enclave, which cannot be copied but bring back Touch ID or Keychain
  prompts.

## 6. Migration with no downtime

Each step adds the new way beside the old; the old way is removed only once
the new one is seen working.

1. **Tool token.** Unchanged throughout. In phase 4 `make_package` stops
   reading it; nothing else changes.
2. **Access keys and labels.** From phase 3 the gate admits both. Each
   agent onboards and its report commands sign from then on. Diagnostics
   counts access-key admissions over the last 7 days; at zero, the keys are
   revoked (`access_keys revoke`) and the module removed in phase 6. Old
   `key:<label>` records stay readable.
3. **Keychain copies.** Unused once their agents sign. After the keys are
   revoked they are dead; Tim may delete the two items in Keychain Access,
   or leave them. No agent reads the Keychain again.
4. **Decision stamp.** In phase 4 `make_package` writes both the stamp and
   the signed statement, and JARVIS accepts either. In phase 6 the stamp is
   no longer written or accepted.
5. **Transcript sender proof.** In phase 4 a valid signature is checked
   first and the transcript proof is the fallback. In phase 6 the fallback
   goes for packages and messages (per Q3), and stays only for key
   submission.

## 7. Phases

Sizes: S is up to a day, M two to four days, L a week.

| # | Phase | Size | What is in it |
|---|---|---|---|
| 1 | Key core | S | `agent_keys.py` (the `ssh-keygen` seam, sign, verify, key files), `python -m jarvis.agent_key init/submit/sign`, the schema, `sensitive_reason` entry, tests with the fake seam plus one real one |
| 2 | Submit and onboard | M | `POST /api/agent-keys`, the stricter transcript proof on the watcher's poll, attention item, Accept and Revoke on the agent's box and detail, `agent_keys` / `onboard_agent` / `revoke_agent_key`, the skill's new section |
| 3 | Signed requests and rights | M | `BY_AGENT_KEY` in the gate, body digest check, nonce cache, `agent_rights.py` with the seven categories moved in, default grants, `reporters.py` on principals, report commands sign, access keys still accepted |
| 4 | Signed packages and messages | M | `agent-signature.json` written by `make_package`, inbox sender from the signature, the signed decision-check statement, check JARVIS seeded with the public key, `<file>.sig` for message files and needs boards, fallbacks kept |
| 5 | Work-item keys | M | grant in the package, `workkey:` principals scoped to one item, `work_report.py` finding `.jarvis/work-key`, revoke at close and at 30 days, the Overview line with Revoke |
| 6 | Cut-over | S | revoke access keys, remove `access_keys.py`, the stamp, `make_package`'s token read and the transcript fallback for packages and messages; `CLAUDE.md`, skill and docs updated |
| 7 | Scoped rights (later) | M | `project:` scopes, finer activities, the rights list on the agent's detail, `set_agent_rights` |

Phases 1 to 6 are about three weeks of work; 7 waits until Tim wants to
tighten rights.

## Routine choices made (one line each)

- Ed25519 through macOS's `ssh-keygen`, behind one seam; no new dependency.
- Key folder `<config home>/jarvis/agent-keys/<session id>/`, 0700, key 0600, no passphrase, never the Keychain or `ssh-agent`.
- Signature namespaces: `jarvis-request@v1`, `jarvis-package@v1`, `jarvis-message@v1`, `jarvis-key-submit@v1`.
- Request signatures live 60 s; nonces are remembered 120 s in memory.
- The submission route is the one route open with no credential; it records only a pending row, capped at 50.
- Submission proof needs an assistant tool call in the claimed session's own transcript naming both the session and the fingerprint; ten-minute window.
- One key per session; a fresh session is a new agent with a new key.
- Subagents sign with the parent's key and may name themselves in a signed `worker` field.
- An access-key label that names exactly the onboarded agent is adopted, so its earlier decisions stay its own.
- Agent keys are revoked when their agent is dropped from the list (30 days after it ends).
- Rights live in one table and one function, `agent_rights.allows`; categories keep their names and routes.
- Work-item keys only for packages someone will work in, never for packages Tim answers.
- Work-item keys end at the item's close or done, else after 30 days, renewable by the issuing agent.
- `jarvis/agent-keys` joins the sensitive-path rule.
- Old `key:<label>` records stay readable; nothing recorded is rewritten.
- The decision stamp is replaced by a signed statement, accepted side by side until the cut-over.
- The tool token stays for the brain, update and restart only.

## Questions for Tim

**Q0. Approve the design?** Agents make their own key pair with the Mac's
built-in `ssh-keygen` (no new dependency), JARVIS keeps only public keys,
and everything an agent sends is signed.
- Yes, build phases 1 to 6.
- Yes, but stop after phase 3 and show me.
- No; say what to change.

**Q1. How does he onboard an agent?**
- A: A separate **Accept** on the agent's box when it has a key waiting.
  One click per agent, and switching an agent on stays a separate act.
  (Recommended: onboarding and switching on mean different things.)
- B: Switching the agent on also accepts its waiting key. No extra click,
  but every agent he switches on is onboarded, whether or not he meant to.

**Q2. Which rights does a newly onboarded agent get?**
- A: What agents use today: `work` and `workflows` (their access keys) and
  `packages` (which `make_package` reaches now through the tool token).
  (Recommended: nothing works differently, and nothing new opens.)
- B: All seven categories, adding `captures`, `agents`, `runs` and `watch`
  (accept or send captures, rename or switch agents, start runs, watch the
  sessions feed). Today these are open to an agent only through an access
  key he makes for it.

**Q3. After the cut-over, what happens to a package or message from an
agent he has not onboarded?**
- A: Held, with "Planning has no accepted key" on the agent's box and
  Accept beside it; delivered as soon as he accepts.
  (Recommended: matches "a key that JARVIS knows belongs to that agent".)
- B: Delivered, marked "not verified", with replies going nowhere until he
  accepts.

**Q4. The two old Keychain items once the access keys are revoked?**
- A: He deletes `jarvis-work-access-key` and `jarvis-workflows-access-key`
  in Keychain Access himself. (Recommended; no agent touches the Keychain.)
- B: Leave them; they open nothing once revoked.

## Tim's answers

- Q0: build all six phases.
- Q1: "switch": switching an agent on (its activation on the Agents tab, or `set_agent_active` by voice) also accepts its waiting key; no separate Accept. The onboarding is recorded as his act, with the time and where (`page` or `voice`).
- Q2: "plus-watch": a new agent gets `work`, `workflows`, `packages` and `watch` (`agent_rights.DEFAULT_RIGHTS`, one line).
- Q3: "yes": anything signed by a key JARVIS does not know as an onboarded agent's is held, the Work item it is for goes to Error with the reason, and JARVIS makes Tim an error package, delivered unshown to his inbox, saying which session or key tried and what it tried to send, offering two acts: switch that agent on (accepting its key) or refuse and revoke the key. One function (phase 4).
- Q4: leave the two old Keychain items alone; nothing touches the Keychain.

## Progress

### Phases 1 to 3 (first pull request)

Built: `agent_keys.py` (the ssh-keygen seam `RUNNER`, key files, sign, verify, the request header), `agent_key.py` (`init`, `submit`, `sign`, `status`), `agent_key_store.py` (schema `agent_keys_schema`: keys, rights, events, access-key admission counts), `agent_rights.py` (the seven rights, the default, `allows`), `agent_auth.py` (checking a signed request), the gate's `BY_AGENT_KEY` in `web_auth.py`, `reporters.py` on principals, the report commands signing with `--as`, the proof loop, onboarding on the switch, the Agents tab's key mark and the thread's key line with Revoke, the brain's `agent_keys` and `revoke_agent_key`, and the skill's "Your own key" section.

Routine choices, one line each with the reason:

- No separate `onboard_agent` tool: `set_agent_active` onboards, because Tim chose the switch and the page and the brain must share one function.
- An active agent whose key waits shows its switch off, so the one click that switches it on is the click that accepts the key.
- A pending key is proven at once on submission as well as on every 5-second look, because Claude Code writes the tool call before the command runs.
- The proof looks only at the session's own transcript or rollout (`own_only`), never a subagent's, as the design says a subagent never submits.
- The proof needs the words `jarvis.agent_key`, `submit`, the session and the fingerprint inside one assistant tool call's input, so a reworded or wrapped command (PYTHONPATH, --cert) still proves it.
- The submit signature is over the namespace, the session and the public key only: replaying it only finds the same row.
- Message signatures are over the file's name and bytes, so a signed file cannot pass under another name.
- The signed request text is namespace, method, path, query, Host, created, nonce and body digest, one per line.
- WebSocket handshakes sign as `GET` with an empty body; their rights are judged as `WS`.
- The gate reads a signed request's whole body (at most 32 MB, above the 25 MB package limit) and checks its digest before the route runs.
- A signed read (GET) by a key JARVIS holds goes on even when it is not onboarded, so `agent_key status` can say where it stands; it is admitted as the agent only when onboarded.
- A revoked key's submission is refused; a new key needs a new `init` folder.
- Onboarding a new key for a session revokes its old one in the same transaction; rights are kept by principal, so they carry over.
- Default rights are granted only when the principal has none, so a later change of the default never overwrites rights Tim has set.
- Keys of agents dropped from the agents table (30 days after they end) are revoked on the 5-second look.
- Access-key admissions are counted per day and label in `legacy_admissions`, shown in `GET /api/agent-keys` and the brain's `agent_keys`, not as a new Diagnostics row, because a row needs its own coded words for a count that lives only until phase 6.
- An agent key admitted to `/api/capture/*` can never approve a message to a person (`web_auth.admitted_by_agent`), as access keys could not.
- Withdrawals and `answers` accept every name the agent went by (`reporters.aliases`), so decisions asked under an adopted access-key label stay its own.
- The report commands take `--as <session id>` rather than `--session`, because `workflow_report.py event` already has `--session` for `agent_attached`; they also read `JARVIS_AGENT_SESSION`, and `workflow_report.py event --session` is used when `--as` is not given.
- `agent-keys` joins `repo_read.SENSITIVE_DIRS`, and `work-key` and `work-key.pub` `SENSITIVE_EXACT`, so no folder tool reads a key.

### Phases 4 to 6 (second pull request)

Built: `package_signature.py` (`agent-signature.json`), message files' and needs boards' `<file>.sig` (`agent_key sign`), the inbox's sender from the signature, the decision check seeded with the agent's key and its signed statement, `server._unrecognised_key` (hold, Work item Error, error package with Tim's two acts), `key_error_package.py`, work-item keys (`make_package --work`, `agent_key grant` and `renew`, the Overview line with Revoke), and the cut-over: `access_keys.py`, the decision stamp, the tool token in `make_package` and the transcript proof for packages, messages and needs boards are gone.

Routine choices, one line each with the reason:

- The Work item "goes to Error" as a new state, `error`, ahead of everything but done, set and cleared only by JARVIS (`error_raised`, `error_cleared`), because no existing state means "JARVIS holds something sent for it" and a reporter must not be able to clear it.
- The Work item put in Error is the one the held thing was for (a package's `--work`, a report's item), since a hold for no item has none to mark.
- The error package is one per subject (`key:<fingerprint>`, else `session:<id>` for an unsigned one), made again (a newer version replacing the open one) as holds are added, so Tim sees one current list per sender rather than one package per attempt.
- The same thing held again (same subject, kind and words) is the same hold and changes nothing, so a retrying agent does not flood the inbox.
- The error package is a page package whose two buttons save `results/act.json` through the frame's existing bridge, so the acts are his clicks with no new bridge message; they are honoured only for error packages JARVIS made.
- "Switch it on" is offered only while the key waits for him; a pending or unknown key says the agent must run `init` and `submit` itself, because switching on can only accept a proven key.
- A request signed by a fingerprint JARVIS holds no key for is refused and not held, because nothing proves it is anyone's; one by a key it holds (pending, waiting, revoked) is held and told.
- A key JARVIS holds but has not onboarded may still reach `POST /api/packages/inbox`, so its package can be held rather than lost.
- A file signature that does not match the file (changed after signing) counts as unsigned, not forged, so an agent that rewrites a board just signs it again.
- An unsigned message file or needs board waits `SIG_GRACE_SEC` (60 s) for its `.sig` before it is held, because the agent signs just after writing.
- A package signature that does not check for a held key's public key is held; one naming a key JARVIS does not hold, that does not check even for the key it names, is treated as unsigned.
- An unsigned package on the tool token keeps its `?session=` as an unproven claim (his answers go nowhere), since the tool token is JARVIS's own and Tim's, not an agent's.
- A subagent is named "<subagent> (<agent>)" (Tim's standing rule: every sender identified, no backdoor) only from proof: its own transcript file shows it writing the message or board, or running make_package on that source; the parent's signature proves the parent. `--worker` was removed, since an agent's own words cannot name the sender; with no proof the parent alone is shown.
- Work-item keys are only made for agent packages (`runs: agent`) with `--work` and `--session`, as the design says packages Tim answers get none.
- A work-item key is live only while its item is open and not in History, checked at every request (`agent_auth`), as well as revoked when the item closes and on the 5-second look after it leaves; the read-time check makes the end immediate without joining the two stores' transactions.
- A work-item key reports for the agent that issued it (shown by that agent's name), and reads the answers to its item's decisions whoever asked them.
- Issuing and renewing a work-item key are part of the `work` right, so no new right is needed; a work-item key itself can do neither.
- Package inbox migration 5 clears every `reprove` flag, since nothing is proven from transcripts any more.
- `decision_stamp.py` keeps only the deck's marks and `is_decision_package`, which three modules use; the stamp itself is gone.
- `access-keys.json` is read only for the labels an onboarded agent adopts (`reporters.legacy_labels`); nothing reads it to admit anything, and nothing deletes it.
- The two Keychain items are left alone (Tim's answer to Q4).
- The cut-over guard (`legacy_access.py`): old access keys keep working, exactly as before, until none has been used for seven days (and use has been watched for at least seven days); then the cut-over is recorded and they admit nothing, for good. So merging cuts nobody off; the agents should still onboard first (the steps are in the PR).
- The report commands keep the `JARVIS_ACCESS_KEY` fallback while an agent has no key of its own, so an agent that has not onboarded yet keeps reporting through the guard's week.

## Cut-over steps (before and after merging the second pull request)

The access keys on record (labels only; the keys themselves are in the agents' environments and the two Keychain items, which nothing reads): `diagnostics-work` (work), `agent-guard` (work), `workflow-reporter`, `human-messaging-workflow` and `claude-workflows` (workflows). Diagnostics' `jarvis-work-access-key` and Planning's `jarvis-workflows-access-key` are among them.

1. **Merge #87 and bring the live JARVIS to it** (`.venv/bin/python -m jarvis.update`, which restarts it). From then on every access-key request is counted, and `POST /api/agent-keys` exists.
2. **Each agent that reports (Diagnostics, Planning, Agent Guard, and any other that holds one of the keys above) runs, itself, in its own session**, from the live checkout, with its own session id:

   ```
   cd /Users/timothystockstill/code/macos/4thWAIV/jarvis
   PYTHONPATH=$PWD .venv/bin/python -P -m jarvis.agent_key --cert cert.pem init --session <its session id>
   PYTHONPATH=$PWD .venv/bin/python -P -m jarvis.agent_key --cert cert.pem submit --session <its session id> --fingerprint <the SHA256:… init printed>
   ```

   It must run `submit` as its own tool call. Within ten minutes JARVIS finds that call in the session's own transcript, and the key waits.
3. **Tim, once per agent:** on the Agents tab, choose All and click the agent's switch. A box with a waiting key shows "New key" and its switch shows off, even when the agent is active. That one click switches the agent on and accepts its key, recorded as his, with the time. By voice it is "switch <agent> on".
4. **Each agent checks and moves over:** `… agent_key --cert cert.pem status --session <its session id>` says `onboarded`. From then on it gives `--as <its session id>` to every `work_report.py` and `workflow_report.py` call, and `--session <its session id>` to `make_package`. Asked "who still uses access keys?", JARVIS's `agent_keys` answers from the count, as does `GET /api/agent-keys`.
5. **Merge #101 and update.** Access keys keep working until none has been used for seven days (`legacy_access.py`). An agent that has not moved over is never cut off mid-week. Then they stop for good. Reinstall the skill so every session learns the new commands: `.venv/bin/python -m jarvis.session_skill install`.
6. **Nothing touches the Keychain.** The two old items open nothing once the cut-over is recorded, and Tim may leave them.
